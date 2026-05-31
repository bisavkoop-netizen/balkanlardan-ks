"""
=================================================================
modules/config.py — Merkezi Yapılandırma (v4 — Balkan Kültür Platformu)
=================================================================
DEĞİŞİKLİKLER v3 → v4:
  • ULKELER: 3 → 10 Balkan ülkesi (TR, GR, BG, RS, BA, MK, HR, ME, AL, XK, RO, SI)
  • DIL_AYARLARI: 4 → 10 dil (TR, EN, EL, BG, SR, HR, BS, MK, SQ, RO, SL)
  • İçerik formatları: VIDEO, CAROUSEL, BLOG → format-bazlı kategori
  • SCHEDULER_SAATLERI eklendi (APScheduler için)
=================================================================
"""
import os
from pathlib import Path
from dotenv import load_dotenv

PROJE_KOK = Path(__file__).resolve().parent.parent
load_dotenv(PROJE_KOK / ".env")


def _zorunlu_env(anahtar: str) -> str:
    deger = os.getenv(anahtar, "").strip()
    if not deger:
        raise RuntimeError(
            f"❌ .env dosyasında '{anahtar}' tanımlı değil veya boş."
        )
    return deger


# ============================================================
# .ENV'DEN GELEN SIRLAR
# ============================================================
ANTHROPIC_API_KEY = _zorunlu_env("ANTHROPIC_API_KEY")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")  # Sonnet — küratör tonu daha iyi

WP_URL = _zorunlu_env("WP_URL")
WP_USER = _zorunlu_env("WP_USER")
WP_APP_PASSWORD = _zorunlu_env("WP_APP_PASSWORD")

APP_USERNAME = os.getenv("APP_USERNAME", "")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")

# YENİ: Günlük üretim hedefi (otomatik çalışma için)
ICERIK_SAYISI_PER_DONGUR = int(os.getenv("ICERIK_SAYISI_PER_DONGUR", "5"))
TASLAK_KOK = os.getenv("TASLAK_KOK", "Arşiv")
LOGO_DOSYASI = os.getenv("LOGO_DOSYASI", "balkanlardan_logo.jpeg")
DEBUG = os.getenv("DEBUG", "False").lower() == "true"

# YENİ: APScheduler saatleri (24h format, virgülle ayrılmış)
SCHEDULER_SAATLERI = os.getenv("SCHEDULER_SAATLERI", "10:00,14:00").split(",")
SCHEDULER_AKTIF = os.getenv("SCHEDULER_AKTIF", "False").lower() == "true"

# ============================================================
# PROJE YOLLARI
# ============================================================
ASSETS_KLASORU = PROJE_KOK / "assets"
ARSIV_KLASORU = PROJE_KOK / TASLAK_KOK
LOGO_TAM_YOLU = ASSETS_KLASORU / LOGO_DOSYASI

# ============================================================
# DİL AYARLARI (TTS sesleri + WP kategori adları)
# Genişletildi: 4 → 10 dil
# ============================================================
DIL_AYARLARI = {
    "TR": {"ses": "tr-TR-AhmetNeural",      "ad": "Türkçe",     "etiket_kisa": "TR", "wp_cat": "Türkçe",     "tts_var": True},
    "EN": {"ses": "en-US-ChristopherNeural", "ad": "English",    "etiket_kisa": "EN", "wp_cat": "English",    "tts_var": True},
    "EL": {"ses": "el-GR-NestorasNeural",   "ad": "Ελληνικά",   "etiket_kisa": "GR", "wp_cat": "Ελληνικά",   "tts_var": True},
    "BG": {"ses": "bg-BG-BorislavNeural",   "ad": "Български",  "etiket_kisa": "BG", "wp_cat": "Български",  "tts_var": True},
    # YENİ DİLLER — kültür dönüşümünün getirdikleri
    "SR": {"ses": "sr-RS-NicholasNeural",   "ad": "Srpski",     "etiket_kisa": "SR", "wp_cat": "Srpski",     "tts_var": True},
    "HR": {"ses": "hr-HR-SreckoNeural",     "ad": "Hrvatski",   "etiket_kisa": "HR", "wp_cat": "Hrvatski",   "tts_var": True},
    "BS": {"ses": "bs-BA-GoranNeural",      "ad": "Bosanski",   "etiket_kisa": "BS", "wp_cat": "Bosanski",   "tts_var": True},
    "MK": {"ses": "mk-MK-AleksandarNeural", "ad": "Македонски", "etiket_kisa": "MK", "wp_cat": "Македонски", "tts_var": True},
    "SQ": {"ses": "sq-AL-IlirNeural",       "ad": "Shqip",      "etiket_kisa": "AL", "wp_cat": "Shqip",      "tts_var": True},
    "RO": {"ses": "ro-RO-EmilNeural",       "ad": "Română",     "etiket_kisa": "RO", "wp_cat": "Română",     "tts_var": True},
    "SL": {"ses": "sl-SI-RokNeural",        "ad": "Slovenščina","etiket_kisa": "SL", "wp_cat": "Slovenščina","tts_var": True},
}

