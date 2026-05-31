"""
=================================================================
modules/haber_motoru.py — Otomatik Haber Çekme Motoru (v2)
=================================================================
v11 kodundaki kanıtlanmış çekirdek fonksiyonların Newsroom CMS'e
uyarlanmış hali:

  • Whitelist entegrasyonu: GNews'e site: operatörü ile filtrelenmiş
    sorgu gönderilir → sadece güvenilir kaynaklardan haber gelir
  • deep_resolve_url + kapak_fotografi_url_bul + kapak_fotografi_indir
    v11'deki haliyle korundu (Google yönlendirmesini aşar, og:image alır)
  • Claude çeviri motoru v11'deki sıkı sistem promptuyla
    (Bulgarya/Yunan yasak kuralı dahil)
  • Çıktı doğrudan storage.py'ye yazılır — burada I/O yok

Dış API: toplu_haber_cek(), claude_baglanti_testi()
=================================================================
"""
import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Any, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from gnews import GNews
from anthropic import Anthropic

# Opsiyonel kütüphaneler — eski v11 koduyla aynı pattern
try:
    from googlenewsdecoder import gnewsdecoder
    DECODER_VAR = True
except ImportError:
    DECODER_VAR = False

try:
    from newspaper import Article
    NEWSPAPER_VAR = True
except ImportError:
    NEWSPAPER_VAR = False

from . import config
from . import storage
from .whitelist import gnews_site_sorgusu, kaynaklari_getir


# ============================================================
# ANTHROPIC İSTEMCİSİ (tek seferlik kurulum)
# ============================================================
_client: Optional[Anthropic] = None


def _claude_client() -> Anthropic:
    """Lazy-init Anthropic istemcisi. .env yüklendikten sonra çağrılır."""
    global _client
    if _client is None:
        _client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


# ============================================================
# YARDIMCI — Google URL'si mi?
# ============================================================
def _google_url_mu(url: Optional[str]) -> bool:
    """URL Google'a aitse (yönlendirme), gerçek kaynak değildir."""
    if not url:
        return True
    try:
        netloc = urlparse(url).netloc.lower()
        return "google" in netloc or "gstatic" in netloc
    except Exception:
        return True


# ============================================================
# GNEWS — WHITELISTED ARAMA
# ============================================================
def ulke_haberlerini_cek(ulke_kodu: str, dil_kodu: str, haber_sayisi: int) -> List[Dict]:
    """
    GNews'ten haber çeker AMA arama sorgusunu whitelist.py'deki
    'site:' operatörü zincirine kilitler. Yani sadece güvenilir
    kaynaklardan haber döner.

    Args:
        ulke_kodu: 'TR' / 'GR' / 'BG'
        dil_kodu:  GNews dil kodu, 'tr' / 'el' / 'bg'
        haber_sayisi: Kaç haber istendi
    """
    site_sorgusu = gnews_site_sorgusu(ulke_kodu)
    if not site_sorgusu:
        print(f"⚠️  {ulke_kodu} için whitelist boş — atlanıyor")
        return []

    try:
        gn = GNews(
            language=dil_kodu,
            country=ulke_kodu,
            period="1d",
            max_results=haber_sayisi * 2,  # filtrelemeden sonra elimizde yeterli kalsın
        )
        # site:trthaber.com OR site:ntv.com.tr OR ...
        haberler = gn.get_news(site_sorgusu)
        return (haberler or [])[:haber_sayisi]
    except Exception as e:
        print(f"⚠️  GNews hatası ({ulke_kodu}): {e}")
        return []


# ============================================================
# DEEP URL RESOLVER (v11'den birebir)
# ============================================================
def deep_resolve_url(gnews_url: Optional[str]) -> Optional[str]:
    """
    Google News'in 'news.google.com/articles/...' URL'sini alır,
    gerçek haber sayfasının URL'sine çözer.

    Strateji:
      1. googlenewsdecoder kütüphanesi (varsa, en güvenilir)
      2. requests ile takip et + final URL'yi al
      3. Sayfadaki 'data-n-au' attribute'unu ara (Google'ın gizli linki)
    """
    if not gnews_url:
        return None

    # 1) Decoder kütüphanesi
    if DECODER_VAR:
        try:
            sonuc = gnewsdecoder(gnews_url, interval=1)
            if sonuc and sonuc.get("status") and sonuc.get("decoded_url"):
                decoded = sonuc["decoded_url"]
                if not _google_url_mu(decoded):
                    return decoded
        except Exception:
            pass

    # 2) HTTP redirect takibi
    try:
        cevap = requests.get(
            gnews_url, headers=config.HEADERS, timeout=15, allow_redirects=True
        )
        final_url = cevap.url
        if not _google_url_mu(final_url):
            return final_url

        # 3) Sayfa içinde 'data-n-au' linki ara
        soup = BeautifulSoup(cevap.text, "html.parser")
        for tag in soup.find_all(attrs={"data-n-au": True}):
            aday = tag.get("data-n-au")
            if aday and not _google_url_mu(aday):
                return aday
    except Exception:
        pass

    return None


