"""
=================================================================
modules/medya_uretimi.py — Ses + Görsel + Video Pipeline
=================================================================
v11 koddaki şu yetenekler buraya taşındı:
  • edge-tts ile çok dilli MP3 üretimi
  • Pillow ile kapak resmi → 9:16 dikey kompozit arkaplan
  • moviepy 1.0.3 ile arkaplan + ses → MP4 (dikey video)

Tasarım notları:
  • Asenkron edge-tts'i senkron sarmalayıcı içinde çağırırız (Streamlit
    event-loop sorununu önlemek için).
  • Tüm dosya yolları config.FONT_YOLLARI ve config.LOGO_TAM_YOLU
    üzerinden — hardcoded macOS yolları yok.
  • Hata toleransı: bir adım başarısız olursa pipeline durmaz, log atılır
    ve sonraki dilin işine geçilir. Çağıran modül None gelirse hangi
    dilin patladığını görür.
=================================================================
"""
import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Dict, Callable, List, Any

from PIL import Image, ImageDraw, ImageFont, ImageFilter
import edge_tts

# moviepy 1.0.3 API'si — sadece ihtiyacımız olanlar
from moviepy.editor import ImageClip, AudioFileClip, VideoFileClip

from . import config
from . import kaynaklar
from . import pexels_motoru


# ============================================================
# FONT YÜKLEYİCİSİ
# ============================================================
def _font_yukle(boyut: int, kalin: bool = False) -> ImageFont.FreeTypeFont:
    """
    config.FONT_YOLLARI listesindeki yolları sırayla dener; ilk bulunan
    fontu yükler. Hiçbiri bulunmazsa Pillow'un default bitmap fontuna
    düşer (kalitesi düşük ama crash etmez).

    'kalin' True ise listede 'Bold' geçen yolları öne alır.
    """
    aday_yollar = list(config.FONT_YOLLARI)
    if kalin:
        # Bold içerenleri öne çek
        aday_yollar.sort(key=lambda y: ("Bold" not in y, "bold" not in y))

    for yol in aday_yollar:
        if Path(yol).exists():
            try:
                return ImageFont.truetype(yol, boyut)
            except Exception:
                continue
    # Hiçbir font yok — fallback
    return ImageFont.load_default()


# ============================================================
# LOGO YÜKLEYİCİSİ (Modül-seviyesi cache ile)
# ============================================================
# Logoyu her arkaplan render'ında baştan açıp işlemek savurganlıktır.
# Modül seviyesinde cache: (hedef_genislik, opacity) → işlenmiş RGBA logo.
_LOGO_CACHE: Dict[tuple, "Image.Image"] = {}


def _logo_yukle_ve_hazirla(
    hedef_genislik: int,
    opacity: float = 0.65,
    krem_esigi: int = 230,
) -> Optional["Image.Image"]:
    """
    Logoyu yükler, krem arkaplanını şeffaflaştırır, hedef genişliğe
    yeniden boyutlandırır ve istenen opacity'ye düşürür.

    Args:
        hedef_genislik: Logonun video üstündeki nihai piksel genişliği
                        (yükseklik en-boy oranı korunarak hesaplanır)
        opacity: 0.0–1.0 arası saydamlık (0.65 = %65 görünür/yarı şeffaf)
        krem_esigi: Bu eşiğin ÜZERİNDEKİ R+G+B/3 değerli pikseller
                    şeffaflaştırılır (logonun krem arkaplanını yok eder).
                    250 yaparsan sadece beyaz şeffaflanır; 230 daha
                    bağışlayıcı (krem-bej tonları da gider).

    Returns:
        RGBA Image, veya logo dosyası yoksa None.
    """
    # Cache anahtarı
    cache_anahtar = (hedef_genislik, round(opacity, 3), krem_esigi)
    if cache_anahtar in _LOGO_CACHE:
        return _LOGO_CACHE[cache_anahtar]

    if not config.LOGO_TAM_YOLU.exists():
        return None

    try:
        # 1) Yükle ve RGBA'ya çevir
        logo = Image.open(config.LOGO_TAM_YOLU).convert("RGBA")

        # 2) Krem arkaplanı şeffaflaştır
        # Her pikselin parlaklığına bakarak (R+G+B)/3 > eşik ise alpha=0 yap
        pikseller = logo.load()
        for y in range(logo.height):
            for x in range(logo.width):
                r, g, b, a = pikseller[x, y]
                parlaklik = (r + g + b) // 3
                if parlaklik > krem_esigi:
                    pikseller[x, y] = (r, g, b, 0)  # tamamen şeffaf
                else:
                    # Karanlık (logo çizgileri) — opacity uygula
                    yeni_alpha = int(a * opacity)
                    pikseller[x, y] = (r, g, b, yeni_alpha)

        # 3) Hedef genişliğe yeniden boyutlandır (aspect ratio korunur)
        oran = hedef_genislik / logo.width
        yeni_yukseklik = int(logo.height * oran)
        logo = logo.resize((hedef_genislik, yeni_yukseklik), Image.LANCZOS)

        # 4) Cache'le ve döndür
        _LOGO_CACHE[cache_anahtar] = logo
        return logo

    except Exception:
        return None


# ============================================================
# 1) EDGE-TTS — METİN → MP3
# ============================================================
async def _async_tts(metin: str, ses: str, cikti_yolu: Path) -> None:
    """edge-tts'in asenkron çağrısı — sadece bu fonksiyon async."""
    iletisim = edge_tts.Communicate(metin, ses)
    await iletisim.save(str(cikti_yolu))


# ============================================================
# TÜRKÇE TELAFFUZ DÜZELTİCİ — sadece TTS'e giden metin için
# Videodaki yazılar değişmez, yalnızca seslendirme metni değişir.
# ============================================================
_TELAFFUZ_DUZELTMELERI = [
    # Müzik türleri
    ("rock",        "rok"),
    ("Rock",        "Rok"),
    ("jazz",        "caz"),
    ("Jazz",        "Caz"),
    ("blues",       "bluz"),
    ("Blues",       "Bluz"),
    ("pop",         "pop"),
    ("hip hop",     "hip hop"),
    ("punk",        "pank"),
    ("Punk",        "Pank"),
    # Balkan isimleri / özel isimler
    ("Kusturica",   "Kusturitsa"),
    ("Kusturica'nın", "Kusturitsa'nın"),
    ("Kusturica'da",  "Kusturitsa'da"),
    ("Djokovic",    "Cokovic"),
    ("Djokovic'in", "Cokovic'in"),
    ("Šarić",       "Şariç"),
    ("Đoković",     "Cokovic"),
    ("Tito",        "Tito"),
    # Yaygın İngilizce kelimeler
    ("show",        "şov"),
    ("Show",        "Şov"),
    ("festival",    "festival"),
    ("online",      "onlayn"),
    ("Online",      "Onlayn"),
    ("trend",       "trend"),
    ("vibe",        "vayb"),
    ("Vibe",        "Vayb"),
    ("blog",        "blog"),
    ("podcast",     "podkast"),
    ("Podcast",     "Podkast"),
    ("beat",        "biyt"),
    ("Beat",        "Biyt"),
    # Sık karşılaşılan yabancı sözcükler
    ("UNESCO",      "Unesko"),
    ("YouTube",     "Yutub"),
    ("Instagram",   "İnstagram"),
    ("TikTok",      "Tiktok"),
    ("Facebook",    "Feysbuk"),
]


def _telaffuz_duzenle(metin: str) -> str:
    """
    TTS'e gönderilecek TR metninde yabancı kelimeleri
    Türkçe okunuşlarıyla değiştirir.
    Büyük/küçük harf duyarlı — listedeki her çift ayrı ele alınır.
    """
    for yanlis, dogru in _TELAFFUZ_DUZELTMELERI:
        metin = metin.replace(yanlis, dogru)
    return metin


