"""
=================================================================
modules/yayin_motoru.py — Ses + Video + Yayın Orkestratörü (v4)
=================================================================
Bir taslağı baştan sona yayına alır:
  1) medya_uretimi → her dil için ses + arkaplan + video
  2) wp_motoru.yayinla() → HER DİL için ayrı WP postu + video gömülü
  3) sosyal_motoru.dagit() → TEK SEFER, 4 dilli caption, global hesaplara

KARAR VERMEZ: hangi kategori/hesap routing.py'nin işi.
PIKSEL/SES İŞLEMEZ: medya_uretimi'nin işi.
=================================================================
"""
from pathlib import Path
from typing import Dict, Any, Callable, Optional
from urllib.parse import urlparse

from . import config
from . import storage
from . import wp_motoru
from . import sosyal_motoru
from . import medya_uretimi
from . import carousel_uretimi


# ============================================================
# YAYIN KLASÖR DÜZENİ
# ============================================================
def _yayin_klasorlerini_kur(ulke_klasor_adi: str, diller: list) -> Dict[str, Any]:
    """
    Bir ülke için yayın çıktı klasörlerini hazırlar:

        Arşiv/_Yayin_<ülke>/Sosyal_Medya/         ← TEK global sosyal video
        Arşiv/_Yayin_<ülke>/<dil>_Detay/          ← WP'ye gömülen videolar
        Arşiv/_Yayin_<ülke>/Sesler_Resimler/      ← MP3 + JPG ara çıktılar

    Her dilin 'Detay' klasörü ayrı, çünkü WP postlarına dil-spesifik
    video gömüyoruz. Sosyal klasörü tek çünkü tek post atılıyor.
    """
    kok = config.ARSIV_KLASORU / f"_Yayin_{ulke_klasor_adi}"
    klasorler: Dict[str, Any] = {
        "kok": kok,
        "sesler_resimler": kok / "Sesler_Resimler",
        "sosyal_medya": kok / "Sosyal_Medya",
        "diller": {},
    }
    for d in diller:
        klasorler["diller"][d] = {
            "detay": kok / f"{d}_Detay",
            "carousel": kok / f"{d}_Carousel",   # YENİ: carousel slaytları ayrı
        }

    # Hepsini yarat
    for p in [klasorler["sesler_resimler"], klasorler["sosyal_medya"]]:
        p.mkdir(parents=True, exist_ok=True)
    for d_klasor in klasorler["diller"].values():
        d_klasor["detay"].mkdir(parents=True, exist_ok=True)
        d_klasor["carousel"].mkdir(parents=True, exist_ok=True)  # YENİ

    return klasorler


