"""
=================================================================
modules/routing.py — YAYIN YÖNLENDİRME MATRİSİ (v3 — Global)
=================================================================
Yayın stratejisi değişti: TEK GLOBAL HESAP modeli.

Bu modülün cevapladığı sorular:
  • Bu (ülke, dil) postu hangi WordPress kategorilerine girecek?
  • Hangi sosyal platformlar şu an yapılandırılmış ve aktif?
  • Çok dilli caption nasıl tek bloğa örülür?

Kural: BAŞKA hiçbir modül "kim nereye?" kararı vermez.
       Hepsi bu modülün API'sini çağırır.
=================================================================
"""
import os
from dataclasses import dataclass
from typing import Dict, List

from . import config


# ============================================================
# PLATFORM TANIMLARI
# ============================================================
@dataclass(frozen=True)
class PlatformTanimi:
    kod: str                    # 'IG'
    ad: str                     # 'Instagram'
    ikon: str                   # 📷
    env_alanlari: List[str]     # ['TOKEN', 'USER_ID']  → .env'den okunacak


# Her platformun ihtiyaç duyduğu .env alanları:
#   ana token (ilk eleman) BOŞ ise platform DEVRE DIŞI sayılır.
PLATFORMLAR: Dict[str, PlatformTanimi] = {
    "IG": PlatformTanimi("IG", "Instagram",       "📷", ["TOKEN", "USER_ID"]),
    "X":  PlatformTanimi("X",  "X (Twitter)",     "🐦", ["API_KEY", "API_SECRET", "ACCESS_TOKEN", "ACCESS_SECRET"]),
    "TT": PlatformTanimi("TT", "TikTok",          "🎵", ["TOKEN"]),
    "FB": PlatformTanimi("FB", "Facebook",        "👥", ["TOKEN", "PAGE_ID"]),
    "YT": PlatformTanimi("YT", "YouTube Shorts",  "▶️", ["TOKEN"]),
}


# ============================================================
# GLOBAL HESAP TEMSİLİ
# ============================================================
@dataclass
class GlobalHesap:
    platform_kodu: str           # 'IG'
    platform_adi: str            # 'Instagram'
    ikon: str                    # 📷
    aktif: bool                  # ana token doluysa True
    env_anahtarlari: Dict[str, str]   # {'TOKEN': '...', 'USER_ID': '...'}

    @property
    def ana_env_adi(self) -> str:
        """Birincil .env değişkeni — UI'da 'hangi token' diye gösterilir."""
        ilk_alan = PLATFORMLAR[self.platform_kodu].env_alanlari[0]
        return f"{self.platform_kodu}_GLOBAL_{ilk_alan}"

    @property
    def kisa_etiket(self) -> str:
        return f"{self.ikon} {self.platform_adi}"


# ============================================================
# .ENV OKUMA
# ============================================================
def _env_okuyucu(platform_kodu: str, alan: str) -> str:
    """`.env`'den `{PLATFORM}_GLOBAL_{ALAN}` değerini okur."""
    anahtar = f"{platform_kodu}_GLOBAL_{alan}"
    return os.getenv(anahtar, "").strip()


def global_hesaplar(sadece_aktifler: bool = True) -> List[GlobalHesap]:
    """
    Tüm platformları tarar, her biri için .env değerlerini okur,
    GlobalHesap nesnesi inşa eder.

    Args:
        sadece_aktifler: True ise sadece ana token'ı dolu olanları döndürür.
                         False ise hepsini (UI'da gri olanları da göstermek için).
    """
    sonuc: List[GlobalHesap] = []
    for plat_kod, plat in PLATFORMLAR.items():
        env_degerleri: Dict[str, str] = {}
        for alan in plat.env_alanlari:
            env_degerleri[alan] = _env_okuyucu(plat_kod, alan)

        ana_token = env_degerleri.get(plat.env_alanlari[0], "")
        aktif = bool(ana_token)

        if sadece_aktifler and not aktif:
            continue

        sonuc.append(GlobalHesap(
            platform_kodu=plat_kod,
            platform_adi=plat.ad,
            ikon=plat.ikon,
            aktif=aktif,
            env_anahtarlari=env_degerleri,
        ))
    return sonuc


def aktif_platform_sayisi() -> int:
    """Sidebar metriği için: kaç platform yapılandırılmış?"""
    return len(global_hesaplar(sadece_aktifler=True))


# ============================================================
# WORDPRESS KATEGORİ EŞLEMESİ (değişmedi — WP'de hâlâ dil kategorisi var)
# ============================================================
def wp_kategorileri(ulke_kodu: str, dil_kodu: str) -> List[str]:
    """
    Bir haberin WordPress'e atılacağı kategoriler:
      ülke adı + dil adı = 2 kategori.

    Örnek (GR, EL) → ['Yunanistan', 'Ελληνικά']
    """
    ulke_bilgi = config.ULKELER.get(ulke_kodu)
    if not ulke_bilgi:
        return []
    return [ulke_bilgi["ad_tr"], config.DIL_AYARLARI[dil_kodu]["wp_cat"]]


