"""
=================================================================
modules/ozel_haber.py — Manuel / Saha Haber Üretici
=================================================================
Kullanıcının yapıştırdığı ham notları Claude'a gönderip
profesyonel gazetecilik diliyle 4 dilli makaleye çevirir.
=================================================================
"""
from pathlib import Path
from typing import Optional, Any, Dict

from . import config
from . import storage


def haber_uret(
    ham_metin: str,
    ulke_kodu: str,
    ek_baglam: str = "",
    kapak_dosya: Optional[Any] = None,  # streamlit UploadedFile
) -> Dict[str, Any]:
    """
    Ham notlardan 4 dilli haber taslağı üretir ve diske kaydeder.

    Returns:
        {
          "json_yolu": Path,
          "diller": {"TR": {"baslik": "..."}, ...}
        }
    """
    ulke_bilgi = config.ULKELER.get(ulke_kodu)
    if not ulke_bilgi:
        raise ValueError(f"Bilinmeyen ülke kodu: {ulke_kodu}")

    # 1) Klasörü hazırla
    taslak_kok, _ = storage.bugunku_taslak_klasoru()
    ulke_klasor, kapak_klasor = storage.ulke_klasoru_olustur(taslak_kok, ulke_bilgi["klasor"])

    # 2) Mevcut OZEL haberlere göre sıra numarası ver
    mevcut_ozel = list(ulke_klasor.glob(f"{ulke_bilgi['klasor']}_Ozel_*.json"))
    sira = len(mevcut_ozel) + 1

    # 3) Kapak fotoğrafını kaydet (varsa)
    kapak_dosya_adi = None
    kapak_basari = False
    if kapak_dosya is not None:
        try:
            uzanti = Path(kapak_dosya.name).suffix.lower() or ".jpg"
            kapak_dosya_adi = f"{ulke_bilgi['klasor']}_Ozel_{sira:02d}_kapak{uzanti}"
            kapak_yolu = kapak_klasor / kapak_dosya_adi
            with open(kapak_yolu, "wb") as f:
                f.write(kapak_dosya.getbuffer())
            kapak_basari = True
        except Exception:
            kapak_basari = False

    # 4) Claude'a gönder — TODO: gerçek prompt ve API çağrısı
    # icerik = _ham_metinden_makale_uret(ham_metin, ek_baglam, ulke_bilgi)
    icerik = {}  # placeholder

    # 5) Taslak JSON oluştur ve kaydet
    taslak = storage.yeni_taslak_olustur(
        ulke_bilgi=ulke_bilgi,
        haber_index=sira,
        icerik=icerik,
        gercek_url="",  # özel haberde dış kaynak yok
        orj_baslik=ham_metin[:120],
        orj_ozet=ek_baglam or ham_metin[:300],
        kapak_dosya_adi=kapak_dosya_adi,
        kapak_basari=kapak_basari,
        kaynak="ozel",
    )

    json_yolu = ulke_klasor / f"{ulke_bilgi['klasor']}_Ozel_{sira:02d}.json"
    storage.taslak_kaydet(taslak, json_yolu)

    return {
        "json_yolu": json_yolu,
        "diller": taslak["diller"],
    }