# ============================================================
# KAPAK FOTOĞRAFI BULUCU (v11'den birebir)
# ============================================================
def kapak_fotografi_url_bul(gercek_url: str) -> Optional[str]:
    """
    Bir haber sayfasının kapak/öne çıkan resmini bulur.
      1. newspaper3k'ın 'top_image' özelliği (varsa)
      2. og:image / twitter:image meta etiketleri
    """
    if not gercek_url or _google_url_mu(gercek_url):
        return None

    # 1) newspaper3k
    if NEWSPAPER_VAR:
        try:
            article = Article(gercek_url, language="en")
            article.download()
            article.parse()
            if article.top_image and not _google_url_mu(article.top_image):
                return article.top_image
        except Exception:
            pass

    # 2) Meta etiketleri
    try:
        cevap = requests.get(
            gercek_url, headers=config.HEADERS, timeout=15, allow_redirects=True
        )
        if cevap.status_code != 200:
            return None
        soup = BeautifulSoup(cevap.text, "html.parser")
        meta_secimleri = [
            {"property": "og:image"},
            {"name": "og:image"},
            {"property": "twitter:image"},
            {"name": "twitter:image"},
        ]
        for secim in meta_secimleri:
            tag = soup.find("meta", attrs=secim)
            if tag and tag.get("content"):
                aday = tag["content"]
                # Göreceli URL'leri normalize et
                if aday.startswith("//"):
                    aday = "https:" + aday
                elif aday.startswith("/"):
                    aday = urljoin(gercek_url, aday)
                if not _google_url_mu(aday):
                    return aday
    except Exception:
        pass

    return None


def kapak_fotografi_indir(gercek_url: str, kayit_yolu: Path) -> bool:
    """Asıl resim URL'sini bulur, indirip diske yazar."""
    if not gercek_url or _google_url_mu(gercek_url):
        return False
    foto_url = kapak_fotografi_url_bul(gercek_url)
    if not foto_url or _google_url_mu(foto_url):
        return False
    try:
        cevap = requests.get(
            foto_url, headers=config.HEADERS, timeout=20, stream=True
        )
        if cevap.status_code != 200:
            return False
        kayit_yolu = Path(kayit_yolu)
        kayit_yolu.parent.mkdir(parents=True, exist_ok=True)
        with open(kayit_yolu, "wb") as f:
            for chunk in cevap.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        # Boyut kontrolü — çok küçükse muhtemelen placeholder/logo
        if kayit_yolu.stat().st_size < 2048:
            kayit_yolu.unlink()
            return False
        return True
    except Exception:
        return False


