"""
=================================================================
modules/carousel_uretimi.py — Instagram Carousel Slayt Üretici (v4)
=================================================================
Yeni format. Mevcut medya_uretimi.py video üretir; bu modül 
5'li Instagram carousel kartları (1080×1350) üretir.

Carousel mantığı:
  • Slayt 1: HOOK — kapak resmi + ülke badge + scroll-stopper
  • Slayt 2-4: Yapı taşları — sade arkaplan + minimal ikon + başlık
  • Slayt 5: CTA — "Save this post 📌" + soru + logo

Renkler config.py'deki "küratör paleti"nden gelir.
=================================================================
"""
from pathlib import Path
from typing import List, Optional, Dict, Callable

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from . import config


# ============================================================
# FONT — medya_uretimi'ndeki ile uyumlu
# ============================================================
def _font_yukle(boyut: int, kalin: bool = False) -> ImageFont.FreeTypeFont:
    """config.FONT_YOLLARI'ndan ilk var olanı yükle."""
    yollar = list(config.FONT_YOLLARI)
    if kalin:
        yollar.sort(key=lambda y: ("Bold" not in y, "bold" not in y))
    for y in yollar:
        if Path(y).exists():
            try:
                return ImageFont.truetype(y, boyut)
            except Exception:
                continue
    return ImageFont.load_default()