# Maliyeti kontrol altında tutmak için: her üretimde zorunlu diller + 
# içeriğin kaynak ülkesinin ana dili. Geri kalanı UI'dan seçilir.
ZORUNLU_DILLER = ["TR", "EN"]

# ============================================================
# ÜLKE KONFİGÜRASYONU — 10 BALKAN ÜLKESİ
# ============================================================
# Her ülke için:
#   • orijinal_dil_kodu: o ülkenin ana dili (zorunlu çevrilir)
#   • varsayilan_diller: bu ülkenin içeriği için varsayılan çeviri seti
#   • komsu_diller: küratoryal eşleme — örn. Bosna içeriği HR ve SR'a da gitsin
#   • badgeler: video üstündeki dil-spesifik etiket
# ============================================================
ULKELER = {
    "TR": {
        "kod": "TR", "ad_tr": "Türkiye", "ad_en": "Turkey", "ad_orj": "Türkiye",
        "klasor": "Turkiye",
        "orijinal_dil_kodu": "TR",
        "varsayilan_diller": ["TR", "EN", "EL", "BG"],
        "komsu_diller": ["EL", "BG"],  # Trakya kültürü için
        "badgeler": {
            "TR": "TÜRKİYE", "EN": "TURKEY", "EL": "ΤΟΥΡΚΙΑ",
            "BG": "ТУРЦИЯ", "SR": "TURSKA", "HR": "TURSKA", "BS": "TURSKA",
            "MK": "ТУРЦИЈА", "SQ": "TURQIA", "RO": "TURCIA", "SL": "TURČIJA",
        },
    },
    "GR": {
        "kod": "GR", "ad_tr": "Yunanistan", "ad_en": "Greece", "ad_orj": "Ελλάδα",
        "klasor": "Yunanistan",
        "orijinal_dil_kodu": "EL",
        "varsayilan_diller": ["EL", "TR", "EN", "BG"],
        "komsu_diller": ["TR", "BG", "MK", "SQ"],
        "badgeler": {
            "EL": "ΕΛΛΑΔΑ", "TR": "YUNANİSTAN", "EN": "GREECE",
            "BG": "ГЪРЦИЯ", "SR": "GRČKA", "MK": "ГРЦИЈА", "SQ": "GREQIA",
        },
    },
    "BG": {
        "kod": "BG", "ad_tr": "Bulgaristan", "ad_en": "Bulgaria", "ad_orj": "България",
        "klasor": "Bulgaristan",
        "orijinal_dil_kodu": "BG",
        "varsayilan_diller": ["BG", "TR", "EN", "EL"],
        "komsu_diller": ["TR", "EL", "MK", "RO", "SR"],
        "badgeler": {
            "BG": "БЪЛГАРИЯ", "TR": "BULGARİSTAN", "EN": "BULGARIA",
            "EL": "ΒΟΥΛΓΑΡΙΑ", "MK": "БУГАРИЈА", "RO": "BULGARIA",
        },
    },
    "RS": {
        "kod": "RS", "ad_tr": "Sırbistan", "ad_en": "Serbia", "ad_orj": "Србија",
        "klasor": "Sirbistan",
        "orijinal_dil_kodu": "SR",
        "varsayilan_diller": ["SR", "TR", "EN", "BS", "HR"],
        "komsu_diller": ["BS", "HR", "MK", "BG", "SQ", "RO"],
        "badgeler": {
            "SR": "СРБИЈА", "TR": "SIRBİSTAN", "EN": "SERBIA",
            "BS": "SRBIJA", "HR": "SRBIJA", "MK": "СРБИЈА", "SQ": "SERBIA",
        },
    },
    "BA": {
        "kod": "BA", "ad_tr": "Bosna Hersek", "ad_en": "Bosnia & Herzegovina", "ad_orj": "Bosna i Hercegovina",
        "klasor": "Bosna",
        "orijinal_dil_kodu": "BS",
        "varsayilan_diller": ["BS", "TR", "EN", "SR", "HR"],
        "komsu_diller": ["SR", "HR"],
        "badgeler": {
            "BS": "BOSNA I HERCEGOVINA", "TR": "BOSNA HERSEK", "EN": "BOSNIA & HERZEGOVINA",
            "SR": "БОСНА И ХЕРЦЕГОВИНА", "HR": "BOSNA I HERCEGOVINA",
        },
    },
    "MK": {
        "kod": "MK", "ad_tr": "Kuzey Makedonya", "ad_en": "North Macedonia", "ad_orj": "Северна Македонија",
        "klasor": "KuzeyMakedonya",
        "orijinal_dil_kodu": "MK",
        "varsayilan_diller": ["MK", "TR", "EN", "SQ", "BG", "SR"],
        "komsu_diller": ["SQ", "BG", "SR", "EL"],
        "badgeler": {
            "MK": "СЕВЕРНА МАКЕДОНИЈА", "TR": "KUZEY MAKEDONYA", "EN": "NORTH MACEDONIA",
            "SQ": "MAQEDONIA E VERIUT", "BG": "СЕВЕРНА МАКЕДОНИЯ", "SR": "СЕВЕРНА МАКЕДОНИЈА",
        },
    },
    "HR": {
        "kod": "HR", "ad_tr": "Hırvatistan", "ad_en": "Croatia", "ad_orj": "Hrvatska",
        "klasor": "Hirvatistan",
        "orijinal_dil_kodu": "HR",
        "varsayilan_diller": ["HR", "TR", "EN", "SR", "BS", "SL"],
        "komsu_diller": ["SR", "BS", "SL"],
        "badgeler": {
            "HR": "HRVATSKA", "TR": "HIRVATİSTAN", "EN": "CROATIA",
            "SR": "ХРВАТСКА", "BS": "HRVATSKA", "SL": "HRVAŠKA",
        },
    },
    "ME": {
        "kod": "ME", "ad_tr": "Karadağ", "ad_en": "Montenegro", "ad_orj": "Crna Gora",
        "klasor": "Karadag",
        "orijinal_dil_kodu": "SR",  # Karadağca standardı SR ailesinin parçası, TTS için SR kullanıyoruz
        "varsayilan_diller": ["SR", "TR", "EN", "SQ", "BS", "HR"],
        "komsu_diller": ["SR", "BS", "HR", "SQ"],
        "badgeler": {
            "SR": "ЦРНА ГОРА", "TR": "KARADAĞ", "EN": "MONTENEGRO",
            "SQ": "MALI I ZI", "BS": "CRNA GORA", "HR": "CRNA GORA",
        },
    },
    "AL": {
        "kod": "AL", "ad_tr": "Arnavutluk", "ad_en": "Albania", "ad_orj": "Shqipëria",
        "klasor": "Arnavutluk",
        "orijinal_dil_kodu": "SQ",
        "varsayilan_diller": ["SQ", "TR", "EN", "EL", "MK"],
        "komsu_diller": ["EL", "MK", "SR"],
        "badgeler": {
            "SQ": "SHQIPËRIA", "TR": "ARNAVUTLUK", "EN": "ALBANIA",
            "EL": "ΑΛΒΑΝΙΑ", "MK": "АЛБАНИЈА", "SR": "АЛБАНИЈА",
        },
    },
    "XK": {
        "kod": "XK", "ad_tr": "Kosova", "ad_en": "Kosovo", "ad_orj": "Kosova",
        "klasor": "Kosova",
        "orijinal_dil_kodu": "SQ",
        "varsayilan_diller": ["SQ", "TR", "EN", "SR"],
        "komsu_diller": ["SQ", "SR", "MK", "AL"],
        "badgeler": {
            "SQ": "KOSOVA", "TR": "KOSOVA", "EN": "KOSOVO",
            "SR": "КОСОВО",
        },
    },
    "RO": {
        "kod": "RO", "ad_tr": "Romanya", "ad_en": "Romania", "ad_orj": "România",
        "klasor": "Romanya",
        "orijinal_dil_kodu": "RO",
        "varsayilan_diller": ["RO", "TR", "EN", "BG"],
        "komsu_diller": ["BG"],
        "badgeler": {
            "RO": "ROMÂNIA", "TR": "ROMANYA", "EN": "ROMANIA",
            "BG": "РУМЪНИЯ",
        },
    },
    "SI": {
        "kod": "SI", "ad_tr": "Slovenya", "ad_en": "Slovenia", "ad_orj": "Slovenija",
        "klasor": "Slovenya",
        "orijinal_dil_kodu": "SL",
        "varsayilan_diller": ["SL", "TR", "EN", "HR"],
        "komsu_diller": ["HR"],
        "badgeler": {
            "SL": "SLOVENIJA", "TR": "SLOVENYA", "EN": "SLOVENIA",
            "HR": "SLOVENIJA",
        },
    },
}