# ============================================================
# CLAUDE AI — ÇOK DİLLİ MAKALE ÜRETİCİ (v11 promptu, sıkı kurallar)
# ============================================================
def haberi_cok_dilli_uret(
    orj_baslik: str,
    orj_ozet: str,
    ulke_bilgi: Dict[str, Any],
) -> Dict[str, str]:
    """
    Orijinal haber başlığı + özetinden, ulke_bilgi['uretilecek_diller']
    listesindeki HER dil için: başlık + özet + uzun metin + hashtag üretir.

    v11'deki sıkı sistem promptu birebir korundu. KRİTİK KURAL bölümü
    hata yapan eski çıktıları engellemek içindir, KOMPROMI YOK.
    """
    uretilecekler = [d.lower() for d in ulke_bilgi["uretilecek_diller"]]

    # JSON şemasını dinamik üret (her dil için 4 alan)
    schema_fields = {}
    for d in uretilecekler:
        schema_fields[f"{d}_baslik"] = f"1 striking sentence headline in {d}"
        schema_fields[f"{d}_ozet"] = f"3-4 sentence clean summary in {d}"
        schema_fields[f"{d}_uzun_metin"] = (
            f"4-5 paragraph comprehensive professional news article body in {d}"
        )
        schema_fields[f"{d}_hashtags"] = f"5-8 relevant hashtags in {d}"

    prompt = f"""You are a professional multilingual editor for the Balkanlardan News Agency.
Analyze the following news item:
Original Headline: {orj_baslik}
Original Context: {orj_ozet if orj_ozet else "No extra context available."}

You MUST generate a symmetric response containing data for ALL requested languages: {', '.join(uretilecekler)}.

KRİTİK KURAL (CRITICAL RULE): Türkçe metinler üretirken ülke isimlerini DAİMA "Türkiye", "Yunanistan" ve "Bulgaristan" olarak kullan. Asla "Bulgarya", "Yunan" (ülke ismi olarak), "Türkiyye" vb. gayriresmi veya hatalı çeviriler yapma. Doğru Türkçe ülke isimleri:
- Bulgaria → Bulgaristan (ASLA "Bulgarya" KULLANMA)
- Greece → Yunanistan (ASLA sadece "Yunan" kullanma, "Yunanistan" kullan)
- Turkey → Türkiye
- Bulgarian (sıfat) → Bulgar
- Greek (sıfat) → Yunan
- Turkish (sıfat) → Türk

When writing in English, use: Bulgaria, Greece, Turkey.
When writing in Greek (EL), use: Βουλγαρία, Ελλάδα, Τουρκία.
When writing in Bulgarian (BG), use: България, Гърция, Турция.

Output STRICTLY a single flat valid JSON object containing exactly these fields:
{json.dumps(schema_fields, indent=2)}

Do not include markdown code block syntax outside the pure raw JSON string."""

    try:
        cevap = _claude_client().messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        metin = cevap.content[0].text.strip()

        # Bazen model yine de ```json``` fence'i ekler — temizle
        if metin.startswith("```"):
            metin = metin.split("```")[1]
            if metin.startswith("json"):
                metin = metin[4:]
            metin = metin.strip()

        return json.loads(metin)

    except Exception as e:
        print(f"   ⚠️  AI Makale Motoru Hatası: {e}")
        # Fallback: orijinal başlık/özeti her dile kopyala
        fallback = {}
        for d in uretilecekler:
            fallback[f"{d}_baslik"] = orj_baslik
            fallback[f"{d}_ozet"] = orj_ozet or orj_baslik
            fallback[f"{d}_uzun_metin"] = orj_ozet or orj_baslik
            fallback[f"{d}_hashtags"] = "#Balkanlardan #News"
        return fallback


# ============================================================
# TEK BİR ÜLKEYİ İŞLE
# ============================================================
def _ulkeyi_isle(
    ulke_kodu: str,
    haber_sayisi: int,
    taslak_kok: Path,
    log_callback: Callable[[str], None],
) -> int:
    """
    Bir ülkenin TÜM adımlarını yapar:
      1) Whitelisted GNews araması
      2) Her haber için deep resolve + kapak indir + AI çeviri
      3) JSON taslağını storage üzerinden kaydet

    Returns: başarıyla işlenen haber sayısı
    """
    ulke_bilgi = config.ULKELER.get(ulke_kodu)
    if not ulke_bilgi:
        return 0

    ad_tr = ulke_bilgi["ad_tr"]
    ulke_klasor_adi = ulke_bilgi["klasor"]

    log_callback(f"🌍 {ad_tr} — {len(kaynaklari_getir(ulke_kodu))} whitelisted kaynak taranıyor")

    # storage'a klasör kurdur
    ulke_klasor, kapak_klasor = storage.ulke_klasoru_olustur(taslak_kok, ulke_klasor_adi)

    # 1) Haberleri çek
    haberler = ulke_haberlerini_cek(ulke_kodu, ulke_bilgi["dil"], haber_sayisi)
    if not haberler:
        log_callback(f"   ⚠️  {ad_tr}: haber bulunamadı")
        return 0

    log_callback(f"   📰 {len(haberler)} haber bulundu, işleniyor...")
    basarili = 0

    for i, haber in enumerate(haberler, start=1):
        try:
            yayinci = haber.get("publisher", {}).get("title", "Bilinmeyen")
            orj_baslik = haber.get("title", "")
            orj_ozet = haber.get("description", "")

            log_callback(f"   [{i:02d}/{len(haberler)}] {yayinci}: {orj_baslik[:60]}...")

            # 2) Gerçek URL'ye çöz
            gercek_url = deep_resolve_url(haber.get("url", "")) or haber.get("url", "")

            # 3) Kapak fotoğrafı indir
            kapak_dosya_adi = f"{ulke_klasor_adi}_Haber_{i:02d}_kapak.jpg"
            kapak_yolu = kapak_klasor / kapak_dosya_adi
            kapak_basari = kapak_fotografi_indir(gercek_url, kapak_yolu)
            if kapak_basari:
                log_callback("        🖼️  Kapak indirildi")
            else:
                log_callback("        ⚠️  Kapak indirilemedi")

            # 4) Claude ile çok dilli içerik üret
            log_callback(f"        🤖 Çeviri üretiliyor ({', '.join(ulke_bilgi['uretilecek_diller'])})")
            icerik = haberi_cok_dilli_uret(orj_baslik, orj_ozet, ulke_bilgi)

            # 5) Taslak JSON'unu storage üzerinden inşa et ve kaydet
            taslak = storage.yeni_taslak_olustur(
                ulke_bilgi=ulke_bilgi,
                haber_index=i,
                icerik=icerik,
                gercek_url=gercek_url,
                orj_baslik=orj_baslik,
                orj_ozet=orj_ozet,
                kapak_dosya_adi=kapak_dosya_adi if kapak_basari else None,
                kapak_basari=kapak_basari,
                kaynak="otomatik",
            )
            json_dosya = ulke_klasor / f"{ulke_klasor_adi}_Haber_{i:02d}.json"
            if storage.taslak_kaydet(taslak, json_dosya):
                log_callback(f"        ✅ Kaydedildi: {json_dosya.name}")
                basarili += 1
            else:
                log_callback("        ⚠️  Kaydedilemedi")

        except Exception as e:
            log_callback(f"        ❌ Haber {i} hatası: {e}")
            if config.DEBUG:
                traceback.print_exc()
            continue

    log_callback(f"   ✅ {ad_tr}: {basarili}/{len(haberler)} haber kaydedildi")
    return basarili


