"""
=================================================================
modules/pexels_motoru.py — Pexels API Video/Foto Çekici (v1)
=================================================================
Pexels'ten kelime grubuna göre video arar, en uygun olanı seçer,
geçici klasöre indirir, dosya yolunu döner.

Tasarım kararları:
  • DİKEY (portrait) formatlı videolar öncelikli — sosyal medya 9:16
    için ideal. Yoksa yatay alıp ortadan dikey crop edilebilir.
  • Süre 5-30 saniye arası tercih edilir. Çok uzun videolar trim
    edilebilir (caller'ın işi — medya_uretimi karar verir).
  • HD veya 4K kalitesi öncelikli, ama 50MB'tan büyük dosyaları atla
    (Pexels bazen 4K'yı 200MB+ veriyor, indirmesi çok yavaş).
  • Aynı sorgu için cache: aynı arama tekrarlanırsa tekrar API'ye
    gitme — modül seviyesi dict cache.
=================================================================
"""
import os
import time
import json
import hashlib
import traceback
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
import requests

from anthropic import Anthropic

from . import config


# ============================================================
# API URL'leri
# ============================================================
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "").strip()
PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"
PEXELS_PHOTO_SEARCH_URL = "https://api.pexels.com/v1/search"

# Modül seviyesi cache — aynı sorguya tekrar tekrar API atmaktan kaçın
_ARAMA_CACHE: Dict[str, List[Dict[str, Any]]] = {}

# Tohum metninden türetilen Pexels terimleri için ayrı cache
# Aynı tohum birden fazla dilde yayın yapılırken her seferinde Claude'a
# gitmeyelim — bir kere üret, hepsi aynı terimi kullansın
_TOHUM_TERIM_CACHE: Dict[str, List[str]] = {}

# Anthropic client — tohumdan terim üretmek için (lazy)
_anthropic_client: Optional[Anthropic] = None


def _claude_client() -> Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _anthropic_client


# ============================================================
# YARDIMCI: HTTP başlığı
# ============================================================
def _headers() -> Dict[str, str]:
    if not PEXELS_API_KEY:
        raise RuntimeError(
            "❌ PEXELS_API_KEY .env dosyasında tanımlı değil. "
            "https://www.pexels.com/api/ adresinden ücretsiz alabilirsin."
        )
    return {"Authorization": PEXELS_API_KEY}


# ============================================================
# BAĞLANTI TESTİ
# ============================================================
def baglanti_testi() -> tuple[bool, str]:
    """UI'dan çağrılır: Pexels'e ulaşabiliyor muyuz?"""
    if not PEXELS_API_KEY:
        return False, "❌ PEXELS_API_KEY .env'de yok"
    try:
        r = requests.get(
            PEXELS_VIDEO_SEARCH_URL,
            headers=_headers(),
            params={"query": "test", "per_page": 1},
            timeout=10,
        )
        if r.status_code == 200:
            kalan = r.headers.get("X-Ratelimit-Remaining", "?")
            return True, f"✅ Pexels OK — saatlik kalan istek: {kalan}"
        if r.status_code == 401:
            return False, "❌ API anahtarı geçersiz (401)"
        return False, f"❌ HTTP {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, f"❌ Hata: {e}"


# ============================================================
# VİDEO DOSYASI SEÇİCİSİ
# ============================================================
def _en_iyi_video_dosyasi(
    video_dict: Dict[str, Any],
    dikey_oncelik: bool = True,
    max_boyut_mb: int = 50,
) -> Optional[Dict[str, Any]]:
    """
    Bir Pexels video sonucundan en uygun dosya varyantını seç.

    Pexels her video için birden fazla "file" verir:
      - farklı çözünürlüklerde (HD, SD, 4K)
      - farklı format (mp4 hep olur)
      - genişlik/yükseklik bilgisi var

    Strateji:
      1) Dikey öncelikli ise, height > width olanlar üstte
      2) HD (720p-1080p) tercih, 4K şişman olabiliyor — sona
      3) max_boyut_mb'yi aşan dosyaları ele (file_size yoksa süreden tahmin)
    """
    video_files = video_dict.get("video_files", [])
    if not video_files:
        return None

    adaylar = []
    for vf in video_files:
        if vf.get("file_type") != "video/mp4":
            continue
        w = vf.get("width", 0)
        h = vf.get("height", 0)
        if w == 0 or h == 0:
            continue

        # Pexels'in "quality" alanı: hd, sd, uhd, hls
        kalite = vf.get("quality", "")
        if kalite == "hls":
            continue  # streaming — direkt indiremiyoruz

        # Boyut tahmini (Pexels 'file_size' her zaman vermiyor)
        # Süre yok bu seviyede ama parent video'da var:
        # eski formdaki gibi varsay: HD ≈ 1.5MB/sn
        sure = video_dict.get("duration", 10)
        if kalite == "uhd":
            tahmini_mb = sure * 6
        elif kalite == "hd":
            tahmini_mb = sure * 1.5
        else:
            tahmini_mb = sure * 0.5

        if tahmini_mb > max_boyut_mb:
            continue

        dikey_mi = h > w
        # Skor: dikey ise +100, hd ise +50, uhd ise +30
        skor = 0
        if dikey_oncelik and dikey_mi:
            skor += 100
        if not dikey_oncelik and not dikey_mi:
            skor += 100
        if kalite == "hd":
            skor += 50
        elif kalite == "uhd":
            skor += 30
        else:
            skor += 10

        adaylar.append((skor, vf))

    if not adaylar:
        return None

    adaylar.sort(key=lambda x: x[0], reverse=True)
    return adaylar[0][1]


