"""
=================================================================
Balkanlardan Newsroom CMS — modules paketi
=================================================================
Bu paket, Streamlit panelinin tüm iş mantığını barındırır.
`app.py` orkestra şefidir; iş kararlarını bu modüller verir.

Paket içi import deseni:
  from . import config            (kardeş modüle göreceli erişim)
  from .whitelist import KAYNAKLAR

Paket dışından (örn. app.py veya testler):
  from modules import config
  from modules.routing import wp_kategorileri

İki desen de doğrudur ve birbirini bozmaz.
=================================================================
"""

__version__ = "3.0.0"
__author__ = "Balkanlardan Haber"

# Public API — `from modules import *` ile dışarı açılanlar.
# Streamlit ortamında 'import *' kullanmıyoruz ama tip ipuçları
# ve IDE auto-complete için referans listesi olarak değerlidir.
__all__ = [
    "config",
    "storage",
    "whitelist",
    "routing",
    "haber_motoru",
    "ozel_haber",
    "medya_uretimi",
    "yayin_motoru",
    "wp_motoru",
    "sosyal_motoru",
]