# ============================================================
# İÇERİK FORMATLARI — Carousel ve Video ayrı pipeline'lar
# ============================================================
FORMATLAR = {
    "video": {
        "ad_tr": "Video / Reels",
        "ikon": "🎬",
        "boyut": (1080, 1920),  # 9:16
        "platformlar": ["IG", "TT", "YT", "FB"],
    },
    "carousel": {
        "ad_tr": "Carousel (5 slayt)",
        "ikon": "🖼️",
        "boyut": (1080, 1350),  # 4:5
        "slayt_sayisi": 5,
        "platformlar": ["IG", "FB"],
    },
    "blog": {
        "ad_tr": "Sadece Blog Yazısı",
        "ikon": "📝",
        "platformlar": [],  # WP-only
    },
}

# ============================================================
# İÇERİK TEMASI ETİKETLERİ — Trend motoru bunlardan birini her içeriğe atar
# ============================================================
ICERIK_TEMALARI = [
    "muzik",          # Türküler, rebetiko, sevdalinka, kaba zurna...
    "yemek",          # Boza, baklava, ćevapi, sarma...
    "mimari",         # Osmanlı çeşmeleri, Avstrougar tarz, manastırlar...
    "gelenek",        # Düğün adetleri, halk dansları, yas ritüelleri
    "tarih_hikaye",   # Anekdotlar, kayıp şehirler, efsaneler
    "el_sanati",      # Kilim, çini, gümüş işçiliği, telkari
    "dil_edebiyat",   # Atasözleri, halk şiiri, eski kelimeler
    "festival",       # Yerel kutlamalar, panayırlar
    "kisi_portre",    # Ünlü Balkan sanatçıları, müzisyenler
    "kayip_kultur",   # Yok olmakta olan zanaatlar/şarkılar/diller
]