# ============================================================
# MEDYA ÜRETİMİ — TÜM DİLLER İÇİN
# ============================================================
def _tum_videolari_uret(
    taslak: Dict[str, Any],
    klasorler: Dict[str, Any],
    log_callback: Callable[[str], None],
    kullanici_video_yollari: Optional[list] = None,
) -> Dict[str, Optional[Path]]:
    """
    Her dil için ayrı detay videosu üretir. İlk başarılı olan dilin
    videosu aynı zamanda 'sosyal' video olarak kullanılır — ayrı render
    yapmamak için (Reels/X/TikTok bu tek videoyu paylaşacak).

    Sosyal için TR önceliklidir (en geniş kitleye Türkçe sesli ulaşıyoruz);
    TR yoksa orijinal dil, o da yoksa elimizdeki ilk dil.
    """
    meta = taslak["metadata"]
    ulke_kodu = meta["ulke_kodu"]
    ulke_klasor_adi = meta["ulke_klasor"]
    haber_idx = meta.get("haber_index", 0)
    kaynak = meta.get("kaynak_tipi", "otomatik")  # 'otomatik' veya 'ozel'
    kapak_yolu = taslak.get("_kapak_tam_yolu")

    ulke_bilgi = config.ULKELER[ulke_kodu]
    badgeler = ulke_bilgi.get("badgeler", {})

    detay_videolari: Dict[str, Optional[Path]] = {}

    # ============================================================
    # YENİ: ORTAK ARKAPLAN VİDEOSU (tüm dillerden önce, BİR KEZ)
    # ============================================================
    # Öncelik sırası:
    #   1) Kullanıcı Streamlit'ten video yüklediyse → onu kullan (Canva Pro stok)
    #   2) Pexels API'den otomatik üret
    #   3) Hiçbiri olmazsa None → dil_icin_video_uret statik fallback'e düşer
    pexels_arkaplan_yolu: Optional[Path] = None
    tema = meta.get("tema")
    video_istendi = "video" in meta.get("format_listesi", [])

    if video_istendi and kullanici_video_yollari:
        # YOL 1: Kullanıcı yüklediği videoları kullan
        log_callback(f"   🎨 Kullanıcı arkaplan videosu kullanılıyor ({len(kullanici_video_yollari)} dosya)")
        ortak_klasor = klasorler.get("sosyal_medya", klasorler["diller"][meta["uretilecek_diller"][0]]["detay"])
        kullanici_cikti = ortak_klasor / f"_kullanici_ortak_arkaplan_{ulke_klasor_adi}_{haber_idx:02d}.mp4"
        pexels_arkaplan_yolu = medya_uretimi.kullanici_videosundan_arkaplan_olustur(
            kaynak_video_yollari=kullanici_video_yollari,
            hedef_sure=32.0,
            cikti_yolu=kullanici_cikti,
            log_callback=log_callback,
        )
        if pexels_arkaplan_yolu:
            log_callback(f"   ✅ Ortak kullanıcı arkaplan hazır")
        else:
            log_callback(f"   ⚠️  Kullanıcı arkaplan başarısız, statik fallback'e düşülecek")
    elif video_istendi and tema:
        # YOL 2: Pexels'ten otomatik üret (mevcut akış)
        log_callback(f"   🎨 Pexels ortak arkaplan üretiliyor (tema: {tema})")
        # 32 saniye = özet kısmının yaklaşık uzunluğu (3sn hook + 32sn özet + 5sn cta = 40sn)
        # Ses süresi farklı olabilir, ama Pexels arkaplanı kullanılırken
        # dil_icin_video_uret videoyu döngüye alarak ses süresine uydurur.
        ortak_klasor = klasorler.get("sosyal_medya", klasorler["diller"][meta["uretilecek_diller"][0]]["detay"])
        pexels_cikti = ortak_klasor / f"_pexels_ortak_arkaplan_{ulke_klasor_adi}_{haber_idx:02d}.mp4"
        pexels_arkaplan_yolu = medya_uretimi.pexels_arkaplan_videosu_olustur(
            tema=tema,
            hedef_sure=32.0,
            cikti_yolu=pexels_cikti,
            sahne_sayisi=4,
            log_callback=log_callback,
        )
        if pexels_arkaplan_yolu:
            log_callback(f"   ✅ Ortak Pexels arkaplan hazır")
        else:
            log_callback(f"   ⚠️  Pexels arkaplan başarısız, statik fallback'e düşülecek")
    else:
        log_callback(f"   ℹ️  Tema yok veya video formatı istenmedi — arkaplan üretimi atlanıyor")

    for dil_kodu in meta["uretilecek_diller"]:
        dil_data = taslak["diller"].get(dil_kodu, {})
        baslik = (dil_data.get("baslik") or "").strip()
        hook = (dil_data.get("hook") or "").strip()
        ozet = (dil_data.get("ozet") or "").strip()
        cta_soru = (dil_data.get("cta_soru") or "").strip()

        # Yeni şema: hook + ozet + cta_soru üçü de gerekli
        eksikler = []
        if not baslik:    eksikler.append("baslik")
        if not hook:      eksikler.append("hook")
        if not ozet:      eksikler.append("ozet")
        if not cta_soru:  eksikler.append("cta_soru")

        if eksikler:
            log_callback(f"   ⚠️  {dil_kodu}: eksik alanlar {eksikler} — video üretilmiyor")
            detay_videolari[dil_kodu] = None
            continue

        log_callback(f"   🎬 {dil_kodu} medya pipeline başlıyor")

        # Dosya ismi prefix'i: 'TR_Haber_05' veya 'TR_Ozel_01' veya 'TR_Kultur_03'
        kaynak_etiket = "Ozel" if kaynak == "ozel" else (
            "Kultur" if "kultur" in kaynak else "Haber"
        )
        on_ek = (
            f"{ulke_klasor_adi}_"
            f"{kaynak_etiket}_"
            f"{haber_idx:02d}"
        )
        badge_metni = badgeler.get(dil_kodu, ulke_bilgi["ad_tr"].upper())

        # Detay klasörü — her dil ayrı
        cikti_klasor = klasorler["diller"][dil_kodu]["detay"]

        sonuc = medya_uretimi.dil_icin_video_uret(
            dil_kodu=dil_kodu,
            hook=hook,                 # YENİ: 0-3sn scroll-stopper
            ozet=ozet,                 # 3-35sn ana içerik
            cta_soru=cta_soru,         # YENİ: 35-40sn izleyiciye soru
            baslik=baslik,             # özet sahnesindeki büyük başlık
            badge_metni=badge_metni,
            kapak_yolu=kapak_yolu,
            cikti_klasoru=cikti_klasor,
            dosya_on_eki=on_ek,
            log_callback=log_callback,
            ozet_arkaplan_videosu=pexels_arkaplan_yolu,  # YENİ: ortak Pexels mp4 (None ise statik fallback)
        )
        detay_videolari[dil_kodu] = sonuc["video"]

    # --- Sosyal video kararı ---
    sosyal_aday = None
    aday_sira = ["TR", ulke_bilgi.get("orijinal_dil_kodu", "TR")]
    for d in aday_sira + list(detay_videolari.keys()):
        if detay_videolari.get(d):
            sosyal_aday = detay_videolari[d]
            break

    if sosyal_aday:
        # Sosyal_Medya/ klasörüne kopyala (referans için)
        import shutil
        sosyal_hedef = klasorler["sosyal_medya"] / f"sosyal_{sosyal_aday.name}"
        try:
            shutil.copy2(sosyal_aday, sosyal_hedef)
            detay_videolari["_sosyal"] = sosyal_hedef
            log_callback(f"   📦 Sosyal videosu hazır: {sosyal_hedef.name}")
        except Exception:
            detay_videolari["_sosyal"] = sosyal_aday  # kopya patladıysa orijinali kullan
    else:
        detay_videolari["_sosyal"] = None

    return detay_videolari


