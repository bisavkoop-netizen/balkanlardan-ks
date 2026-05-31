"""
=================================================================
modules/wp_motoru.py — WordPress REST API Yayını
=================================================================
Akıllı kategori eşlemesi: routing.wp_kategorileri()'ne sorar,
kategori ID'lerini cache'ler, resim+video yükler ve postu yayınlar.
=================================================================
"""
from typing import List, Optional, Dict, Any
from pathlib import Path
import requests

from . import config
from . import routing


# Modül seviyesi kategori ID cache — uygulama yaşam döngüsü boyunca.
_KATEGORI_CACHE: Dict[str, int] = {}


# ============================================================
# DURUM TESTİ
# ============================================================
def wp_baglanti_testi() -> tuple[bool, str]:
    try:
        r = requests.get(
            f"{config.WP_URL}/categories",
            auth=(config.WP_USER, config.WP_APP_PASSWORD),
            timeout=10,
            params={"per_page": 1},
        )
        if r.status_code == 200:
            return True, f"✅ WP bağlantısı OK — {config.WP_URL}"
        return False, f"❌ HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, f"❌ Hata: {e}"


# ============================================================
# KATEGORİ ID ÇÖZÜCÜSÜ (ad → ID, gerekirse yarat)
# ============================================================
def _kategori_id_bul_veya_olustur(kategori_adi: str) -> Optional[int]:
    """Kategori adını WP ID'sine çevirir. Yoksa yaratır. Cache'ler."""
    if kategori_adi in _KATEGORI_CACHE:
        return _KATEGORI_CACHE[kategori_adi]

    auth = (config.WP_USER, config.WP_APP_PASSWORD)

    # 1) Mevcut mu?
    try:
        r = requests.get(
            f"{config.WP_URL}/categories",
            auth=auth, timeout=15,
            params={"search": kategori_adi, "per_page": 20},
        )
        if r.status_code == 200:
            for kat in r.json():
                if kat.get("name", "").strip().lower() == kategori_adi.strip().lower():
                    _KATEGORI_CACHE[kategori_adi] = kat["id"]
                    return kat["id"]
    except Exception:
        pass

    # 2) Yarat
    try:
        r = requests.post(
            f"{config.WP_URL}/categories",
            auth=auth, timeout=15,
            json={"name": kategori_adi},
        )
        if r.status_code in (200, 201):
            kid = r.json().get("id")
            if kid:
                _KATEGORI_CACHE[kategori_adi] = kid
                return kid
    except Exception:
        pass
    return None


# ============================================================
# MEDYA YÜKLEYİCİSİ (resim veya video)
# ============================================================
def _medya_yukle(dosya_yolu: str) -> Optional[int]:
    """Bir dosyayı /media endpoint'ine yükler, medya ID'sini döner."""
    if not dosya_yolu or not Path(dosya_yolu).exists():
        return None
    auth = (config.WP_USER, config.WP_APP_PASSWORD)
    dosya = Path(dosya_yolu)
    try:
        with open(dosya, "rb") as f:
            r = requests.post(
                f"{config.WP_URL}/media",
                auth=auth, timeout=120,
                headers={"Content-Disposition": f'attachment; filename="{dosya.name}"'},
                files={"file": (dosya.name, f)},
            )
        if r.status_code in (200, 201):
            return r.json().get("id")
    except Exception:
        pass
    return None