def _piper_ses_uret(
    metin: str,
    cikti_yolu: Path,
    log_callback: Callable[[str], None] = print,
) -> bool:
    """
    Türkçe metin için Piper TTS ile WAV üretir, ardından ffmpeg ile MP3'e çevirir.
    Piper kurulu değilse veya model yoksa False döner (edge-tts'e fallback için).
    """
    import wave as wave_module

    model_yolu = Path(os.path.expanduser(
        "~/Desktop/Balkanlardan Kültür Sanat/piper_modeller/tr/tr_TR/dfki/medium/tr_TR-dfki-medium.onnx"
    ))

    if not model_yolu.exists():
        log_callback("      ⚠️  Piper modeli bulunamadı, edge-tts'e geçiliyor")
        return False

    try:
        from piper import PiperVoice
        from piper.config import SynthesisConfig
    except ImportError:
        log_callback("      ⚠️  Piper kurulu değil, edge-tts'e geçiliyor")
        return False

    gecici_wav = cikti_yolu.with_suffix(".tmp.wav")
    try:
        voice = PiperVoice.load(str(model_yolu))
        cfg = SynthesisConfig(length_scale=0.95, noise_scale=0.6, noise_w_scale=0.8)
        duzeltilmis_metin = _telaffuz_duzenle(metin)
        with wave_module.open(str(gecici_wav), "wb") as wf:
            voice.synthesize_wav(duzeltilmis_metin, wf, syn_config=cfg)

        if not gecici_wav.exists() or gecici_wav.stat().st_size < 1024:
            log_callback("      ⚠️  Piper WAV boş üretildi, edge-tts'e geçiliyor")
            return False

        # WAV → MP3 (ffmpeg)
        komut = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(gecici_wav),
            "-codec:a", "libmp3lame", "-q:a", "2",
            str(cikti_yolu),
        ]
        sonuc = subprocess.run(komut, capture_output=True, text=True, timeout=60)
        if sonuc.returncode != 0:
            log_callback(f"      ⚠️  Piper WAV→MP3 dönüşüm hatası: {sonuc.stderr[:100]}")
            return False

        return cikti_yolu.exists() and cikti_yolu.stat().st_size > 1024

    except Exception as e:
        log_callback(f"      ⚠️  Piper hatası ({e}), edge-tts'e geçiliyor")
        return False
    finally:
        if gecici_wav.exists():
            try:
                gecici_wav.unlink()
            except Exception:
                pass


def ses_uret(
    metin: str,
    dil_kodu: str,
    cikti_yolu: Path,
    log_callback: Callable[[str], None] = print,
) -> bool:
    """
    Verilen metni dil_kodu'na uygun sesle MP3'e dönüştürür.

    TR için önce Piper TTS denenir (daha kaliteli, yerel, ücretsiz).
    Piper başarısız olursa veya dil TR değilse edge-tts kullanılır.

    Args:
        metin: Seslendirilecek metin (genelde 'ozet' alanı kullanılır)
        dil_kodu: 'TR' / 'EN' / 'EL' / 'BG' vb.
        cikti_yolu: Kayıt yolu (klasör yoksa yaratılır)

    Returns:
        True başarılıysa, False değilse.
    """
    if not metin or not metin.strip():
        log_callback(f"      ⚠️  {dil_kodu}: ses üretmek için metin boş")
        return False

    cikti_yolu = Path(cikti_yolu)
    cikti_yolu.parent.mkdir(parents=True, exist_ok=True)

    # TR için önce Piper dene
    if dil_kodu == "TR":
        log_callback(f"      🎙️  TR: Piper TTS deneniyor")
        if _piper_ses_uret(metin, cikti_yolu, log_callback):
            log_callback(f"      ✅ TR: Piper TTS başarılı")
            return True
        log_callback(f"      ↩️  TR: Piper başarısız, edge-tts'e düşülüyor")

    # Diğer diller (ve TR fallback) için edge-tts
    ses_ad = config.DIL_AYARLARI.get(dil_kodu, {}).get("ses")
    if not ses_ad:
        log_callback(f"      ⚠️  {dil_kodu}: ses tanımı bulunamadı")
        return False

    try:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import nest_asyncio
                nest_asyncio.apply()
                loop.run_until_complete(_async_tts(metin, ses_ad, cikti_yolu))
            else:
                asyncio.run(_async_tts(metin, ses_ad, cikti_yolu))
        except RuntimeError:
            asyncio.run(_async_tts(metin, ses_ad, cikti_yolu))

        if cikti_yolu.exists() and cikti_yolu.stat().st_size > 1024:
            return True
        log_callback(f"      ⚠️  {dil_kodu}: ses dosyası üretildi ama çok küçük")
        return False

    except Exception as e:
        log_callback(f"      ❌ {dil_kodu} TTS hatası: {e}")
        if config.DEBUG:
            import traceback
            traceback.print_exc()
        return False


# ============================================================
# 2) PILLOW — 9:16 DİKEY ARKAPLAN
# ============================================================
def _resmi_kirp_ve_olcekle(
    kaynak_yolu: str,
    hedef_genislik: int,
    hedef_yukseklik: int,
) -> Optional[Image.Image]:
    """
    Bir kapak resmini cover-fit yöntemiyle hedef boyutlara getirir.
    Yani: en boy oranı korunur, taşan kenarlar simetrik kırpılır.
    """
    try:
        img = Image.open(kaynak_yolu).convert("RGB")
    except Exception:
        return None

    kaynak_oran = img.width / img.height
    hedef_oran = hedef_genislik / hedef_yukseklik

    if kaynak_oran > hedef_oran:
        # Kaynak daha geniş — yüksekliği eşitle, genişlikten kırp
        yeni_y = hedef_yukseklik
        yeni_x = int(yeni_y * kaynak_oran)
    else:
        # Kaynak daha dar — genişliği eşitle, yükseklikten kırp
        yeni_x = hedef_genislik
        yeni_y = int(yeni_x / kaynak_oran)

    img = img.resize((yeni_x, yeni_y), Image.LANCZOS)

    sol = (yeni_x - hedef_genislik) // 2
    ust = (yeni_y - hedef_yukseklik) // 2
    img = img.crop((sol, ust, sol + hedef_genislik, ust + hedef_yukseklik))
    return img