# ============================================================
# CAROUSEL ÜRETİMİ — TÜM DİLLER İÇİN (YENİ - v4)
# ============================================================
def _tum_carousellari_uret(
    taslak: Dict[str, Any],
    klasorler: Dict[str, Any],
    log_callback: Callable[[str], None],
) -> Dict[str, list]:
    """
    Her dil için 5'li carousel slayt seti üretir.

    Carousel video pipeline'a paraleldir — video başarısız olsa bile
    carousel üretilir (eğer alanlar doluysa). Her dilin carousel'ı
    o dilin başlığını, hook'unu, slayt metinlerini ve cta_soru'sunu
    kullanır.

    Returns:
        {
          'TR': [Path, Path, Path, Path, Path],   # 5 PNG yolu
          'EN': [...],
          ...
        }
        Eksik diller boş liste döner.
    """
    meta = taslak["metadata"]
    ulke_kodu = meta["ulke_kodu"]
    ulke_klasor_adi = meta["ulke_klasor"]
    haber_idx = meta.get("haber_index", 0)
    kaynak = meta.get("kaynak_tipi", "otomatik")
    kapak_yolu = taslak.get("_kapak_tam_yolu")

    ulke_bilgi = config.ULKELER[ulke_kodu]
    badgeler = ulke_bilgi.get("badgeler", {})

    tum_carousellar: Dict[str, list] = {}

    for dil_kodu in meta["uretilecek_diller"]:
        dil_data = taslak["diller"].get(dil_kodu, {})
        carousel_slaytlar = dil_data.get("carousel_slaytlar") or []
        cta_soru = (dil_data.get("cta_soru") or "").strip()

        # Eğer slayt metinleri yoksa carousel üretilemez
        if not carousel_slaytlar or len(carousel_slaytlar) < 5:
            log_callback(
                f"   ⚠️  {dil_kodu}: carousel_slaytlar eksik "
                f"({len(carousel_slaytlar)}/5) — carousel atlanıyor"
            )
            tum_carousellar[dil_kodu] = []
            continue

        log_callback(f"   🖼️  {dil_kodu} carousel pipeline başlıyor")

        # Dosya prefix'i — video ile aynı mantık
        kaynak_etiket = "Ozel" if kaynak == "ozel" else (
            "Kultur" if "kultur" in kaynak else "Haber"
        )
        on_ek = f"{ulke_klasor_adi}_{kaynak_etiket}_{haber_idx:02d}_{dil_kodu}"
        badge_metni = badgeler.get(dil_kodu, ulke_bilgi["ad_tr"].upper())

        cikti_klasor = klasorler["diller"][dil_kodu]["carousel"]

        c_sonuc = carousel_uretimi.carousel_uret(
            slaytlar=carousel_slaytlar,
            cta_soru=cta_soru,
            badge_metni=badge_metni,
            kapak_yolu=kapak_yolu,
            cikti_klasoru=cikti_klasor,
            dosya_on_eki=on_ek,
            log_callback=log_callback,
        )
        tum_carousellar[dil_kodu] = c_sonuc.get("slaytlar", [])

    return tum_carousellar