# ============================================================
# ASIL YAYIN FONKSİYONU
# ============================================================
def yayinla(
    baslik: str,
    html_icerik: str,
    kapak_yolu: Optional[str],
    ulke_kodu: str,
    dil_kodu: str,
    video_yolu: Optional[str] = None,
    carousel_resimleri: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Bir haberi WordPress'e yayınlar.

    Kategoriler: routing.wp_kategorileri(ulke_kodu, dil_kodu)'dan alınır.
                 Yani ülke adı + dil adı = 2 kategori (otomatik eşleme).

    Args:
        carousel_resimleri: Opsiyonel PNG yolları listesi (genelde 5 carousel
                            slaytı). Her biri WP'ye yüklenir, sonra grid HTML
                            ile post içeriğine gömülür.

    Returns:
        {
          'ok': bool,
          'post_id': int|None,
          'kategoriler': [{'ad': 'Yunanistan', 'id': 12}, ...],
          'kapak_medya_id': int|None,
          'video_medya_id': int|None,
          'video_url': str|None,
          'carousel_medya_idleri': [int, ...],   ← YENİ
          'carousel_urlleri': [str, ...],         ← YENİ (sosyal motor için)
          'hata': str|None,
        }
    """
    auth = (config.WP_USER, config.WP_APP_PASSWORD)
    sonuc: Dict[str, Any] = {
        "ok": False, "post_id": None,
        "kategoriler": [], "kapak_medya_id": None, "video_medya_id": None,
        "video_url": None,
        "carousel_medya_idleri": [],   # YENİ
        "carousel_urlleri": [],         # YENİ
        "hata": None,
    }

    # 1) Routing matrisinden kategori isimlerini al
    kategori_adlari = routing.wp_kategorileri(ulke_kodu, dil_kodu)
    kategori_idleri: List[int] = []
    for ad in kategori_adlari:
        kid = _kategori_id_bul_veya_olustur(ad)
        if kid:
            kategori_idleri.append(kid)
            sonuc["kategoriler"].append({"ad": ad, "id": kid})

    # 2) Kapak resmi yükle
    kapak_id = _medya_yukle(kapak_yolu) if kapak_yolu else None
    sonuc["kapak_medya_id"] = kapak_id

    # 3) Video yükle ve HTML'e embed et
    video_html = ""
    if video_yolu:
        vid_id = _medya_yukle(video_yolu)
        sonuc["video_medya_id"] = vid_id
        if vid_id:
            # WP attachment URL'sini çek
            try:
                rr = requests.get(f"{config.WP_URL}/media/{vid_id}", auth=auth, timeout=15)
                if rr.status_code == 200:
                    video_url = rr.json().get("source_url", "")
                    if video_url:
                        sonuc["video_url"] = video_url  # sosyal motora geçecek
                        video_html = (
                            f'<video controls preload="metadata" '
                            f'style="width:100%; max-width:640px; margin:20px auto; display:block;">'
                            f'<source src="{video_url}" type="video/mp4"></video>'
                        )
            except Exception:
                pass

    # 4) Carousel resimleri yükle ve grid HTML üret
    carousel_html = ""
    if carousel_resimleri:
        carousel_urlleri: List[str] = []
        for resim_yolu in carousel_resimleri:
            if not resim_yolu:
                continue
            r_id = _medya_yukle(resim_yolu)
            if not r_id:
                continue
            sonuc["carousel_medya_idleri"].append(r_id)
            # source_url çek (sosyal motor için lazım olacak)
            try:
                rr = requests.get(f"{config.WP_URL}/media/{r_id}", auth=auth, timeout=15)
                if rr.status_code == 200:
                    url = rr.json().get("source_url", "")
                    if url:
                        carousel_urlleri.append(url)
            except Exception:
                pass

        sonuc["carousel_urlleri"] = carousel_urlleri

        # Grid HTML — CSS Grid ile yan yana, mobilde otomatik alt alta
        if carousel_urlleri:
            img_etiketleri = "\n".join(
                f'    <img src="{u}" alt="Carousel slayt {i+1}" '
                f'style="width:100%; height:auto; border-radius:8px;" loading="lazy" />'
                for i, u in enumerate(carousel_urlleri)
            )
            carousel_html = (
                '<div style="display:grid; '
                'grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); '
                'gap:12px; margin:24px 0;">\n'
                f'{img_etiketleri}\n'
                '</div>\n'
            )

    # 5) HTML birleştir (sıra: video → carousel → ana içerik)
    tam_icerik = video_html + carousel_html + html_icerik

    # 6) Postu yayınla
    payload = {
        "title": baslik,
        "content": tam_icerik,
        "status": "publish",
        "categories": kategori_idleri,
    }
    if kapak_id:
        payload["featured_media"] = kapak_id

    try:
        r = requests.post(f"{config.WP_URL}/posts", auth=auth, timeout=30, json=payload)
        if r.status_code in (200, 201):
            sonuc["ok"] = True
            sonuc["post_id"] = r.json().get("id")
        else:
            sonuc["hata"] = f"HTTP {r.status_code}: {r.text[:300]}"
    except Exception as e:
        sonuc["hata"] = str(e)

    return sonuc