# ============================================================
# 4 DİLLİ CAPTION ÖRÜCÜSÜ
# ============================================================
# Her dil için bayrak emojisi — caption'ın görsel ayraçları.
DIL_BAYRAKLARI: Dict[str, str] = {
    "TR": "🇹🇷",
    "EN": "🇬🇧",
    "EL": "🇬🇷",
    "BG": "🇧🇬",
}

# Caption başı için ayraç — postu profesyonel ve okunaklı tutar.
CAPTION_AYRAC = "━━━━━━━━━━━━━━━"


def cok_dilli_caption_or(
    diller_data: Dict[str, Dict[str, str]],
    ortak_hashtagler: str = "#Balkanlardan #BalkanlardanHaber",
    sira: List[str] = None,
    karakter_limiti: int = 2200,
) -> str:
    """
    Bir haberin tüm dillerini TEK caption metni olarak örer.

    Args:
        diller_data: {'TR': {'baslik': ..., 'ozet': ..., 'hashtags': ...}, 'EN': {...}}
        ortak_hashtagler: Postun en altına eklenecek genel marka hashtag'leri
        sira: Dillerin sırası. Default: TR → EN → EL → BG
        karakter_limiti: Instagram ~2200, TikTok ~2200, X için ayrı kısa versiyon
                         istersek `kisa_caption_or()` kullanılır.

    Format:
        🇹🇷 [Başlık TR]
        [Özet TR]
        [Hashtags TR]
        ━━━━━━━━━━━━━━━
        🇬🇧 [Başlık EN]
        ...
        ━━━━━━━━━━━━━━━
        #Balkanlardan #BalkanlardanHaber
    """
    if sira is None:
        sira = ["TR", "EN", "EL", "BG"]

    bloklar: List[str] = []
    for dil_kodu in sira:
        dil_data = diller_data.get(dil_kodu)
        if not dil_data:
            continue
        baslik = (dil_data.get("baslik") or "").strip()
        if not baslik:
            continue  # bu dil yoksa atla

        ozet = (dil_data.get("ozet") or "").strip()
        ozel_tags = (dil_data.get("hashtags") or "").strip()
        bayrak = DIL_BAYRAKLARI.get(dil_kodu, "🌐")

        parcalar = [f"{bayrak} {baslik}"]
        if ozet:
            parcalar.append(ozet)
        if ozel_tags:
            parcalar.append(ozel_tags)
        bloklar.append("\n\n".join(parcalar))

    govde = f"\n\n{CAPTION_AYRAC}\n\n".join(bloklar)
    tam = f"{govde}\n\n{CAPTION_AYRAC}\n\n{ortak_hashtagler}".strip()

    # Limit aşarsa, hashtag'leri koru, gövdeyi kısalt
    if len(tam) > karakter_limiti:
        kullanilabilir = karakter_limiti - len(ortak_hashtagler) - len(CAPTION_AYRAC) - 10
        kisa_govde = govde[:kullanilabilir].rsplit(" ", 1)[0] + "…"
        tam = f"{kisa_govde}\n\n{CAPTION_AYRAC}\n\n{ortak_hashtagler}"

    return tam


def kisa_caption_or(
    diller_data: Dict[str, Dict[str, str]],
    karakter_limiti: int = 280,
) -> str:
    """
    X (Twitter) gibi sıkı karakter limitli platformlar için.
    Sadece TR başlığı + EN başlığı + 1-2 hashtag.

    Format:  🇹🇷 [TR başlık] · 🇬🇧 [EN başlık] #Balkanlardan
    """
    tr_baslik = (diller_data.get("TR", {}).get("baslik") or "").strip()
    en_baslik = (diller_data.get("EN", {}).get("baslik") or "").strip()
    suffix = " #Balkanlardan"

    if tr_baslik and en_baslik:
        aday = f"🇹🇷 {tr_baslik} · 🇬🇧 {en_baslik}{suffix}"
    elif tr_baslik:
        aday = f"🇹🇷 {tr_baslik}{suffix}"
    elif en_baslik:
        aday = f"🇬🇧 {en_baslik}{suffix}"
    else:
        # Son çare: ilk dolu dil neyse onu al
        for d in ["EL", "BG"]:
            b = (diller_data.get(d, {}).get("baslik") or "").strip()
            if b:
                aday = f"{DIL_BAYRAKLARI[d]} {b}{suffix}"
                break
        else:
            aday = "Balkanlardan Haber"

    if len(aday) > karakter_limiti:
        aday = aday[: karakter_limiti - 1] + "…"
    return aday