# ============================================================
# VİDEO ARAMA
# ============================================================
def video_ara(
    sorgu: str,
    min_sure: int = 5,
    max_sure: int = 30,
    per_page: int = 15,
    dikey_oncelik: bool = True,
) -> List[Dict[str, Any]]:
    """
    Pexels'te video arar, filtrelenmiş sonuç listesi döner.

    Returns:
        Listede her eleman:
        {
          'pexels_id': int,
          'duration': int (saniye),
          'width': int,
          'height': int,
          'preview_url': str (küçük thumbnail),
          'video_url': str (indirilebilir mp4 URL),
          'pexels_url': str (orijinal sayfa, telif gösterimi için),
          'photographer': str,
        }
    """
    if not sorgu.strip():
        return []

    cache_key = f"v:{sorgu}:{min_sure}:{max_sure}:{dikey_oncelik}"
    if cache_key in _ARAMA_CACHE:
        return _ARAMA_CACHE[cache_key]

    try:
        params = {
            "query": sorgu,
            "per_page": per_page,
            "orientation": "portrait" if dikey_oncelik else "landscape",
        }
        r = requests.get(
            PEXELS_VIDEO_SEARCH_URL,
            headers=_headers(),
            params=params,
            timeout=15,
        )
        if r.status_code != 200:
            return []

        data = r.json()
        sonuclar: List[Dict[str, Any]] = []
        for v in data.get("videos", []):
            sure = v.get("duration", 0)
            if not (min_sure <= sure <= max_sure):
                continue

            en_iyi = _en_iyi_video_dosyasi(v, dikey_oncelik=dikey_oncelik)
            if not en_iyi:
                continue

            sonuclar.append({
                "pexels_id": v.get("id"),
                "duration": sure,
                "width": en_iyi.get("width"),
                "height": en_iyi.get("height"),
                "preview_url": v.get("image", ""),
                "video_url": en_iyi.get("link", ""),
                "pexels_url": v.get("url", ""),
                "photographer": v.get("user", {}).get("name", "Pexels"),
            })

        _ARAMA_CACHE[cache_key] = sonuclar
        return sonuclar
    except Exception:
        return []