# ============================================================
# AY ADLARI (klasör için ASCII-safe)
# ============================================================
AY_ADLARI_KLASOR = {
    1: "Ocak", 2: "Subat", 3: "Mart", 4: "Nisan", 5: "Mayis", 6: "Haziran",
    7: "Temmuz", 8: "Agustos", 9: "Eylul", 10: "Ekim", 11: "Kasim", 12: "Aralik",
}

# ============================================================
# VİDEO / GÖRSEL SABİTLERİ
# ============================================================
W, H = 1080, 1920                   # Reels/TikTok dikey
CAROUSEL_W, CAROUSEL_H = 1080, 1350  # IG carousel oranı
FPS = 24
CERCEVE_MARGIN = 60
CERCEVE_KALINLIK = 4

# Yeni "küratör" paleti — Balkan toprağı tonları, eski mavi-altın yerine
KREM_ARKAPLAN = (244, 238, 222)      # Antika kağıt
KOYU_LACIVERT = (24, 42, 64)         # Logo rengi (#182A40)
SICAK_KIRMIZI = (178, 53, 49)        # Osmanlı kırmızısı
HARDAL_SARI = (212, 168, 64)         # Eski sikke sarısı
KOMUR_GRI = (52, 52, 52)             # Ana metin
YUMUSAK_BEYAZ = (252, 250, 246)      # İkincil arkaplan

# Eski isimlerle geriye uyumluluk (medya_uretimi'nde kullanılıyor olabilir)
KOYU_ARKAPLAN = KOYU_LACIVERT
BEYAZ = YUMUSAK_BEYAZ
ACIK_GRI = (230, 230, 230)
ALTIN = HARDAL_SARI
KIRMIZI_BADGE = SICAK_KIRMIZI

FONT_YOLLARI = [
    str(ASSETS_KLASORU / "fonts" / "DejaVuSans.ttf"),
    str(ASSETS_KLASORU / "fonts" / "DejaVuSans-Bold.ttf"),
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arial.ttf",
]

# ============================================================
# HTTP HEADERS
# ============================================================
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/121.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,tr;q=0.8",
    "Connection": "keep-alive",
}