def _metin_kutulu_sar(
    cizici: ImageDraw.ImageDraw,
    metin: str,
    font: ImageFont.FreeTypeFont,
    max_genislik: int,
) -> List[str]:
    """Metni piksel genişliğine göre kelime-kelime satırlara böler."""
    if not metin:
        return []
    kelimeler = metin.split()
    satirlar: List[str] = []
    mevcut = ""
    for k in kelimeler:
        aday = f"{mevcut} {k}".strip()
        # textbbox tüm fontlarda var (Pillow 9+)
        try:
            sol, ust, sag, alt = cizici.textbbox((0, 0), aday, font=font)
            genislik = sag - sol
        except Exception:
            genislik = len(aday) * (font.size // 2)
        if genislik <= max_genislik:
            mevcut = aday
        else:
            if mevcut:
                satirlar.append(mevcut)
            mevcut = k
    if mevcut:
        satirlar.append(mevcut)
    return satirlar


# ============================================================
# LOGO — modül cache'li
# ============================================================
_LOGO_CACHE: Dict[tuple, Image.Image] = {}


def _logo_hazirla(hedef_genislik: int, opacity: float = 0.85) -> Optional[Image.Image]:
    """Logoyu yarı şeffaf RGBA olarak döndür."""
    anahtar = (hedef_genislik, opacity)
    if anahtar in _LOGO_CACHE:
        return _LOGO_CACHE[anahtar]

    yol = Path(config.LOGO_TAM_YOLU)
    if not yol.exists():
        return None

    try:
        logo = Image.open(yol).convert("RGBA")
        oran = hedef_genislik / logo.width
        yeni_boyut = (hedef_genislik, int(logo.height * oran))
        logo = logo.resize(yeni_boyut, Image.LANCZOS)

        # Krem arkaplanı şeffaflaştır
        veri = logo.load()
        for y in range(logo.height):
            for x in range(logo.width):
                r, g, b, a = veri[x, y]
                if r > 230 and g > 220 and b > 200:  # krem
                    veri[x, y] = (r, g, b, 0)
                else:
                    veri[x, y] = (r, g, b, int(a * opacity))
        _LOGO_CACHE[anahtar] = logo
        return logo
    except Exception:
        return None


# ============================================================
# SLAYT TÜRLERİ — her biri 1080×1350
# ============================================================
def _slayt_1_hook(
    metin: str,
    badge: str,
    kapak_yolu: Optional[str],
    cikti_yolu: Path,
) -> bool:
    """
    İlk slayt: hook metni + (varsa) blur'lanmış kapak + ülke badge.
    Yoksa düz krem arkaplan + büyük tipografi.
    """
    W, H = config.CAROUSEL_W, config.CAROUSEL_H
    slayt = Image.new("RGB", (W, H), config.KREM_ARKAPLAN)

    # Kapak varsa: blur + karartma + üzerine metin
    if kapak_yolu and Path(kapak_yolu).exists():
        try:
            kapak = Image.open(kapak_yolu).convert("RGB")
            # Cover (oranı koruyarak doldur)
            oran_w = W / kapak.width
            oran_h = H / kapak.height
            oran = max(oran_w, oran_h)
            yeni_boyut = (int(kapak.width * oran), int(kapak.height * oran))
            kapak = kapak.resize(yeni_boyut, Image.LANCZOS)
            sol = (kapak.width - W) // 2
            ust = (kapak.height - H) // 2
            kapak = kapak.crop((sol, ust, sol + W, ust + H))
            kapak = kapak.filter(ImageFilter.GaussianBlur(radius=8))

            # Lacivert karartma overlay
            overlay = Image.new("RGB", (W, H), config.KOYU_LACIVERT)
            slayt = Image.blend(kapak, overlay, alpha=0.65)
        except Exception:
            pass

    cizici = ImageDraw.Draw(slayt)

    # Badge — üstte, kırmızı
    badge_font = _font_yukle(38, kalin=True)
    badge_padding = (24, 12)
    try:
        sol, ust, sag, alt = cizici.textbbox((0, 0), badge, font=badge_font)
        b_w, b_h = sag - sol, alt - ust
    except Exception:
        b_w, b_h = len(badge) * 20, 40
    b_kutu_w = b_w + badge_padding[0] * 2
    b_kutu_h = b_h + badge_padding[1] * 2
    b_x = (W - b_kutu_w) // 2
    b_y = 80
    cizici.rectangle(
        [b_x, b_y, b_x + b_kutu_w, b_y + b_kutu_h],
        fill=config.SICAK_KIRMIZI,
    )
    cizici.text(
        (b_x + badge_padding[0], b_y + badge_padding[1] - 4),
        badge, font=badge_font, fill=config.YUMUSAK_BEYAZ,
    )

    # Hook metni — büyük, beyaz veya lacivert (arkaplana göre)
    metin_rengi = config.YUMUSAK_BEYAZ if kapak_yolu and Path(str(kapak_yolu)).exists() else config.KOYU_LACIVERT
    hook_font = _font_yukle(72, kalin=True)
    satirlar = _metin_kutulu_sar(cizici, metin, hook_font, W - 160)

    # Dikey ortalama (hook genelde 3-4 satır)
    satir_yuksekligi = int(hook_font.size * 1.25)
    toplam_yukseklik = len(satirlar) * satir_yuksekligi
    y_baslangic = (H - toplam_yukseklik) // 2

    for i, s in enumerate(satirlar):
        try:
            sol, ust, sag, alt = cizici.textbbox((0, 0), s, font=hook_font)
            s_w = sag - sol
        except Exception:
            s_w = len(s) * 30
        s_x = (W - s_w) // 2
        s_y = y_baslangic + i * satir_yuksekligi
        cizici.text((s_x, s_y), s, font=hook_font, fill=metin_rengi)

    # Aşağıda küçük "Kaydır →" ipucu
    kucuk_font = _font_yukle(28)
    kaydir_metni = "Kaydır →"
    try:
        sol, ust, sag, alt = cizici.textbbox((0, 0), kaydir_metni, font=kucuk_font)
        k_w = sag - sol
    except Exception:
        k_w = 100
    cizici.text(
        ((W - k_w) // 2, H - 120),
        kaydir_metni, font=kucuk_font, fill=metin_rengi,
    )

    try:
        slayt.save(cikti_yolu, "PNG", quality=95)
        return True
    except Exception:
        return False


def _slayt_orta(
    metin: str,
    slayt_no: int,
    toplam_slayt: int,
    cikti_yolu: Path,
) -> bool:
    """Yapı taşı slaytları (2-4): krem arkaplan + büyük tipografi + ilerleme çubuğu."""
    W, H = config.CAROUSEL_W, config.CAROUSEL_H
    slayt = Image.new("RGB", (W, H), config.YUMUSAK_BEYAZ)
    cizici = ImageDraw.Draw(slayt)

    # Üstte ilerleme çubuğu (kaç slayttayız?)
    cubuk_y = 80
    cubuk_genislik = 240
    cubuk_yukseklik = 4
    cubuk_x = (W - cubuk_genislik) // 2
    # Arkaplan çubuğu (gri)
    cizici.rectangle(
        [cubuk_x, cubuk_y, cubuk_x + cubuk_genislik, cubuk_y + cubuk_yukseklik],
        fill=config.ACIK_GRI,
    )
    # Dolu kısım
    dolu_genislik = int(cubuk_genislik * (slayt_no / toplam_slayt))
    cizici.rectangle(
        [cubuk_x, cubuk_y, cubuk_x + dolu_genislik, cubuk_y + cubuk_yukseklik],
        fill=config.HARDAL_SARI,
    )

    # Slayt numarası
    no_font = _font_yukle(32, kalin=True)
    no_metin = f"{slayt_no} / {toplam_slayt}"
    try:
        sol, ust, sag, alt = cizici.textbbox((0, 0), no_metin, font=no_font)
        no_w = sag - sol
    except Exception:
        no_w = 80
    cizici.text(
        ((W - no_w) // 2, cubuk_y - 50),
        no_metin, font=no_font, fill=config.HARDAL_SARI,
    )

    # Ana metin — büyük, ortalanmış
    metin_font = _font_yukle(64, kalin=True)
    satirlar = _metin_kutulu_sar(cizici, metin, metin_font, W - 200)

    satir_yuksekligi = int(metin_font.size * 1.3)
    toplam_yukseklik = len(satirlar) * satir_yuksekligi
    y_baslangic = (H - toplam_yukseklik) // 2

    for i, s in enumerate(satirlar):
        try:
            sol, ust, sag, alt = cizici.textbbox((0, 0), s, font=metin_font)
            s_w = sag - sol
        except Exception:
            s_w = len(s) * 28
        s_x = (W - s_w) // 2
        s_y = y_baslangic + i * satir_yuksekligi
        cizici.text((s_x, s_y), s, font=metin_font, fill=config.KOYU_LACIVERT)

    # Alt köşede logo
    logo = _logo_hazirla(hedef_genislik=180, opacity=0.4)
    if logo:
        slayt.paste(logo, ((W - logo.width) // 2, H - 140), logo)

    try:
        slayt.save(cikti_yolu, "PNG", quality=95)
        return True
    except Exception:
        return False


def _slayt_5_cta(
    cta_metni: str,
    soru: str,
    cikti_yolu: Path,
) -> bool:
    """Son slayt: 'Save this post 📌' + soru + logo merkezde."""
    W, H = config.CAROUSEL_W, config.CAROUSEL_H
    slayt = Image.new("RGB", (W, H), config.KOYU_LACIVERT)
    cizici = ImageDraw.Draw(slayt)

    # Üstte: SAVE
    save_font = _font_yukle(80, kalin=True)
    try:
        sol, ust, sag, alt = cizici.textbbox((0, 0), cta_metni, font=save_font)
        s_w = sag - sol
    except Exception:
        s_w = len(cta_metni) * 38
    cizici.text(
        ((W - s_w) // 2, 280),
        cta_metni, font=save_font, fill=config.HARDAL_SARI,
    )

    # Ortada: soru — satır sarılı
    soru_font = _font_yukle(48)
    satirlar = _metin_kutulu_sar(cizici, soru, soru_font, W - 200)
    satir_yuksekligi = int(soru_font.size * 1.4)
    toplam_yukseklik = len(satirlar) * satir_yuksekligi
    y_baslangic = (H // 2) - (toplam_yukseklik // 2) + 60

    for i, s in enumerate(satirlar):
        try:
            sol, ust, sag, alt = cizici.textbbox((0, 0), s, font=soru_font)
            sw = sag - sol
        except Exception:
            sw = len(s) * 20
        cizici.text(
            ((W - sw) // 2, y_baslangic + i * satir_yuksekligi),
            s, font=soru_font, fill=config.YUMUSAK_BEYAZ,
        )

    # Aşağıda büyük logo
    logo = _logo_hazirla(hedef_genislik=360, opacity=0.95)
    if logo:
        slayt.paste(logo, ((W - logo.width) // 2, H - 280), logo)

    # Logonun altında website etiketi
    site_font = _font_yukle(24)
    site_metin = "balkanlardan.com"
    try:
        sol, ust, sag, alt = cizici.textbbox((0, 0), site_metin, font=site_font)
        sw = sag - sol
    except Exception:
        sw = 200
    cizici.text(
        ((W - sw) // 2, H - 80),
        site_metin, font=site_font, fill=config.HARDAL_SARI,
    )

    try:
        slayt.save(cikti_yolu, "PNG", quality=95)
        return True
    except Exception:
        return False


# ============================================================
# DIŞ API
# ============================================================
def carousel_uret(
    slaytlar: List[str],
    cta_soru: str,
    badge_metni: str,
    kapak_yolu: Optional[str],
    cikti_klasoru: Path,
    dosya_on_eki: str,
    cta_metni: str = "Save this post 📌",
    log_callback: Callable[[str], None] = print,
) -> Dict[str, List[Path]]:
    """
    5'li carousel slayt seti üretir.

    Args:
        slaytlar: 5 elemanlı string listesi (Claude'dan gelir)
        cta_soru: 5. slaydın orta soru metni
        badge_metni: 1. slayttaki ülke etiketi (örn: 'TÜRKİYE')
        kapak_yolu: Varsa, 1. slaydın arkaplanı
        cikti_klasoru: Çıktı klasörü
        dosya_on_eki: "TR_Kultur_03" gibi prefix
        cta_metni: 5. slaytta üst metin

    Returns:
        {'slaytlar': [Path, ...], 'basarisiz': [int, ...]}
    """
    cikti_klasoru = Path(cikti_klasoru)
    cikti_klasoru.mkdir(parents=True, exist_ok=True)

    if len(slaytlar) < 5:
        # Eksik slayt geldi → boşları doldur
        log_callback(f"      ⚠️  Sadece {len(slaytlar)} slayt verildi, 5'e tamamlanıyor")
        slaytlar = list(slaytlar) + [""] * (5 - len(slaytlar))

    uretilenler: List[Path] = []
    basarisizlar: List[int] = []

    # SLAYT 1 — HOOK
    s1_yol = cikti_klasoru / f"{dosya_on_eki}_carousel_01_hook.png"
    log_callback("      🖼️  Carousel slayt 1 (HOOK)")
    if _slayt_1_hook(slaytlar[0], badge_metni, kapak_yolu, s1_yol):
        uretilenler.append(s1_yol)
    else:
        basarisizlar.append(1)

    # SLAYTLAR 2-4 — ORTA
    for i, no in enumerate([2, 3, 4]):
        s_yol = cikti_klasoru / f"{dosya_on_eki}_carousel_0{no}.png"
        log_callback(f"      🖼️  Carousel slayt {no}")
        if _slayt_orta(slaytlar[i + 1], no, 5, s_yol):
            uretilenler.append(s_yol)
        else:
            basarisizlar.append(no)

    # SLAYT 5 — CTA
    s5_yol = cikti_klasoru / f"{dosya_on_eki}_carousel_05_cta.png"
    log_callback("      🖼️  Carousel slayt 5 (CTA)")
    # CTA slaydında "soru" alanına slaytlar[4] (Claude'un 5. slaytı) ile cta_soru'yu kombine
    son_soru = cta_soru or slaytlar[4] or ""
    if _slayt_5_cta(cta_metni, son_soru, s5_yol):
        uretilenler.append(s5_yol)
    else:
        basarisizlar.append(5)

    log_callback(f"      ✅ {len(uretilenler)}/5 carousel slayt üretildi")
    return {
        "slaytlar": uretilenler,
        "basarisiz": basarisizlar,
    }