# ============================================================
# VİDEO İNDİRME
# ============================================================
def video_indir(
    video_url: str,
    hedef_klasor: Path,
    dosya_adi: Optional[str] = None,
    log_callback: Callable[[str], None] = print,
) -> Optional[Path]:
    """
    Bir Pexels video URL'sini diske indirir.

    Args:
        video_url: Pexels'ten gelen direkt mp4 linki
        hedef_klasor: İndirileceği klasör
        dosya_adi: Verilmezse URL'den hash üretilir

    Returns:
        İndirilen dosyanın Path objesi, başarısızsa None
    """
    if not video_url:
        return None

    hedef_klasor = Path(hedef_klasor)
    hedef_klasor.mkdir(parents=True, exist_ok=True)

    if not dosya_adi:
        # URL'den deterministik bir isim üret
        h = hashlib.md5(video_url.encode()).hexdigest()[:12]
        dosya_adi = f"pexels_{h}.mp4"

    hedef_yol = hedef_klasor / dosya_adi

    # Aynı dosya zaten varsa tekrar indirme (cache)
    if hedef_yol.exists() and hedef_yol.stat().st_size > 1024:
        log_callback(f"      💾 Pexels cache hit: {dosya_adi}")
        return hedef_yol

    try:
        log_callback(f"      ⬇️  Pexels indiriliyor: {dosya_adi}")
        baslangic = time.time()
        with requests.get(video_url, stream=True, timeout=60) as r:
            if r.status_code != 200:
                log_callback(f"      ❌ İndirme HTTP {r.status_code}")
                return None
            with open(hedef_yol, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        sure = time.time() - baslangic
        boyut_mb = hedef_yol.stat().st_size / (1024 * 1024)
        log_callback(f"      ✅ İndirildi: {boyut_mb:.1f}MB / {sure:.1f}s")
        return hedef_yol
    except Exception as e:
        log_callback(f"      ❌ İndirme hatası: {e}")
        if hedef_yol.exists():
            try:
                hedef_yol.unlink()
            except Exception:
                pass
        return None


# ============================================================
# TEK BİR ÇAĞRIDA: ARA + EN İYİSİNİ İNDİR
# ============================================================
def ara_ve_indir(
    sorgu: str,
    hedef_klasor: Path,
    dosya_adi: Optional[str] = None,
    min_sure: int = 5,
    max_sure: int = 30,
    dikey_oncelik: bool = True,
    log_callback: Callable[[str], None] = print,
) -> Optional[Dict[str, Any]]:
    """
    Sorgu için Pexels'i arar ve ilk uygun videoyu indirir.

    Returns:
        {
          'yol': Path (indirilen mp4),
          'duration': int,
          'photographer': str,
          'pexels_url': str (atıf için),
        }
        veya bulunamazsa None.
    """
    log_callback(f"      🔍 Pexels arama: '{sorgu}'")
    sonuclar = video_ara(
        sorgu=sorgu,
        min_sure=min_sure,
        max_sure=max_sure,
        dikey_oncelik=dikey_oncelik,
    )
    if not sonuclar:
        # Dikey bulunamadıysa yataya da bak (fallback)
        if dikey_oncelik:
            log_callback("      ↩️  Dikey yok, yatay deneniyor")
            sonuclar = video_ara(
                sorgu=sorgu,
                min_sure=min_sure,
                max_sure=max_sure,
                dikey_oncelik=False,
            )

    if not sonuclar:
        log_callback(f"      ⚠️  '{sorgu}' için sonuç yok")
        return None

    log_callback(f"      📊 {len(sonuclar)} aday bulundu, ilkini alıyoruz")
    secilen = sonuclar[0]
    yol = video_indir(
        video_url=secilen["video_url"],
        hedef_klasor=hedef_klasor,
        dosya_adi=dosya_adi,
        log_callback=log_callback,
    )
    if not yol:
        return None

    return {
        "yol": yol,
        "duration": secilen["duration"],
        "photographer": secilen["photographer"],
        "pexels_url": secilen["pexels_url"],
    }


# ============================================================
# ÇOKLU ARAMA — Bir taslak için 4 farklı sahne videosu
# ============================================================
def coklu_video_indir(
    sorgular: List[str],
    hedef_klasor: Path,
    on_ek: str = "sahne",
    log_callback: Callable[[str], None] = print,
) -> List[Dict[str, Any]]:
    """
    Bir liste sorgu alır, her biri için bir video indirir.

    Örnek kullanım (medya_uretimi'nden):
      sorgular = ["fermented drink", "balkan market", "pouring glass", "traditional food"]
      videolar = pexels_motoru.coklu_video_indir(sorgular, klasor, "boza")

    Returns:
        Başarılı indirmelerin listesi (None'lar atılır).
        Eğer hiçbiri inemediyse boş liste.
    """
    sonuclar = []
    for i, sorgu in enumerate(sorgular, 1):
        sonuc = ara_ve_indir(
            sorgu=sorgu,
            hedef_klasor=hedef_klasor,
            dosya_adi=f"{on_ek}_{i:02d}.mp4",
            log_callback=log_callback,
        )
        if sonuc:
            sonuclar.append(sonuc)

    return sonuclar

# ============================================================
# TOHUMDAN PEXELS ARAMA TERİMLERİ ÜRET (Parça 6)
# ============================================================
# Mevcut sistem: yayin_motoru, "gelenek" temasını biliyor ama
# "Nestinari — ateş üstünde dans" tohumunu bilmiyordu. Sonuç:
# gelenek havuzundan rastgele 4 terim çıkıyor (kilim dokuyan kadın,
# kına gecesi mağaza çekimi vs.) — Nestinari'nin ATEŞ konusuyla
# alakasız.
#
# Bu fonksiyon çözüm: tohum metnini Claude'a ver, tohuma özel
# İngilizce Pexels arama terimleri üretsin. Cache'liyoruz ki
# aynı tohum 6 dilde yayın yaparken Claude'a 6 kez gitmeyelim.
#
# Fallback: Claude patlarsa veya boş dönerse, caller (yayin_motoru)
# eski tema havuzuna düşsün — boş liste döneriz, sistem ayakta kalır.
# ============================================================

_TERIM_URETME_SISTEM_PROMPTU = """You generate Pexels stock video search terms for a Balkan cultural content platform.

Your job: read a short topic seed (usually in Turkish), and produce English search terms
that will find good ATMOSPHERIC background footage on Pexels for that specific topic.

Rules:
- Output ENGLISH terms only (Pexels stock library is English-tagged).
- Each term is 2-5 words, lowercase, no punctuation.
- Each term must be CONCRETE and VISUAL — describe what would be on screen,
  not the abstract concept. "fire walking ritual" YES, "spiritual tradition" NO.
- Prefer terms that suggest Balkan / Mediterranean / European aesthetic where
  relevant. Avoid terms that will pull Indian / Asian / Latin American footage
  by default (e.g. "henna ceremony" pulls Indian — use "balkan wedding" or
  "european bride veil" instead).
- For very obscure topics where Pexels likely has no specific footage,
  produce atmospheric proxies that capture the FEELING (smoke, candles, old hands,
  village square) rather than the literal subject.
- Output ONLY a JSON array of strings. No prose, no markdown, no explanation.
  Example output: ["fire walking ritual", "ember dance ceremony", "sacred bonfire night", "barefoot ritual ground"]
"""


def tohumdan_terim_uret(
    tohum_metni: str,
    sayi: int = 4,
    log_callback: Callable[[str], None] = print,
) -> List[str]:
    """
    Bir tohum metninden (örn. "Nestinari — Bulgar ateş üstünde dans ayini"),
    Claude'a sorarak N adet İngilizce Pexels arama terimi üretir.

    Cache'lidir: aynı tohum_metni için aynı sonuç döner (oturum boyunca).

    Args:
        tohum_metni: Tohum cümlesi (genelde Türkçe). Olduğu gibi Claude'a gider.
        sayi: Kaç terim istenir (varsayılan 4 = video özet sahnesi sayısı).
        log_callback: İlerleme/hata logları için.

    Returns:
        N adet İngilizce arama terimi. Hata durumunda BOŞ LİSTE döner —
        caller bunu görüp eski tema havuzu fallback'ine geçmeli.
    """
    if not tohum_metni or not tohum_metni.strip():
        return []

    # Cache anahtarı: tohum + sayı (farklı sayı isteklerinde tekrar üret)
    cache_key = f"{tohum_metni.strip()}|{sayi}"
    if cache_key in _TOHUM_TERIM_CACHE:
        log_callback(f"      💾 Tohum terim cache hit")
        return _TOHUM_TERIM_CACHE[cache_key]

    kullanici_mesaji = (
        f"Topic seed: {tohum_metni}\n\n"
        f"Generate exactly {sayi} Pexels search terms for atmospheric background "
        f"video for this topic. Output only a JSON array of {sayi} strings."
    )

    try:
        log_callback(f"      🧠 Claude'dan Pexels terimleri isteniyor...")
        cevap = _claude_client().messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=300,
            system=_TERIM_URETME_SISTEM_PROMPTU,
            messages=[{"role": "user", "content": kullanici_mesaji}],
        )
        metin = cevap.content[0].text.strip()

        # ```json fence temizliği (trend_motoru'ndaki kalıbın aynısı)
        if metin.startswith("```"):
            metin = metin.split("```", 2)[1]
            if metin.startswith("json"):
                metin = metin[4:]
            metin = metin.strip()

        terimler = json.loads(metin)

        # Doğrulama: liste mi, string mi içeriyor?
        if not isinstance(terimler, list):
            log_callback(f"      ⚠️  Claude liste döndürmedi, fallback'e düşülecek")
            return []

        # String olmayanları ele, boşları ele, lowercase, strip
        temiz = []
        for t in terimler:
            if isinstance(t, str) and t.strip():
                temiz.append(t.strip().lower())

        if not temiz:
            log_callback(f"      ⚠️  Üretilen terim listesi boş, fallback")
            return []

        # İstenen sayı kadar al (Claude bazen fazla verir)
        temiz = temiz[:sayi]

        log_callback(f"      ✅ {len(temiz)} terim üretildi: {temiz}")
        _TOHUM_TERIM_CACHE[cache_key] = temiz
        return temiz

    except json.JSONDecodeError as e:
        log_callback(f"      ⚠️  JSON parse hatası: {e}, fallback'e düşülecek")
        return []
    except Exception as e:
        log_callback(f"      ⚠️  Claude tohum terim hatası: {e}, fallback")
        if getattr(config, "DEBUG", False):
            traceback.print_exc()
        return []
