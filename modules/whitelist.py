"""
=================================================================
modules/whitelist.py — Güvenilir Haber Kaynakları
=================================================================
GNews aramaları SADECE bu domain'lerle sınırlı tutulur.
Yeni bir kaynak eklemek için ilgili ülkenin listesine domain ekle.

Liste, ülke kodları (TR/GR/BG) -> domain listesi şeklinde tutulur.
GNews 'site:' operatörünü desteklediği için, arama sorgusunu
buradan inşa ederiz: "site:trt.net.tr OR site:cnnturk.com ..."
=================================================================
"""
from typing import Dict, List

WHITELIST: Dict[str, List[str]] = {
    "TR": [
        "trt.net.tr",
        "trthaber.com",
        "cnnturk.com",
        "ntv.com.tr",
        "aa.com.tr",          # Anadolu Ajansı
        "hurriyet.com.tr",
        "milliyet.com.tr",
        "sabah.com.tr",
        "dailysabah.com",     # English yayın
    ],
    "GR": [
        "skai.gr",
        "amna.gr",            # Atina-Makedonya Haber Ajansı
        "protothema.gr",
        "kathimerini.gr",
        "ert.gr",             # ERT — devlet yayıncısı
        "naftemporiki.gr",
        "ekathimerini.com",   # English yayın
    ],
    "BG": [
        "bntnews.bg",         # BNT — devlet yayıncısı
        "dnevnik.bg",
        "nova.bg",
        "bnr.bg",             # Bulgar Ulusal Radyo
        "24chasa.bg",
        "mediapool.bg",
        "novinite.com",       # English yayın
    ],
}


def kaynaklari_getir(ulke_kodu: str) -> List[str]:
    """Bir ülkenin tüm whitelisted domain'lerini döndürür."""
    return WHITELIST.get(ulke_kodu.upper(), [])


def gnews_site_sorgusu(ulke_kodu: str) -> str:
    """
    GNews'e gönderilecek 'site:' operatörü ile filtrelenmiş sorgu üretir.

    Örnek çıktı (TR için):
      'site:trt.net.tr OR site:cnnturk.com OR site:ntv.com.tr OR ...'
    """
    domainler = kaynaklari_getir(ulke_kodu)
    if not domainler:
        return ""
    return " OR ".join(f"site:{d}" for d in domainler)