# ============================================================
# DIŞ API — app.py'nin çağırdığı asıl fonksiyon
# ============================================================
def toplu_haber_cek(
    ulke_kodlari: List[str],
    haber_sayisi: int,
    log_callback: Callable[[str], None] = print,
    ilerleme_callback: Callable[[int, str], None] = lambda y, e: None,
) -> Dict[str, Any]:
    """
    Seçilen ülkelerden whitelisted GNews + AI çeviri + JSON kayıt.

    Returns:
        {
          'klasor_adi': 'Taslaklar_23_Mayis_2026',
          'klasor_yolu': '/path/to/Arşiv/Taslaklar_...',
          'toplam_basarili': 27,
          'ulke_basina': {'TR': 10, 'GR': 9, 'BG': 8},
        }
    """
    baslangic = datetime.now()
    taslak_kok, klasor_adi = storage.bugunku_taslak_klasoru()
    log_callback(f"📁 Klasör: {klasor_adi}")

    sonuc = {
        "klasor_adi": klasor_adi,
        "klasor_yolu": str(taslak_kok),
        "toplam_basarili": 0,
        "ulke_basina": {},
    }

    toplam = len(ulke_kodlari)
    for sira, ulke_kodu in enumerate(ulke_kodlari):
        yuzde = int((sira / toplam) * 100) if toplam else 0
        ulke_adi = config.ULKELER.get(ulke_kodu, {}).get("ad_tr", ulke_kodu)
        ilerleme_callback(yuzde, f"{ulke_adi} işleniyor ({sira + 1}/{toplam})...")

        try:
            basarili = _ulkeyi_isle(ulke_kodu, haber_sayisi, taslak_kok, log_callback)
        except Exception as e:
            log_callback(f"❌ {ulke_adi} hatası: {e}")
            if config.DEBUG:
                traceback.print_exc()
            basarili = 0

        sonuc["ulke_basina"][ulke_kodu] = basarili
        sonuc["toplam_basarili"] += basarili

    sure = (datetime.now() - baslangic).total_seconds() / 60
    log_callback(f"\n🎉 Bitti: {sonuc['toplam_basarili']} taslak, süre: {sure:.1f} dk")
    return sonuc


# ============================================================
# BAĞLANTI TESTİ
# ============================================================
def claude_baglanti_testi() -> tuple[bool, str]:
    """Ayarlar sekmesindeki 'Claude'u test et' butonu için."""
    try:
        cevap = _claude_client().messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=10,
            messages=[{"role": "user", "content": "ping"}],
        )
        return True, f"✅ Bağlantı OK — model: {cevap.model}"
    except Exception as e:
        return False, f"❌ Hata: {e}"