def arkaplan_hazirla(
    kapak_yolu: Optional[str],
    baslik: str,
    ulke_badge: str,
    cikti_yolu: Path,
    log_callback: Callable[[str], None] = print,
) -> Optional[Path]:
    """
    9:16 (1080×1920) dikey haber arkaplanı yaratır. Kapak resminin
    üstüne karanlık overlay + altın çerçeve + üst kısma kırmızı 'ülke
    badge'i + alt kısma haber başlığını yerleştirir.

    Kapak yoksa düz koyu arkaplana düşer.
    """
    W, H = config.W, config.H
    cikti_yolu = Path(cikti_yolu)
    cikti_yolu.parent.mkdir(parents=True, exist_ok=True)

    # --- Arkaplan: kapak veya düz renk ---
    if kapak_yolu and Path(kapak_yolu).exists():
        arkaplan = _resmi_kirp_ve_olcekle(kapak_yolu, W, H)
        if arkaplan is None:
            log_callback("      ⚠️  Kapak okunamadı, düz arkaplana düşülüyor")
            arkaplan = Image.new("RGB", (W, H), config.KOYU_ARKAPLAN)
    else:
        arkaplan = Image.new("RGB", (W, H), config.KOYU_ARKAPLAN)

    # --- Karanlık overlay (metin okunabilirliği için) ---
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    # Üst kısım — biraz koyu (badge için)
    od.rectangle([0, 0, W, 350], fill=(0, 0, 0, 140))
    # Alt kısım — başlık alanı, çok daha koyu
    od.rectangle([0, H - 700, W, H], fill=(0, 0, 0, 180))
    arkaplan = Image.alpha_composite(arkaplan.convert("RGBA"), overlay).convert("RGB")

    # --- Altın çerçeve ---
    d = ImageDraw.Draw(arkaplan)
    m = config.CERCEVE_MARGIN
    k = config.CERCEVE_KALINLIK
    d.rectangle([m, m, W - m, H - m], outline=config.ALTIN, width=k)

    # --- Kırmızı ülke badge (üst orta) ---
    badge_font = _font_yukle(60, kalin=True)
    # Pillow 10+'da textsize yerine textbbox
    try:
        bbox = d.textbbox((0, 0), ulke_badge, font=badge_font)
        bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except AttributeError:
        bw, bh = d.textsize(ulke_badge, font=badge_font)

    badge_padding_x, badge_padding_y = 50, 25
    badge_w = bw + badge_padding_x * 2
    badge_h = bh + badge_padding_y * 2
    badge_x = (W - badge_w) // 2
    badge_y = 150
    d.rounded_rectangle(
        [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
        radius=20, fill=config.KIRMIZI_BADGE,
    )
    d.text(
        (badge_x + badge_padding_x, badge_y + badge_padding_y - 5),
        ulke_badge, fill=config.BEYAZ, font=badge_font,
    )

    # --- Logo (sağ üst, watermark estetiği) ---
    # Spec:
    #   • Genişlik = video genişliğinin ~%17'si (1080×0.17 ≈ 184 px)
    #   • Margin   = video genişliğinin ~%5'i  (1080×0.05 = 54 px)
    #   • Opacity  = %65 (watermark hissi, baskın değil)
    #   • Krem JPEG arkaplanı şeffaflaştırılmış olarak gelir
    # Dosya yoksa graceful fail — render devam eder, sadece logo yok.
    logo_genisligi = int(W * 0.17)   # 184 px
    margin = int(W * 0.05)           # 54 px
    logo = _logo_yukle_ve_hazirla(hedef_genislik=logo_genisligi, opacity=0.65)
    if logo is not None:
        logo_x = W - logo.width - margin
        logo_y = margin
        # Mask olarak logo'nun kendi alpha kanalını veriyoruz —
        # bu hem şeffaflığı hem yumuşak kenarları korur.
        arkaplan.paste(logo, (logo_x, logo_y), logo)
    # else: logo dosyası yok → sessizce devam et (graceful failure)

    # --- Haber başlığı (alt, satır kaydırmalı) ---
    baslik_font = _font_yukle(64, kalin=True)
    baslik_yatay_pad = 90
    max_genislik = W - 2 * baslik_yatay_pad
    satirlar = _metni_satira_bol(baslik, baslik_font, max_genislik, d)
    if len(satirlar) > 6:
        satirlar = satirlar[:6]
        satirlar[-1] = satirlar[-1].rstrip(" ") + "…"

    satir_yuksekligi = 84
    toplam_h = len(satirlar) * satir_yuksekligi
    baslangic_y = H - 250 - toplam_h
    for i, satir in enumerate(satirlar):
        try:
            bbox = d.textbbox((0, 0), satir, font=baslik_font)
            sw = bbox[2] - bbox[0]
        except AttributeError:
            sw = d.textsize(satir, font=baslik_font)[0]
        sx = (W - sw) // 2
        sy = baslangic_y + i * satir_yuksekligi
        # Hafif gölge — okunabilirliği artırır
        d.text((sx + 2, sy + 2), satir, fill=(0, 0, 0), font=baslik_font)
        d.text((sx, sy), satir, fill=config.BEYAZ, font=baslik_font)

    # --- Alt watermark: Balkanlardan ---
    wm_font = _font_yukle(38)
    wm_text = "BALKANLARDAN"
    try:
        bbox = d.textbbox((0, 0), wm_text, font=wm_font)
        ww = bbox[2] - bbox[0]
    except AttributeError:
        ww = d.textsize(wm_text, font=wm_font)[0]
    d.text(((W - ww) // 2, H - 130), wm_text, fill=config.ALTIN, font=wm_font)

    arkaplan.save(cikti_yolu, "JPEG", quality=92)
    return cikti_yolu


def _metni_satira_bol(
    metin: str,
    font: ImageFont.FreeTypeFont,
    max_genislik: int,
    cizici: ImageDraw.ImageDraw,
) -> list:
    """Bir metni kelime kelime kontrol edip max_genislik'i aşmadan satırlara böler."""
    kelimeler = metin.split()
    if not kelimeler:
        return []
    satirlar = []
    mevcut = kelimeler[0]
    for kelime in kelimeler[1:]:
        deneme = mevcut + " " + kelime
        try:
            bbox = cizici.textbbox((0, 0), deneme, font=font)
            genislik = bbox[2] - bbox[0]
        except AttributeError:
            genislik = cizici.textsize(deneme, font=font)[0]
        if genislik <= max_genislik:
            mevcut = deneme
        else:
            satirlar.append(mevcut)
            mevcut = kelime
    satirlar.append(mevcut)
    return satirlar


# ============================================================
# 3) MOVIEPY — VİDEO MONTAJI
# ============================================================
def video_render(
    arkaplan_yolu: Path,
    ses_yolu: Path,
    cikti_yolu: Path,
    log_callback: Callable[[str], None] = print,
) -> Optional[Path]:
    """
    Arkaplan resmi + ses → 1080×1920 H.264 MP4 dikey video.

    Ses süresine eşit uzunlukta sabit görüntü. Eğer ses 60 saniyeden
    uzunsa Reels limiti aşılır; clipping yapmak yerine sadece logla
    (çağıran taraf isterse keser).
    """
    arkaplan_yolu = Path(arkaplan_yolu)
    ses_yolu = Path(ses_yolu)
    cikti_yolu = Path(cikti_yolu)
    cikti_yolu.parent.mkdir(parents=True, exist_ok=True)

    if not arkaplan_yolu.exists():
        log_callback(f"      ❌ Arkaplan yok: {arkaplan_yolu}")
        return None
    if not ses_yolu.exists():
        log_callback(f"      ❌ Ses yok: {ses_yolu}")
        return None

    try:
        ses = AudioFileClip(str(ses_yolu))
        sure = ses.duration

        if sure > 90:
            log_callback(f"      ⚠️  Ses çok uzun ({sure:.1f}s), IG Reels limiti 90s")

        # ImageClip — duration parametresi ses süresine bağlanır
        gorsel = (
            ImageClip(str(arkaplan_yolu))
            .set_duration(sure)
            .set_fps(config.FPS)
        )

        final = gorsel.set_audio(ses)

        # H.264 + AAC, mobil cihazlarda en uyumlu kombinasyon
        final.write_videofile(
            str(cikti_yolu),
            codec="libx264",
            audio_codec="aac",
            fps=config.FPS,
            preset="medium",
            threads=4,
            logger=None,  # moviepy'nin verbose çıktısını kapat
        )

        # Cleanup
        ses.close()
        gorsel.close()
        final.close()

        if cikti_yolu.exists() and cikti_yolu.stat().st_size > 10_000:
            return cikti_yolu
        log_callback("      ⚠️  Video üretildi ama çok küçük")
        return None

    except Exception as e:
        log_callback(f"      ❌ Video render hatası: {e}")
        if config.DEBUG:
            import traceback
            traceback.print_exc()
        return None



# ============================================================
# ============================================================
# v4 EKLEMELERİ — Hook + CTA Arkaplanları, 3-segmentli Pipeline
# ============================================================
# Aşağıdaki fonksiyonlar v4'te eklendi. Eski fonksiyonlar
# (arkaplan_hazirla, video_render) korundu — yeni fonksiyonlar
# onların yanına eklendi, üzerine yazılmadı.
# ============================================================
# ============================================================


# ============================================================
# YENİ: HOOK ARKAPLANI (0-3sn sahnesi)
# ============================================================
def hook_arkaplan_hazirla(
    kapak_yolu: Optional[str],
    hook_metni: str,
    ulke_badge: str,
    cikti_yolu: Path,
    log_callback: Callable[[str], None] = print,
) -> Optional[Path]:
    """
    Video'nun 0-3sn segmenti için kanca (hook) arkaplanı.

    Tasarım:
      • Kapak varsa: blur'lanmış + lacivert overlay (carousel slayt 1 estetiği)
      • Kapak yoksa: düz lacivert arkaplan
      • Üstte: kırmızı ülke badge (mevcut arkaplan_hazirla ile uyumlu)
      • Ortada: BÜYÜK hook metni, beyaz, gölgeli
      • Altta: "👇 Hikaye başlıyor" küçük ipucu

    Args:
        hook_metni: 3 sn'de okunabilecek scroll-stopper soru/cümle
        ulke_badge: "BOSNA" / "ΕΛΛΑΔΑ" gibi
    """
    W, H = config.W, config.H
    cikti_yolu = Path(cikti_yolu)
    cikti_yolu.parent.mkdir(parents=True, exist_ok=True)

    # --- ARKAPLAN: kapak blur+overlay VEYA düz lacivert ---
    if kapak_yolu and Path(kapak_yolu).exists():
        kapak = _resmi_kirp_ve_olcekle(kapak_yolu, W, H)
        if kapak is None:
            log_callback("      ⚠️  Hook: kapak okunamadı, düz arkaplana düşülüyor")
            arkaplan = Image.new("RGB", (W, H), config.KOYU_LACIVERT)
        else:
            # 8 piksel Gaussian blur — carousel slayt 1 ile aynı değer
            kapak_blur = kapak.filter(ImageFilter.GaussianBlur(radius=8))
            # Lacivert overlay %65 — metin okunaklılığı için
            lacivert_overlay = Image.new("RGB", (W, H), config.KOYU_LACIVERT)
            arkaplan = Image.blend(kapak_blur, lacivert_overlay, alpha=0.65)
    else:
        arkaplan = Image.new("RGB", (W, H), config.KOYU_LACIVERT)

    d = ImageDraw.Draw(arkaplan)

    # --- Altın çerçeve (mevcut özet sahnesi ile tutarlı) ---
    m = config.CERCEVE_MARGIN
    k = config.CERCEVE_KALINLIK
    d.rectangle([m, m, W - m, H - m], outline=config.ALTIN, width=k)

    # --- Kırmızı ülke badge (üst orta) ---
    badge_font = _font_yukle(60, kalin=True)
    try:
        bbox = d.textbbox((0, 0), ulke_badge, font=badge_font)
        bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except AttributeError:
        bw, bh = d.textsize(ulke_badge, font=badge_font)

    badge_padding_x, badge_padding_y = 50, 25
    badge_w = bw + badge_padding_x * 2
    badge_h = bh + badge_padding_y * 2
    badge_x = (W - badge_w) // 2
    badge_y = 200
    d.rounded_rectangle(
        [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
        radius=20, fill=config.KIRMIZI_BADGE,
    )
    d.text(
        (badge_x + badge_padding_x, badge_y + badge_padding_y - 5),
        ulke_badge, fill=config.BEYAZ, font=badge_font,
    )

    # --- HOOK METNİ — büyük, ortalanmış, beyaz, gölgeli ---
    # 80pt başlangıç, metin uzunsa otomatik küçültme
    hook_font_boyutu = 80
    hook_font = _font_yukle(hook_font_boyutu, kalin=True)
    yatay_pad = 90
    max_genislik = W - 2 * yatay_pad
    satirlar = _metni_satira_bol(hook_metni, hook_font, max_genislik, d)

    # 5 satırdan fazlaysa fontu küçült ve tekrar dene
    if len(satirlar) > 5:
        hook_font_boyutu = 64
        hook_font = _font_yukle(hook_font_boyutu, kalin=True)
        satirlar = _metni_satira_bol(hook_metni, hook_font, max_genislik, d)

    satir_yuksekligi = int(hook_font_boyutu * 1.25)
    toplam_h = len(satirlar) * satir_yuksekligi
    # Dikey ortalama (ekran ortası, hafif yukarı)
    baslangic_y = (H - toplam_h) // 2 - 50

    for i, satir in enumerate(satirlar):
        try:
            bbox = d.textbbox((0, 0), satir, font=hook_font)
            sw = bbox[2] - bbox[0]
        except AttributeError:
            sw = d.textsize(satir, font=hook_font)[0]
        sx = (W - sw) // 2
        sy = baslangic_y + i * satir_yuksekligi
        # Gölge — okunabilirliği artırır
        d.text((sx + 3, sy + 3), satir, fill=(0, 0, 0), font=hook_font)
        d.text((sx, sy), satir, fill=config.BEYAZ, font=hook_font)

    # --- "👇 Hikaye başlıyor" ipucu (alt orta) ---
    ipucu_font = _font_yukle(36)
    ipucu_text = "👇  Hikaye başlıyor"
    try:
        bbox = d.textbbox((0, 0), ipucu_text, font=ipucu_font)
        iw = bbox[2] - bbox[0]
    except AttributeError:
        iw = d.textsize(ipucu_text, font=ipucu_font)[0]
    d.text(((W - iw) // 2, H - 250), ipucu_text, fill=config.ALTIN, font=ipucu_font)

    # --- Logo (sağ üst, mevcut arkaplanla aynı yer) ---
    logo_genisligi = int(W * 0.17)
    margin = int(W * 0.05)
    logo = _logo_yukle_ve_hazirla(hedef_genislik=logo_genisligi, opacity=0.65)
    if logo is not None:
        arkaplan.paste(logo, (W - logo.width - margin, margin), logo)

    try:
        arkaplan.save(cikti_yolu, "JPEG", quality=92)
        return cikti_yolu
    except Exception as e:
        log_callback(f"      ❌ Hook arkaplan kaydedilemedi: {e}")
        return None


# ============================================================
# YENİ: CTA ARKAPLANI (35-40sn sahnesi)
# ============================================================
def cta_arkaplan_hazirla(
    cta_soru: str,
    cikti_yolu: Path,
    log_callback: Callable[[str], None] = print,
    ust_metin: str = "BİR DAKİKA...",
) -> Optional[Path]:
    """
    Video'nun son segmenti (35-40sn) için CTA arkaplanı.

    Tasarım: Lacivert arkaplan (temiz, dikkat dağıtmayan) — kapak resmi yok
             ki izleyici sadece CTA sorusuna odaklansın.
      • Altın çerçeve
      • Üst orta: hardal sarısı "BİR DAKİKA..." attention metni
      • Orta: CTA SORU, beyaz, büyük, ortalanmış
      • Alt orta: BÜYÜK logo + balkanlardan.com

    Args:
        cta_soru: Taslaktan gelen soru metni
        ust_metin: Attention çağıran üst metin (dile göre çevrilebilir)
    """
    W, H = config.W, config.H
    cikti_yolu = Path(cikti_yolu)
    cikti_yolu.parent.mkdir(parents=True, exist_ok=True)

    # Düz lacivert
    arkaplan = Image.new("RGB", (W, H), config.KOYU_LACIVERT)
    d = ImageDraw.Draw(arkaplan)

    # --- Altın çerçeve ---
    m = config.CERCEVE_MARGIN
    k = config.CERCEVE_KALINLIK
    d.rectangle([m, m, W - m, H - m], outline=config.ALTIN, width=k)

    # --- Üst attention metni: "BİR DAKİKA..." ---
    ust_font = _font_yukle(56, kalin=True)
    try:
        bbox = d.textbbox((0, 0), ust_metin, font=ust_font)
        uw = bbox[2] - bbox[0]
    except AttributeError:
        uw = d.textsize(ust_metin, font=ust_font)[0]
    d.text(((W - uw) // 2, 280), ust_metin, fill=config.ALTIN, font=ust_font)

    # --- CTA SORU — büyük, beyaz, ortalanmış ---
    soru_font_boyutu = 64
    soru_font = _font_yukle(soru_font_boyutu, kalin=True)
    yatay_pad = 100
    max_genislik = W - 2 * yatay_pad
    satirlar = _metni_satira_bol(cta_soru, soru_font, max_genislik, d)

    # Çok uzunsa fontu küçült
    if len(satirlar) > 6:
        soru_font_boyutu = 52
        soru_font = _font_yukle(soru_font_boyutu, kalin=True)
        satirlar = _metni_satira_bol(cta_soru, soru_font, max_genislik, d)

    satir_yuksekligi = int(soru_font_boyutu * 1.35)
    toplam_h = len(satirlar) * satir_yuksekligi
    # Dikey ortalama (logo'ya yer bırakarak biraz yukarı)
    baslangic_y = (H - toplam_h) // 2 - 100

    for i, satir in enumerate(satirlar):
        try:
            bbox = d.textbbox((0, 0), satir, font=soru_font)
            sw = bbox[2] - bbox[0]
        except AttributeError:
            sw = d.textsize(satir, font=soru_font)[0]
        sx = (W - sw) // 2
        sy = baslangic_y + i * satir_yuksekligi
        d.text((sx, sy), satir, fill=config.BEYAZ, font=soru_font)

    # --- BÜYÜK LOGO (alt orta) ---
    logo_genisligi = int(W * 0.40)  # 432 px — büyük, hatırlatıcı
    logo = _logo_yukle_ve_hazirla(hedef_genislik=logo_genisligi, opacity=0.95)
    if logo is not None:
        logo_x = (W - logo.width) // 2
        logo_y = H - logo.height - 280
        arkaplan.paste(logo, (logo_x, logo_y), logo)

    # --- balkanlardan.com (en altta hardal sarısı) ---
    site_font = _font_yukle(40, kalin=True)
    site_text = "balkanlardan.com"
    try:
        bbox = d.textbbox((0, 0), site_text, font=site_font)
        sw = bbox[2] - bbox[0]
    except AttributeError:
        sw = d.textsize(site_text, font=site_font)[0]
    d.text(((W - sw) // 2, H - 180), site_text, fill=config.ALTIN, font=site_font)

    try:
        arkaplan.save(cikti_yolu, "JPEG", quality=92)
        return cikti_yolu
    except Exception as e:
        log_callback(f"      ❌ CTA arkaplan kaydedilemedi: {e}")
        return None


# ============================================================
# YENİ: SES UZATICI — sessizlik ekleyerek min_sure'ye getir
# ============================================================
def _ses_sessizlik_ile_uzat(
    ses_yolu: Path,
    min_sure: float,
    log_callback: Callable[[str], None] = print,
) -> bool:
    """
    Bir MP3 dosyasının sonuna sessizlik ekleyerek minimum süreye uzatır.
    Eğer ses zaten yeterince uzunsa dokunmaz.

    ffmpeg apad filter ile yapılır:
        ffmpeg -i input.mp3 -af "apad=whole_dur=3.0" -y output.mp3
    """
    ses_yolu = Path(ses_yolu)
    if not ses_yolu.exists():
        log_callback(f"      ❌ Uzatılacak ses yok: {ses_yolu.name}")
        return False

    # Mevcut süreyi öğren
    try:
        ses = AudioFileClip(str(ses_yolu))
        mevcut_sure = ses.duration
        ses.close()
    except Exception as e:
        log_callback(f"      ⚠️  Ses süresi okunamadı ({e}), uzatma atlanıyor")
        return True  # graceful

    if mevcut_sure >= min_sure:
        return True  # zaten yeterli

    log_callback(f"      🔇 Ses {mevcut_sure:.1f}s → {min_sure:.1f}s sessizlikle uzatılıyor")

    gecici = ses_yolu.with_suffix(".tmp.mp3")
    try:
        komut = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(ses_yolu),
            "-af", f"apad=whole_dur={min_sure}",
            "-c:a", "libmp3lame", "-q:a", "2",
            str(gecici),
        ]
        sonuc = subprocess.run(komut, capture_output=True, text=True, timeout=60)
        if sonuc.returncode != 0:
            log_callback(f"      ❌ ffmpeg ses uzatma hatası: {sonuc.stderr[:200]}")
            if gecici.exists():
                gecici.unlink()
            return False
        gecici.replace(ses_yolu)
        return True
    except subprocess.TimeoutExpired:
        log_callback("      ❌ ffmpeg ses uzatma 60s'de bitmedi")
        if gecici.exists():
            gecici.unlink()
        return False
    except Exception as e:
        log_callback(f"      ❌ Ses uzatma hatası: {e}")
        if gecici.exists():
            gecici.unlink()
        return False


# ============================================================
# YENİ: VİDEO SEGMENTİ RENDER — min_sure desteği ile
# ============================================================
def video_segmenti_render(
    arkaplan_yolu: Path,
    ses_yolu: Path,
    cikti_yolu: Path,
    min_sure: Optional[float] = None,
    log_callback: Callable[[str], None] = print,
) -> Optional[Path]:
    """
    Mevcut video_render() ile aynı işi yapar AMA min_sure parametresi var.
    Eğer ses min_sure'den kısaysa, ses dosyasını sessizlikle uzatır.

    Args:
        min_sure: Hook için 3.0, CTA için 5.0, özet için None
    """
    arkaplan_yolu = Path(arkaplan_yolu)
    ses_yolu = Path(ses_yolu)
    cikti_yolu = Path(cikti_yolu)
    cikti_yolu.parent.mkdir(parents=True, exist_ok=True)

    if not arkaplan_yolu.exists():
        log_callback(f"      ❌ Arkaplan yok: {arkaplan_yolu.name}")
        return None
    if not ses_yolu.exists():
        log_callback(f"      ❌ Ses yok: {ses_yolu.name}")
        return None

    if min_sure is not None and min_sure > 0:
        if not _ses_sessizlik_ile_uzat(ses_yolu, min_sure, log_callback):
            log_callback("      ⚠️  Ses uzatılamadı, mevcut süreyle devam")

    try:
        ses = AudioFileClip(str(ses_yolu))
        sure = ses.duration

        if sure > 90:
            log_callback(f"      ⚠️  Segment çok uzun ({sure:.1f}s)")

        # Arkaplan video mu, resim mi? — uzantıdan anla
        uzanti = arkaplan_yolu.suffix.lower()
        if uzanti in {".mp4", ".mov", ".webm", ".mkv"}:
            # VİDEO ARKAPLAN — ses süresine göre kırp veya döngüye al
            kaynak_video = VideoFileClip(str(arkaplan_yolu))
            if kaynak_video.duration >= sure:
                # Video yeterince uzun → ses süresine kırp
                gorsel = kaynak_video.subclip(0, sure).set_fps(config.FPS)
            else:
                # Video kısa → ses süresine yetecek kadar döngüye al
                from moviepy.editor import concatenate_videoclips
                tekrar = int(sure / kaynak_video.duration) + 1
                parcalar = [kaynak_video] * tekrar
                birlesik = concatenate_videoclips(parcalar)
                gorsel = birlesik.subclip(0, sure).set_fps(config.FPS)
            # Video'nun kendi sesini at — segment'in kendi sesi olacak
            gorsel = gorsel.without_audio()
        else:
            # RESİM ARKAPLAN — eski davranış aynen korunur
            gorsel = (
                ImageClip(str(arkaplan_yolu))
                .set_duration(sure)
                .set_fps(config.FPS)
            )

        final = gorsel.set_audio(ses)

        final.write_videofile(
            str(cikti_yolu),
            codec="libx264",
            audio_codec="aac",
            fps=config.FPS,
            preset="medium",
            threads=4,
            logger=None,
        )

        ses.close()
        gorsel.close()
        final.close()

        if cikti_yolu.exists() and cikti_yolu.stat().st_size > 10_000:
            return cikti_yolu
        log_callback("      ⚠️  Video segmenti üretildi ama çok küçük")
        return None

    except Exception as e:
        log_callback(f"      ❌ Video segment render hatası: {e}")
        if config.DEBUG:
            import traceback
            traceback.print_exc()
        return None


# ============================================================
# YENİ: VİDEO BİRLEŞTİRİCİ — N segment → 1 MP4 (ffmpeg concat)
# ============================================================
def video_segmentlerini_birlestir(
    segmentler: List[Path],
    cikti_yolu: Path,
    log_callback: Callable[[str], None] = print,
) -> Optional[Path]:
    """
    N adet MP4'ü tek bir MP4'e birleştirir.
    Önce hızlı yol (concat demuxer + -c copy), başarısızsa
    güvenli yol (concat filter + re-encode).
    """
    cikti_yolu = Path(cikti_yolu)
    cikti_yolu.parent.mkdir(parents=True, exist_ok=True)

    var_olan = [p for p in segmentler if p and Path(p).exists()]
    if len(var_olan) == 0:
        log_callback("      ❌ Birleştirilecek hiçbir segment yok")
        return None
    if len(var_olan) == 1:
        log_callback("      ℹ️  Tek segment var, doğrudan kopyalanıyor")
        import shutil
        shutil.copy2(var_olan[0], cikti_yolu)
        return cikti_yolu

    log_callback(f"      🔗 {len(var_olan)} segment birleştiriliyor (hızlı yol)")
    if _concat_hizli_yol(var_olan, cikti_yolu, log_callback):
        if cikti_yolu.exists() and cikti_yolu.stat().st_size > 10_000:
            return cikti_yolu
        log_callback("      ⚠️  Hızlı yol çıktısı şüpheli, güvenli yola düşülüyor")

    log_callback("      🔗 Güvenli yol deneniyor (re-encode)")
    if _concat_guvenli_yol(var_olan, cikti_yolu, log_callback):
        if cikti_yolu.exists() and cikti_yolu.stat().st_size > 10_000:
            return cikti_yolu

    log_callback("      ❌ İki yol da başarısız")
    return None


def _concat_hizli_yol(
    segmentler: List[Path],
    cikti_yolu: Path,
    log_callback: Callable[[str], None],
) -> bool:
    """ffmpeg concat demuxer ile re-encode yapmadan birleştir."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        liste_yolu = Path(f.name)
        for seg in segmentler:
            mutlak = str(Path(seg).resolve()).replace("'", "'\\''")
            f.write(f"file '{mutlak}'\n")

    try:
        komut = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", str(liste_yolu),
            "-c", "copy",
            str(cikti_yolu),
        ]
        sonuc = subprocess.run(komut, capture_output=True, text=True, timeout=120)
        if sonuc.returncode == 0:
            return True
        log_callback(f"      ⚠️  Hızlı yol başarısız: {sonuc.stderr[:200]}")
        return False
    except subprocess.TimeoutExpired:
        log_callback("      ⚠️  Hızlı yol 120s'de bitmedi")
        return False
    except Exception as e:
        log_callback(f"      ⚠️  Hızlı yol hatası: {e}")
        return False
    finally:
        if liste_yolu.exists():
            liste_yolu.unlink()


def _concat_guvenli_yol(
    segmentler: List[Path],
    cikti_yolu: Path,
    log_callback: Callable[[str], None],
) -> bool:
    """ffmpeg concat filter ile re-encode ederek birleştir (yavaş ama garanti)."""
    n = len(segmentler)
    girdiler = "".join([f"[{i}:v][{i}:a]" for i in range(n)])
    filtre = f"{girdiler}concat=n={n}:v=1:a=1[v][a]"

    komut = ["ffmpeg", "-y", "-loglevel", "error"]
    for seg in segmentler:
        komut.extend(["-i", str(seg)])
    komut.extend([
        "-filter_complex", filtre,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium",
        "-c:a", "aac", "-b:a", "192k",
        str(cikti_yolu),
    ])

    try:
        sonuc = subprocess.run(komut, capture_output=True, text=True, timeout=300)
        if sonuc.returncode == 0:
            return True
        log_callback(f"      ❌ Güvenli yol başarısız: {sonuc.stderr[:300]}")
        return False
    except subprocess.TimeoutExpired:
        log_callback("      ❌ Güvenli yol 5 dakikada bitmedi")
        return False
    except Exception as e:
        log_callback(f"      ❌ Güvenli yol hatası: {e}")
        return False


# ============================================================
# YENİ: PEXELS ARKAPLAN VİDEOSU ÜRETİM PIPELINE'I
# ============================================================
def _pexels_videosunu_dikey_kirp(
    girdi_yolu: Path,
    cikti_yolu: Path,
    hedef_sure: float = 8.0,
    log_callback: Callable[[str], None] = print,
) -> Optional[Path]:
    """
    Bir Pexels videosunu 1080x1920 dikey formatına kırp ve trim et.

    Strateji:
      • Yatay video gelirse: ortayı dikey kes (overflow yana atılır)
      • Dikey ama farklı oran: en boyu koruyarak doldur (cover)
      • Süre fazlaysa: baştan trim et
      • Süre AZSA: videoyu DÖNGÜYE alarak hedef süreye ulaş (donma yok)

    ffmpeg subprocess kullanır (moviepy'dan hızlı + RAM dostu).
    """
    girdi_yolu = Path(girdi_yolu)
    cikti_yolu = Path(cikti_yolu)
    cikti_yolu.parent.mkdir(parents=True, exist_ok=True)

    if not girdi_yolu.exists():
        log_callback(f"      ❌ Pexels videosu yok: {girdi_yolu.name}")
        return None

    W, H = config.W, config.H  # 1080, 1920

    # Önce videonun gerçek süresini ffprobe ile öğren
    kaynak_sure = None
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(girdi_yolu)],
            capture_output=True, text=True, timeout=10,
        )
        if probe.returncode == 0:
            kaynak_sure = float(probe.stdout.strip())
    except Exception:
        pass

    # Eğer video hedef süreden kısaysa, döngü sayısını hesapla
    # -stream_loop ile videoyu N kere tekrarlat
    if kaynak_sure and kaynak_sure < hedef_sure:
        dongu_sayisi = int(hedef_sure / kaynak_sure) + 1
        log_callback(f"      🔁 Kaynak {kaynak_sure:.1f}s < hedef {hedef_sure:.1f}s, {dongu_sayisi}x döngü")
        loop_args = ["-stream_loop", str(dongu_sayisi - 1)]
    else:
        loop_args = []

    # ffmpeg filter: scale (cover) + crop (dikey ortayı al)
    vf = (
        f"scale='if(gt(a,{W}/{H}),-2,{W})':'if(gt(a,{W}/{H}),{H},-2)',"
        f"crop={W}:{H}"
    )

    komut = [
        "ffmpeg", "-y", "-loglevel", "error",
        *loop_args,
        "-i", str(girdi_yolu),
        "-t", str(hedef_sure),
        "-vf", vf,
        "-an",  # ses YOK — pipeline ses katacak
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-r", str(config.FPS),  # tüm sahneler aynı fps'e zorlanır
        str(cikti_yolu),
    ]
    try:
        sonuc = subprocess.run(komut, capture_output=True, text=True, timeout=120)
        if sonuc.returncode != 0:
            log_callback(f"      ⚠️  Pexels kırpma hatası: {sonuc.stderr[:200]}")
            return None
        if cikti_yolu.exists() and cikti_yolu.stat().st_size > 1024:
            return cikti_yolu
        return None
    except Exception as e:
        log_callback(f"      ⚠️  Pexels kırpma istisnası: {e}")
        return None


def _video_dosyalarini_birlestir(
    video_yollari: List[Path],
    cikti_yolu: Path,
    log_callback: Callable[[str], None] = print,
) -> Optional[Path]:
    """
    Birden fazla mp4'yi tek bir dosyaya birleştir (ffmpeg concat demuxer).
    """
    cikti_yolu = Path(cikti_yolu)
    cikti_yolu.parent.mkdir(parents=True, exist_ok=True)

    if not video_yollari:
        return None

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for v in video_yollari:
            f.write(f"file '{Path(v).absolute()}'\n")
        liste_yolu = f.name

    try:
        # NOT: Pexels videoları farklı codec/fps/format ile geliyor.
        # Stream copy ("c copy") çoğunlukla "başarılı" görünür ama oynatımda
        # donmalar/atlanan sahneler oluşur. Bu yüzden DOĞRUDAN re-encode'la
        # gidiyoruz — biraz yavaş ama tutarlı.
        komut = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", liste_yolu,
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-r", str(config.FPS),  # tüm sahneler aynı fps'e zorlanır
            str(cikti_yolu),
        ]
        sonuc = subprocess.run(komut, capture_output=True, text=True, timeout=180)
        if sonuc.returncode != 0:
            log_callback(f"      ❌ Re-encode birleştirme hatası: {sonuc.stderr[:300]}")
            return None

        if cikti_yolu.exists() and cikti_yolu.stat().st_size > 1024:
            return cikti_yolu
        return None
    finally:
        try:
            Path(liste_yolu).unlink()
        except Exception:
            pass


def pexels_arkaplan_videosu_olustur(
    tema: str,
    hedef_sure: float,
    cikti_yolu: Path,
    sahne_sayisi: int = 4,
    log_callback: Callable[[str], None] = print,
) -> Optional[Path]:
    """
    Bir tema için 4 Pexels videosu indirir, dikey kırpar, birleştirir.

    Args:
        tema: config.ICERIK_TEMALARI'ndan biri (örn 'muzik')
        hedef_sure: Çıktının toplam süresi (saniye) — özet ses süresi
        cikti_yolu: Nihai birleşik mp4'in yolu
        sahne_sayisi: Kaç sahne (default 4)

    Returns:
        Birleşik arkaplan videosunun Path'i, başarısızsa None.
    """
    cikti_yolu = Path(cikti_yolu)
    cikti_yolu.parent.mkdir(parents=True, exist_ok=True)

    terimler = kaynaklar.pexels_terimleri_sec(tema, sayi=sahne_sayisi)
    if not terimler:
        log_callback(f"      ⚠️  Pexels: '{tema}' için terim bulunamadı")
        return None

    log_callback(f"      🎬 Pexels arkaplan: tema='{tema}', {sahne_sayisi} sahne")
    for i, t in enumerate(terimler, 1):
        log_callback(f"        {i}. '{t}'")

    gecici_klasor = cikti_yolu.parent / f"_pexels_temp_{cikti_yolu.stem}"
    gecici_klasor.mkdir(parents=True, exist_ok=True)

    sahne_suresi = hedef_sure / sahne_sayisi
    log_callback(f"      ⏱️  Sahne başına {sahne_suresi:.1f}s, toplam hedef {hedef_sure:.1f}s")

    kirpilmis_sahneler: List[Path] = []
    for i, terim in enumerate(terimler, 1):
        ham_video = pexels_motoru.ara_ve_indir(
            sorgu=terim,
            hedef_klasor=gecici_klasor,
            dosya_adi=f"ham_{i:02d}.mp4",
            log_callback=log_callback,
        )
        if not ham_video:
            log_callback(f"      ⚠️  Sahne {i} indirilemedi, atlanıyor")
            continue

        kirpilmis = gecici_klasor / f"kirp_{i:02d}.mp4"
        sonuc = _pexels_videosunu_dikey_kirp(
            girdi_yolu=ham_video["yol"],
            cikti_yolu=kirpilmis,
            hedef_sure=sahne_suresi,
            log_callback=log_callback,
        )
        if sonuc:
            kirpilmis_sahneler.append(sonuc)

    if not kirpilmis_sahneler:
        log_callback("      ❌ Hiçbir Pexels sahnesi hazırlanamadı")
        try:
            import shutil
            shutil.rmtree(gecici_klasor, ignore_errors=True)
        except Exception:
            pass
        return None

    log_callback(f"      🔗 {len(kirpilmis_sahneler)} sahne birleştiriliyor")

    sonuc = _video_dosyalarini_birlestir(
        video_yollari=kirpilmis_sahneler,
        cikti_yolu=cikti_yolu,
        log_callback=log_callback,
    )

    try:
        import shutil
        shutil.rmtree(gecici_klasor, ignore_errors=True)
    except Exception:
        pass

    if sonuc:
        boyut_mb = cikti_yolu.stat().st_size / (1024 * 1024)
        log_callback(f"      ✅ Pexels arkaplan hazır: {cikti_yolu.name} ({boyut_mb:.1f}MB)")
        return sonuc
    log_callback("      ❌ Pexels arkaplan birleştirilemedi")
    return None


def kullanici_videosundan_arkaplan_olustur(
    kaynak_video_yollari: List[Path],
    hedef_sure: float,
    cikti_yolu: Path,
    log_callback: Callable[[str], None] = print,
) -> Optional[Path]:
    """
    Kullanıcının yüklediği 1 veya birden fazla video dosyasından
    1080x1920 dikey arkaplan üretir.

    pexels_arkaplan_videosu_olustur() ile aynı çıktıyı verir; tek fark
    indirme adımı yok, doğrudan yerel dosyaları kullanır. Böylece Hak
    Canva Pro'dan seçtiği kaliteli stok videoları sisteme verebiliyor.

    Args:
        kaynak_video_yollari: Yüklenmiş video dosyalarının yolları (1+)
        hedef_sure: Çıktının toplam süresi (saniye)
        cikti_yolu: Nihai birleşik mp4'in yolu

    Returns:
        Birleşik arkaplan videosunun Path'i, başarısızsa None.
    """
    cikti_yolu = Path(cikti_yolu)
    cikti_yolu.parent.mkdir(parents=True, exist_ok=True)

    # Sadece var olan dosyaları al
    gecerli_yollar = [Path(p) for p in kaynak_video_yollari if Path(p).exists()]
    if not gecerli_yollar:
        log_callback("      ❌ Kullanıcı videosu: hiçbir geçerli dosya bulunamadı")
        return None

    sahne_sayisi = len(gecerli_yollar)
    log_callback(f"      🎬 Kullanıcı arkaplan: {sahne_sayisi} dosya yüklendi")
    for i, p in enumerate(gecerli_yollar, 1):
        log_callback(f"        {i}. {p.name}")

    gecici_klasor = cikti_yolu.parent / f"_kullanici_temp_{cikti_yolu.stem}"
    gecici_klasor.mkdir(parents=True, exist_ok=True)

    sahne_suresi = hedef_sure / sahne_sayisi
    log_callback(f"      ⏱️  Sahne başına {sahne_suresi:.1f}s, toplam hedef {hedef_sure:.1f}s")

    kirpilmis_sahneler: List[Path] = []
    for i, kaynak_yol in enumerate(gecerli_yollar, 1):
        kirpilmis = gecici_klasor / f"kirp_{i:02d}.mp4"
        sonuc = _pexels_videosunu_dikey_kirp(
            girdi_yolu=kaynak_yol,
            cikti_yolu=kirpilmis,
            hedef_sure=sahne_suresi,
            log_callback=log_callback,
        )
        if sonuc:
            kirpilmis_sahneler.append(sonuc)
        else:
            log_callback(f"      ⚠️  Sahne {i} ({kaynak_yol.name}) hazırlanamadı, atlanıyor")

    if not kirpilmis_sahneler:
        log_callback("      ❌ Hiçbir kullanıcı sahnesi hazırlanamadı")
        try:
            import shutil
            shutil.rmtree(gecici_klasor, ignore_errors=True)
        except Exception:
            pass
        return None

    # Tek dosya varsa birleştirmeye gerek yok — direkt taşı
    if len(kirpilmis_sahneler) == 1:
        try:
            import shutil
            shutil.move(str(kirpilmis_sahneler[0]), str(cikti_yolu))
            shutil.rmtree(gecici_klasor, ignore_errors=True)
            boyut_mb = cikti_yolu.stat().st_size / (1024 * 1024)
            log_callback(f"      ✅ Kullanıcı arkaplan hazır: {cikti_yolu.name} ({boyut_mb:.1f}MB)")
            return cikti_yolu
        except Exception as e:
            log_callback(f"      ❌ Tek sahne taşıma hatası: {e}")
            return None

    log_callback(f"      🔗 {len(kirpilmis_sahneler)} sahne birleştiriliyor")

    sonuc = _video_dosyalarini_birlestir(
        video_yollari=kirpilmis_sahneler,
        cikti_yolu=cikti_yolu,
        log_callback=log_callback,
    )

    try:
        import shutil
        shutil.rmtree(gecici_klasor, ignore_errors=True)
    except Exception:
        pass

    if sonuc:
        boyut_mb = cikti_yolu.stat().st_size / (1024 * 1024)
        log_callback(f"      ✅ Kullanıcı arkaplan hazır: {cikti_yolu.name} ({boyut_mb:.1f}MB)")
        return sonuc
    log_callback("      ❌ Kullanıcı arkaplan birleştirilemedi")
    return None


def pexels_arkaplan_uzerine_yazi_ekle(
    girdi_video: Path,
    cikti_yolu: Path,
    baslik: str,
    badge_metni: str,
    log_callback: Callable[[str], None] = print,
) -> Optional[Path]:
    """
    Bir Pexels arkaplan videosunun üstüne dile özel başlık + ülke badge'i ekler.

    Yöntem:
        • PIL ile şeffaf overlay PNG üret (badge + başlık + alt karartma)
        • ffmpeg ile videoyla overlay birleştir
    """
    girdi_video = Path(girdi_video)
    cikti_yolu = Path(cikti_yolu)
    cikti_yolu.parent.mkdir(parents=True, exist_ok=True)

    if not girdi_video.exists():
        log_callback(f"      ❌ Pexels girdi yok: {girdi_video.name}")
        return None

    W, H = config.W, config.H

    overlay_yolu = cikti_yolu.parent / f"_overlay_{cikti_yolu.stem}.png"
    try:
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cizici = ImageDraw.Draw(overlay)

        # Alt yarıyı gradyan karart
        gradient_yuk = H // 2
        for i in range(gradient_yuk):
            alpha = int(180 * (i / gradient_yuk))
            y = H - gradient_yuk + i
            cizici.rectangle([0, y, W, y + 1], fill=(0, 0, 0, alpha))

        # Badge (üst orta)
        badge_font = _font_yukle(48, kalin=True)
        badge_padding = 28
        try:
            bbox = cizici.textbbox((0, 0), badge_metni, font=badge_font)
            b_w, b_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except Exception:
            b_w, b_h = len(badge_metni) * 28, 52
        b_kutu_w = b_w + badge_padding * 2
        b_kutu_h = b_h + badge_padding
        b_x = (W - b_kutu_w) // 2
        b_y = 120
        cizici.rectangle(
            [b_x, b_y, b_x + b_kutu_w, b_y + b_kutu_h],
            fill=config.SICAK_KIRMIZI + (230,),
        )
        cizici.text(
            (b_x + badge_padding, b_y + badge_padding // 3),
            badge_metni, font=badge_font, fill=config.YUMUSAK_BEYAZ,
        )

        # Başlık — alt orta (gölgeli, beyaz)
        baslik_font = _font_yukle(78, kalin=True)
        satirlar = _metni_satira_bol(baslik, baslik_font, W - 160, cizici)
        satirlar = satirlar[:4]
        satir_yuk = int(baslik_font.size * 1.25)
        toplam_yuk = len(satirlar) * satir_yuk
        baslangic_y = H - toplam_yuk - 240

        for i, satir in enumerate(satirlar):
            try:
                bbox = cizici.textbbox((0, 0), satir, font=baslik_font)
                s_w = bbox[2] - bbox[0]
            except Exception:
                s_w = len(satir) * 32
            s_x = (W - s_w) // 2
            s_y = baslangic_y + i * satir_yuk
            cizici.text((s_x + 3, s_y + 3), satir, font=baslik_font, fill=(0, 0, 0, 200))
            cizici.text((s_x, s_y), satir, font=baslik_font, fill=config.YUMUSAK_BEYAZ + (255,))

        # Logo (en alt)
        logo = _logo_yukle_ve_hazirla(hedef_genislik=160, opacity=0.85)
        if logo:
            overlay.paste(logo, ((W - logo.width) // 2, H - 140), logo)

        overlay.save(overlay_yolu, "PNG")
    except Exception as e:
        log_callback(f"      ❌ Overlay oluşturma hatası: {e}")
        return None

    # ffmpeg ile bindir
    try:
        komut = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(girdi_video),
            "-i", str(overlay_yolu),
            "-filter_complex", "[0:v][1:v]overlay=0:0",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-an",
            str(cikti_yolu),
        ]
        sonuc = subprocess.run(komut, capture_output=True, text=True, timeout=180)
        if sonuc.returncode != 0:
            log_callback(f"      ❌ Overlay bindirme hatası: {sonuc.stderr[:200]}")
            return None

        try:
            overlay_yolu.unlink()
        except Exception:
            pass

        if cikti_yolu.exists() and cikti_yolu.stat().st_size > 1024:
            return cikti_yolu
        return None
    except Exception as e:
        log_callback(f"      ❌ Overlay render istisnası: {e}")
        return None


# ============================================================
# YENİ: ÜST DÜZEY ORKESTRATÖR — Tek dil için 3-segmentli pipeline
# ============================================================
def dil_icin_video_uret(
    dil_kodu: str,
    hook: str,             # 0-3sn seslendirilecek scroll-stopper
    ozet: str,             # 3-35sn ana içerik
    cta_soru: str,         # 35-40sn izleyiciye soru
    baslik: str,           # özet sahnesinin arkaplanındaki büyük başlık
    badge_metni: str,      # "BOSNA" / "ΕΛΛΑΔΑ" — ülke etiketi
    kapak_yolu: Optional[str],
    cikti_klasoru: Path,
    dosya_on_eki: str,     # "BS_Kultur_03" gibi
    log_callback: Callable[[str], None] = print,
    ozet_arkaplan_videosu: Optional[Path] = None,  # YENİ: ortak Pexels mp4 — verilirse statik resim yerine kullanılır
) -> Dict[str, Any]:
    """
    Tek bir dil için tam 3-segmentli video pipeline'ı.

    Akış: ses → arkaplan → segment → birleştirme.
    Katı mod: herhangi bir adım eksik kalırsa nihai video=None,
    ara dosyalar diskte kalır.

    İmza değişikliği (v3 → v4):
      ESKİ: dil_icin_video_uret(dil_kodu, metin, baslik, ...)
      YENİ: dil_icin_video_uret(dil_kodu, hook, ozet, cta_soru, baslik, ...)
    Çağıran (yayin_motoru) güncellenmeli.

    Returns:
        {
          'video': Path|None,
          'sesler':      {'hook': P|N, 'ozet': P|N, 'cta': P|N},
          'arkaplanlar': {'hook': P|N, 'ozet': P|N, 'cta': P|N},
          'segmentler':  {'hook': P|N, 'ozet': P|N, 'cta': P|N},
        }
    """
    cikti_klasoru = Path(cikti_klasoru)
    cikti_klasoru.mkdir(parents=True, exist_ok=True)

    sonuc: Dict[str, Any] = {
        "video": None,
        "sesler":      {"hook": None, "ozet": None, "cta": None},
        "arkaplanlar": {"hook": None, "ozet": None, "cta": None},
        "segmentler":  {"hook": None, "ozet": None, "cta": None},
    }

    # Girdi doğrulama — boş içerikle uğraşma
    if not (hook and hook.strip()):
        log_callback(f"      ⚠️  {dil_kodu}: hook boş — video üretilmiyor")
        return sonuc
    if not (ozet and ozet.strip()):
        log_callback(f"      ⚠️  {dil_kodu}: ozet boş — video üretilmiyor")
        return sonuc
    if not (cta_soru and cta_soru.strip()):
        log_callback(f"      ⚠️  {dil_kodu}: cta_soru boş — video üretilmiyor")
        return sonuc

    # --- ADIM 1: 3 ses üret ---
    log_callback(f"      🎙️  {dil_kodu} sesler üretiliyor (hook + ozet + cta)")
    ses_metinleri = {"hook": hook, "ozet": ozet, "cta": cta_soru}
    for parca, metin in ses_metinleri.items():
        ses_yolu = cikti_klasoru / f"{dosya_on_eki}_{dil_kodu}_{parca}.mp3"
        if ses_uret(metin, dil_kodu, ses_yolu, log_callback):
            sonuc["sesler"][parca] = ses_yolu

    if not all(sonuc["sesler"].values()):
        eksik = [k for k, v in sonuc["sesler"].items() if not v]
        log_callback(f"      ❌ {dil_kodu}: eksik ses parçaları {eksik} — video yok")
        return sonuc

    # --- ADIM 2: 3 arkaplan hazırla ---
    log_callback(f"      🖼️  {dil_kodu} arkaplanlar hazırlanıyor")

    hook_ark_yolu = cikti_klasoru / f"{dosya_on_eki}_{dil_kodu}_hook_arkaplan.jpg"
    h = hook_arkaplan_hazirla(kapak_yolu, hook, badge_metni, hook_ark_yolu, log_callback)
    if h:
        sonuc["arkaplanlar"]["hook"] = h

    ozet_ark_yolu = cikti_klasoru / f"{dosya_on_eki}_{dil_kodu}_ozet_arkaplan.jpg"
    o = None

    # YENİ: Pexels arkaplan videosu verildiyse, onun üstüne dile özel yazı bindir
    if ozet_arkaplan_videosu and Path(ozet_arkaplan_videosu).exists():
        log_callback(f"      🎬 {dil_kodu}: Pexels arkaplan kullanılıyor (+ overlay)")
        pexels_overlay_yolu = cikti_klasoru / f"{dosya_on_eki}_{dil_kodu}_ozet_arkaplan.mp4"
        o_video = pexels_arkaplan_uzerine_yazi_ekle(
            girdi_video=Path(ozet_arkaplan_videosu),
            cikti_yolu=pexels_overlay_yolu,
            baslik=baslik,
            badge_metni=badge_metni,
            log_callback=log_callback,
        )
        if o_video:
            o = o_video
        else:
            log_callback(f"      ⚠️  {dil_kodu}: Pexels overlay başarısız, statik arkaplana düşülüyor")

    # FALLBACK: Pexels yoksa veya overlay başarısızsa eski statik arkaplan
    if not o:
        o = arkaplan_hazirla(kapak_yolu, baslik, badge_metni, ozet_ark_yolu, log_callback)
    if o:
        sonuc["arkaplanlar"]["ozet"] = o

    cta_ark_yolu = cikti_klasoru / f"{dosya_on_eki}_{dil_kodu}_cta_arkaplan.jpg"
    c = cta_arkaplan_hazirla(cta_soru, cta_ark_yolu, log_callback)
    if c:
        sonuc["arkaplanlar"]["cta"] = c

    if not all(sonuc["arkaplanlar"].values()):
        eksik = [k for k, v in sonuc["arkaplanlar"].items() if not v]
        log_callback(f"      ❌ {dil_kodu}: eksik arkaplan {eksik} — video yok")
        return sonuc

    # --- ADIM 3: 3 video segmenti render et ---
    log_callback(f"      🎬 {dil_kodu} video segmentleri render ediliyor")
    min_sureler = {"hook": 3.0, "ozet": None, "cta": 5.0}

    for parca in ["hook", "ozet", "cta"]:
        seg_yolu = cikti_klasoru / f"{dosya_on_eki}_{dil_kodu}_seg_{parca}.mp4"
        v = video_segmenti_render(
            arkaplan_yolu=sonuc["arkaplanlar"][parca],
            ses_yolu=sonuc["sesler"][parca],
            cikti_yolu=seg_yolu,
            min_sure=min_sureler[parca],
            log_callback=log_callback,
        )
        if v:
            sonuc["segmentler"][parca] = v

    if not all(sonuc["segmentler"].values()):
        eksik = [k for k, v in sonuc["segmentler"].items() if not v]
        log_callback(f"      ❌ {dil_kodu}: eksik segment {eksik} — birleştirme atlandı")
        return sonuc

    # --- ADIM 4: 3 segmenti birleştir ---
    log_callback(f"      🔗 {dil_kodu} segmentler birleştiriliyor")
    nihai_yolu = cikti_klasoru / f"{dosya_on_eki}_{dil_kodu}.mp4"
    sira = [sonuc["segmentler"]["hook"],
            sonuc["segmentler"]["ozet"],
            sonuc["segmentler"]["cta"]]

    b = video_segmentlerini_birlestir(sira, nihai_yolu, log_callback)
    if b:
        sonuc["video"] = b
        log_callback(f"      ✅ {dil_kodu} video hazır: {nihai_yolu.name}")
    else:
        log_callback(f"      ❌ {dil_kodu} birleştirme başarısız")

    return sonuc
