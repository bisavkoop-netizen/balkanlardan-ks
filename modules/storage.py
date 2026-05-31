"""
=================================================================
modules/storage.py — Taslak Depolama Katmanı (v4)
=================================================================
DEĞİŞİKLİKLER v3 → v4:
  • yeni_taslak_olustur(): yeni alanlar (hook, carousel_slaytlar, 
    cta_soru, tema, format_listesi) eklendi
  • Geriye uyumluluk: ESKI taslaklar (haber kaynağından gelen) hâlâ
    yüklenir; eksik alanlar default değerle doldurulur
  • Yeni: uretim_gecmisi_listele() — APScheduler tekrarı önlemek için
=================================================================
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional

from . import config


# ============================================================
# KLASÖR YARDIMCILARI (değişmedi)
# ============================================================
def bugunku_taslak_klasoru() -> tuple[Path, str]:
    bugun = datetime.now()
    klasor_adi = (
        f"Taslaklar_{bugun.day:02d}_"
        f"{config.AY_ADLARI_KLASOR[bugun.month]}_{bugun.year}"
    )
    tam_yol = config.ARSIV_KLASORU / klasor_adi
    tam_yol.mkdir(parents=True, exist_ok=True)
    return tam_yol, klasor_adi


def ulke_klasoru_olustur(taslak_ana_klasor: Path, ulke_klasor_adi: str) -> tuple[Path, Path]:
    ulke_yolu = taslak_ana_klasor / ulke_klasor_adi
    kapak_yolu = ulke_yolu / "Kapaklar"
    ulke_yolu.mkdir(parents=True, exist_ok=True)
    kapak_yolu.mkdir(parents=True, exist_ok=True)
    return ulke_yolu, kapak_yolu


# ============================================================
# LİSTELEME
# ============================================================
def taslak_klasorlerini_listele() -> List[Dict[str, Any]]:
    if not config.ARSIV_KLASORU.exists():
        return []
    sonuc = []
    for klasor in config.ARSIV_KLASORU.iterdir():
        if not (klasor.is_dir() and klasor.name.startswith("Taslaklar_")):
            continue
        ulke_sayisi = 0
        haber_sayisi = 0
        for ulke_alt in klasor.iterdir():
            if ulke_alt.is_dir():
                ulke_sayisi += 1
                haber_sayisi += len(list(ulke_alt.glob("*.json")))
        sonuc.append({
            "ad": klasor.name,
            "yol": str(klasor),
            "ulke_sayisi": ulke_sayisi,
            "haber_sayisi": haber_sayisi,
            "degisiklik_zamani": klasor.stat().st_mtime,
        })
    sonuc.sort(key=lambda x: x["degisiklik_zamani"], reverse=True)
    return sonuc


# ============================================================
# YÜKLE / KAYDET / GÜNCELLE / SİL
# ============================================================
def taslaklari_yukle(taslak_klasor: Path) -> List[Dict[str, Any]]:
    taslaklar = []
    taslak_klasor = Path(taslak_klasor)
    for ulke_alt in taslak_klasor.iterdir():
        if not ulke_alt.is_dir():
            continue
        kapak_klasor = ulke_alt / "Kapaklar"
        for json_dosya in sorted(ulke_alt.glob("*.json")):
            try:
                with open(json_dosya, "r", encoding="utf-8") as f:
                    veri = json.load(f)
                veri["_json_yolu"] = str(json_dosya)
                veri["_ulke_klasor_adi"] = ulke_alt.name
                kapak_ad = veri.get("metadata", {}).get("kapak_dosya_adi")
                if kapak_ad:
                    kapak_tam = kapak_klasor / kapak_ad
                    veri["_kapak_tam_yolu"] = str(kapak_tam) if kapak_tam.exists() else None
                else:
                    veri["_kapak_tam_yolu"] = None
                # ↓ YENİ: Geriye uyumluluk — eski taslaklarda olmayan alanları doldur
                _geriye_uyumluluk_doldur(veri)
                taslaklar.append(veri)
            except Exception as e:
                if config.DEBUG:
                    print(f"⚠️  {json_dosya.name} yüklenemedi: {e}")
                continue
    return taslaklar


def _geriye_uyumluluk_doldur(taslak: Dict[str, Any]) -> None:
    """v3 taslaklarında yeni alanları boş değerlerle ekle — kırılmasın."""
    meta = taslak.setdefault("metadata", {})
    meta.setdefault("tema", None)
    meta.setdefault("tohum", None)
    meta.setdefault("format_listesi", ["video"])
    meta.setdefault("kaynak_tipi", "otomatik")

    for dil_kodu, dil_data in taslak.get("diller", {}).items():
        if not isinstance(dil_data, dict):
            continue
        dil_data.setdefault("hook", "")
        dil_data.setdefault("cta_soru", "")
        dil_data.setdefault("carousel_slaytlar", [])


def taslak_kaydet(taslak: Dict[str, Any], dosya_yolu: Path) -> bool:
    try:
        dosya_yolu = Path(dosya_yolu)
        dosya_yolu.parent.mkdir(parents=True, exist_ok=True)
        temiz = {k: v for k, v in taslak.items() if not k.startswith("_")}
        with open(dosya_yolu, "w", encoding="utf-8") as f:
            json.dump(temiz, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def taslak_guncelle(taslak: Dict[str, Any], yeni_diller_dict: Dict[str, Any]) -> bool:
    """Editör masasından gelen güncellemeleri uygula."""
    json_yolu = taslak.get("_json_yolu")
    if not json_yolu:
        return False
    yeni_diller = yeni_diller_dict.get("diller", {})
    for dil_kodu, yeni_data in yeni_diller.items():
        if dil_kodu in taslak.get("diller", {}):
            taslak["diller"][dil_kodu].update(yeni_data)
    return taslak_kaydet(taslak, Path(json_yolu))


def taslak_sil(taslak: Dict[str, Any]) -> bool:
    try:
        json_yolu = taslak.get("_json_yolu")
        if json_yolu and Path(json_yolu).exists():
            Path(json_yolu).unlink()
        kapak_yolu = taslak.get("_kapak_tam_yolu")
        if kapak_yolu and Path(kapak_yolu).exists():
            Path(kapak_yolu).unlink()
        return True
    except Exception:
        return False


# ============================================================
# YENİ TASLAK İNŞASI (motorlar bunu çağırır)
# ============================================================
def yeni_taslak_olustur(
    ulke_bilgi: Dict[str, Any],
    haber_index: int,
    icerik: Dict[str, Any],
    gercek_url: str,
    orj_baslik: str,
    orj_ozet: str,
    kapak_dosya_adi: Optional[str],
    kapak_basari: bool,
    kaynak: str = "otomatik_kultur",
) -> Dict[str, Any]:
    """
    Hem eski (haber tabanlı, düz JSON) hem yeni (kültür tabanlı, 
    pre-nested) içerik girdilerini destekleyen taslak yapıcısı.

    Eski çağrı: icerik = {"tr_baslik": "...", "tr_ozet": "..."}  → düz
    Yeni çağrı: icerik = {"TR_hook": ..., "TR_baslik": ...}      → düz (trend_motoru)
    """
    from urllib.parse import urlparse

    # Hangi dil seti? Ülke config'inden değil, ÜRETİCİ söylesin —
    # trend_motoru farklı diller seçebilir
    uretilecek_diller = ulke_bilgi.get("uretilecek_diller") or ulke_bilgi.get("varsayilan_diller", ["TR", "EN"])

    diller_data: Dict[str, Dict[str, Any]] = {}
    for dil_kodu in uretilecek_diller:
        # ESKI haber motoru lowercase kullanıyordu (tr_baslik), 
        # YENI trend motoru uppercase kullanıyor (TR_baslik). İkisini de ara.
        pfx_lo = dil_kodu.lower()
        pfx_up = dil_kodu.upper()

        def al(alan: str, default: Any = "") -> Any:
            return (
                icerik.get(f"{pfx_up}_{alan}")
                or icerik.get(f"{pfx_lo}_{alan}")
                or default
            )

        diller_data[dil_kodu] = {
            "_dil_adi": config.DIL_AYARLARI.get(dil_kodu, {}).get("ad", dil_kodu),
            "hook": al("hook", ""),
            "baslik": al("baslik", ""),
            "ozet": al("ozet", ""),
            "uzun_metin": al("uzun_metin", ""),
            "cta_soru": al("cta_soru", ""),
            "carousel_slaytlar": al("carousel_slaytlar", []) or [],
            "hashtags": al("hashtags", ""),
        }

    return {
        "_README": "Editör masası üzerinden düzenlenmelidir. Metadata alanlarına dokunma.",
        "metadata": {
            "haber_index": haber_index,
            "ulke_kodu": ulke_bilgi["kod"],
            "ulke_adi_tr": ulke_bilgi["ad_tr"],
            "ulke_klasor": ulke_bilgi["klasor"],
            "uretilecek_diller": uretilecek_diller,
            "uretim_tarihi": datetime.now().isoformat(),
            "kaynak_tipi": kaynak,
            "kaynak_url": gercek_url,
            "kaynak_domain": urlparse(gercek_url).netloc if gercek_url else "",
            "orijinal_baslik": orj_baslik,
            "orijinal_ozet": orj_ozet,
            "kapak_dosya_adi": kapak_dosya_adi,
            "kapak_indirildi": kapak_basari,
            "yayinlandi": False,
            # ↓ YENİ
            "tema": None,           # trend_motoru sonradan dolduracak
            "tohum": None,
            "format_listesi": ["video", "carousel"],
        },
        "diller": diller_data,
    }


# ============================================================
# YENİ: ÜRETİM GEÇMİŞİ — APScheduler için tohum tekrarı önle
# ============================================================
def son_n_gun_kullanilan_tohumlar(gun_sayisi: int = 30) -> List[str]:
    """
    Son N gündeki taslakları tarar, hangi konu tohumları kullanılmış,
    döndürür. trend_motoru.toplu_otomatik_uretim() bunu kullanır.
    """
    sinir_tarih = datetime.now() - timedelta(days=gun_sayisi)
    kullanilanlar: List[str] = []

    if not config.ARSIV_KLASORU.exists():
        return kullanilanlar

    for klasor in config.ARSIV_KLASORU.iterdir():
        if not (klasor.is_dir() and klasor.name.startswith("Taslaklar_")):
            continue
        if datetime.fromtimestamp(klasor.stat().st_mtime) < sinir_tarih:
            continue
        for ulke_alt in klasor.iterdir():
            if not ulke_alt.is_dir():
                continue
            for json_dosya in ulke_alt.glob("*.json"):
                try:
                    with open(json_dosya, "r", encoding="utf-8") as f:
                        veri = json.load(f)
                    tohum = veri.get("metadata", {}).get("tohum")
                    if tohum:
                        kullanilanlar.append(tohum)
                except Exception:
                    continue
    return kullanilanlar