# ============================================================
# DIŞ API — TASLAĞI YAYINLA
# ============================================================
def taslagi_yayinla(
    taslak: Dict[str, Any],
    log_callback: Callable[[str], None] = print,
    kullanici_video_yollari: Optional[list] = None,
) -> Dict[str, Any]:
    """
    Tek bir taslağı yayına alır.

    Returns:
        {
          'wp_postlar':       {'TR': {ok, post_id, video_url, carousel_urlleri, ...}, ...},
          'sosyal':           {'IG': {ok, post_id}, ...},
          'video_yollari':    {'TR': str|None, ..., '_sosyal': str|None},
          'carousel_yollari': {'TR': [str, ...] | [], ...},   ← YENİ
        }
    """
    meta = taslak["metadata"]
    ulke_kodu = meta["ulke_kodu"]
    ulke_klasor = meta["ulke_klasor"]
    diller = meta["uretilecek_diller"]
    diller_data = taslak["diller"]
    kapak_yolu = taslak.get("_kapak_tam_yolu")
    gercek_url = meta.get("kaynak_url", "")

    log_callback(f"🚀 Yayın başlıyor: {meta.get('orijinal_baslik', '')[:60]}")

    klasorler = _yayin_klasorlerini_kur(ulke_klasor, diller)
    sonuc: Dict[str, Any] = {
        "wp_postlar": {},
        "sosyal": {},
        "video_yollari": {},
        "carousel_yollari": {},   # YENİ
    }

    # ============================================================
    # 1A) MEDYA ÜRETİMİ — VİDEO (her dil için 3-segmentli)
    # ============================================================
    log_callback("📹 Video pipeline başlıyor (hook → özet → CTA → birleştir)")
    video_dict = _tum_videolari_uret(taslak, klasorler, log_callback, kullanici_video_yollari=kullanici_video_yollari)
    sosyal_video_yolu = video_dict.pop("_sosyal", None)
    sonuc["video_yollari"] = {k: (str(v) if v else None) for k, v in video_dict.items()}
    sonuc["video_yollari"]["_sosyal"] = str(sosyal_video_yolu) if sosyal_video_yolu else None

    # ============================================================
    # 1B) MEDYA ÜRETİMİ — CAROUSEL (her dil için 5 PNG slayt)  YENİ
    # ============================================================
    log_callback("🖼️  Carousel pipeline başlıyor (5'li slayt her dil için)")
    carousel_dict = _tum_carousellari_uret(taslak, klasorler, log_callback)
    sonuc["carousel_yollari"] = {
        d: [str(p) for p in liste] for d, liste in carousel_dict.items()
    }

    # ============================================================
    # 2) WORDPRESS — Her dil için ayrı post (kategoriler ülke+dil)
    # ============================================================
    for dil_kodu in diller:
        dil_data = diller_data.get(dil_kodu, {})
        baslik = (dil_data.get("baslik") or "").strip()
        if not baslik:
            log_callback(f"   ⚠️  {dil_kodu}: başlık boş, WP yayını atlanıyor")
            continue

        ozet = dil_data.get("ozet", "") or ""
        uzun_metin = dil_data.get("uzun_metin", ozet) or ""
        tags = dil_data.get("hashtags", "") or ""
        kaynak_domain = urlparse(gercek_url).netloc if gercek_url else "Balkanlardan"

        wp_html = f"""
        <div style="border-left: 4px solid #0284c7; padding-left: 15px; margin-bottom: 20px;">
            <p style="font-size: 16px; font-style: italic; color: #475569;">{ozet}</p>
        </div>
        <div style="font-size: 16px; line-height: 1.8; color: #1e293b; margin-bottom: 30px; text-align: justify;">
            {uzun_metin.replace(chr(10), '<br>')}
        </div>
        <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0;"/>
        <p style="font-size: 14px; color: #64748b;"><strong>Source / Kaynak:</strong>
           {('<a href="' + gercek_url + '" target="_blank" style="color:#0284c7;">' + kaynak_domain + '</a>') if gercek_url else kaynak_domain}
        </p>
        <p style="color: #0ea5e9; font-weight: bold; font-size: 15px; margin-top: 15px;">{tags}</p>
        """

        # Carousel yollarını dil'e göre al — wp_motoru'ya string liste geçir
        bu_dil_carousel = [str(p) for p in carousel_dict.get(dil_kodu, [])]

        log_callback(f"   📝 WP yayını → {dil_kodu}")
        wp_sonuc = wp_motoru.yayinla(
            baslik=baslik,
            html_icerik=wp_html,
            kapak_yolu=kapak_yolu,
            ulke_kodu=ulke_kodu,
            dil_kodu=dil_kodu,
            video_yolu=str(video_dict.get(dil_kodu)) if video_dict.get(dil_kodu) else None,
            carousel_resimleri=bu_dil_carousel if bu_dil_carousel else None,  # YENİ
        )
        sonuc["wp_postlar"][dil_kodu] = wp_sonuc

        if wp_sonuc.get("ok"):
            kat_adlari = ", ".join(k["ad"] for k in wp_sonuc.get("kategoriler", []))
            carousel_sayi = len(wp_sonuc.get("carousel_medya_idleri", []))
            extra = f", {carousel_sayi} carousel" if carousel_sayi else ""
            log_callback(f"      ✅ {dil_kodu} WP post ID: {wp_sonuc.get('post_id')} ({kat_adlari}{extra})")
        else:
            log_callback(f"      ❌ {dil_kodu} WP hatası: {wp_sonuc.get('hata')}")

    # ============================================================
    # 3) SOSYAL — TEK SEFER, 4 DİLLİ CAPTION, GLOBAL HESAPLARA
    # ============================================================
    # Public video URL'sini ilk başarılı WP postundan al — IG/TT/FB
    # diskte dosya değil, public URL ister.
    public_video_url = None
    for wp_sonuc in sonuc["wp_postlar"].values():
        if wp_sonuc.get("video_url"):
            public_video_url = wp_sonuc["video_url"]
            break

    if sosyal_video_yolu or public_video_url:
        log_callback("   📡 Sosyal dağıtım başlıyor (4 dilli tek caption)")
        sonuc["sosyal"] = sosyal_motoru.dagit(
            video_yolu=str(sosyal_video_yolu) if sosyal_video_yolu else "",
            diller_data=diller_data,
            public_video_url=public_video_url,
            log_callback=log_callback,
        )
    else:
        log_callback("   ⏭️  Hiçbir dil için video üretilmedi — sosyal dağıtım atlandı")

    # ============================================================
    # 4) METADATA GÜNCELLE
    # ============================================================
    taslak["metadata"]["yayinlandi"] = True
    storage.taslak_kaydet(taslak, Path(taslak["_json_yolu"]))

    log_callback("🎉 Yayın akışı tamamlandı")
    return sonuc
