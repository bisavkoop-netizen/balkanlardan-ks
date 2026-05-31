"""
=================================================================
modules/trend_motoru.py — Kültür-Sanat İçerik Motoru (v4)
=================================================================
ESKİ haber_motoru.py'NİN YERİNE — Felsefe Değişti:

Eski: GNews → site filtreli arama → çok dilli haber özeti
Yeni: Konu tohumu (RSS / manuel / havuz) → küratör Claude → 
      hook + carousel + uzun blog + CTA sorusu → çok dilli çıktı

Dış API:
  • icerik_uret_tohumdan(tohum_konusu, ulke_kodu, tema, format_listesi)
      → Tek bir konu tohumundan içerik üretir
  • toplu_otomatik_uretim(ulke_kodu, sayi, log_callback)
      → APScheduler için — havuzdan rastgele tohumlar seçip üretir
  • claude_baglanti_testi()

JSON şema değişiklikleri (storage.yeni_taslak_olustur ile uyumlu):
  Yeni alanlar (her dil için):
    - {dil}_hook                (scroll-stopper, ≤15 kelime)
    - {dil}_baslik              (SEO başlığı, klasik)
    - {dil}_ozet                (caption gövdesi, 3-4 cümle)
    - {dil}_uzun_metin          (blog gövdesi, 4-5 paragraf)
    - {dil}_carousel_slaytlar   (5 elemanlı liste, her biri ≤12 kelime)
    - {dil}_cta_soru            (kapanış sorusu, yoruma davet)
    - {dil}_hashtags            (5-8 hashtag)
=================================================================
"""
import json
import random
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Any, Optional

from anthropic import Anthropic

from . import config
from . import storage
from . import kaynaklar


# ============================================================
# ANTHROPIC İSTEMCİSİ
# ============================================================
_client: Optional[Anthropic] = None


def _claude_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


# ============================================================
# DİL SETİ ÇÖZÜCÜSÜ
# Her içerik için: ZORUNLU_DILLER + ülkenin orijinal dili + 
# kullanıcı seçimi (varsa varsayılan_diller'den) birleşir
# ============================================================
def _dil_seti_belirle(
    ulke_kodu: str,
    ekstra_diller: Optional[List[str]] = None,
) -> List[str]:
    """
    Bir içerik için hangi dillere çevirilecek?

    Mantık:
      1) Zorunlu diller (TR, EN) HER ZAMAN var
      2) Ülkenin orijinal dili eklenir (yoksa)
      3) ekstra_diller verilmişse onlar da eklenir
      4) Verilmemişse, ülkenin varsayilan_diller listesi kullanılır
    """
    ulke = config.ULKELER.get(ulke_kodu, {})
    sonuc = list(config.ZORUNLU_DILLER)

    orj_dil = ulke.get("orijinal_dil_kodu")
    if orj_dil and orj_dil not in sonuc:
        sonuc.append(orj_dil)

    eklenecekler = ekstra_diller if ekstra_diller is not None else ulke.get("varsayilan_diller", [])
    for d in eklenecekler:
        if d not in sonuc and d in config.DIL_AYARLARI:
            sonuc.append(d)

    return sonuc


# ============================================================
# CLAUDE PROMPT — KÜLTÜR KÜRATÖR'Ü
# Mevcut "haber editörü" promptunun tam tersi: artık tarafsız 
# bilgi aktarımı değil, hikaye anlatımı + viral kanca + carousel.
# ============================================================
SISTEM_PROMPTU = """You are the Editorial Director of "Balkanlardan" — a Balkan culture & heritage 
platform whose mission is to surface forgotten, beautiful, and surprising Balkan stories 
to a global Gen-Z/Millennial audience. You work simultaneously as a serious cultural 
curator AND a social media native who understands the algorithm.

YOUR EDITORIAL VOICE — "Curator-Influencer":
  • Intellectual but accessible. Never lecture, always invite.
  • Source-aware: when you mention a fact (a date, a UNESCO listing, a famous person), 
    use it confidently — never invent specific years, place names, or attributions you 
    are not sure about. If unsure, speak more generally.
  • Hook-first writing. The first sentence ALWAYS earns the second.
  • Respectful across all Balkan identities. Never play one nation against another.
    The Balkans share more than they divide — this is your editorial axiom.
  • You write like Anthony Bourdain met Bettany Hughes — curious, grounded, warm.

ABSOLUTE RULES (ZERO TOLERANCE):
  1. NO INVENTED FACTS. If a story requires a specific date/name you're not certain of,
     phrase it as "according to local tradition" or rewrite to avoid the claim.
  2. NO NATIONALIST FRAMING. Never write "actually originated in X country" about 
     contested heritage (baklava, sevdalinka, çevapi etc.). Use phrasing like 
     "shared across the region" or "with versions in...".
  3. NO ORIENTALISM. The Balkans are Europe; treat them with the same seriousness 
     you'd treat Provençal cooking or Tuscan music.
  4. NO CLICKBAIT THAT LIES. Hooks can be dramatic, but the article must deliver.

LANGUAGE-SPECIFIC NAMING (critical):
  • Turkish (TR): Bulgaristan (NOT Bulgarya), Yunanistan (NOT just Yunan), 
    Türkiye, Sırbistan, Bosna Hersek, Kuzey Makedonya, Hırvatistan, 
    Arnavutluk, Kosova, Romanya, Slovenya, Karadağ.
  • English (EN): Turkey, Greece, Bulgaria, Serbia, Bosnia & Herzegovina, 
    North Macedonia, Croatia, Albania, Kosovo, Romania, Slovenia, Montenegro.
  • Greek (EL): Τουρκία, Ελλάδα, Βουλγαρία, Σερβία, etc.
  • Use ASCII-correct accented characters in all languages — no fallback transliteration.
"""


def _icerik_uret_promptu(
    tohum_konusu: str,
    tema: str,
    ulke_bilgi: Dict[str, Any],
    diller: List[str],
    formatlar: List[str],
    ek_baglam: str = "",
) -> str:
    """Her üretim için inşa edilen kullanıcı mesajı."""
    # JSON şemasını dinamik üret
    schema_per_lang: Dict[str, str] = {}
    for d in diller:
        dil_adi = config.DIL_AYARLARI[d]["ad"]
        schema_per_lang[f"{d}_hook"] = (
            f"({dil_adi}) Scroll-stopping first line, MAX 15 words. "
            f"Curiosity gap or pattern interrupt. NO 'today we discuss'."
        )
        schema_per_lang[f"{d}_baslik"] = (
            f"({dil_adi}) SEO-friendly article title, 8-12 words, "
            f"factual but inviting. Different from hook."
        )
        schema_per_lang[f"{d}_ozet"] = (
            f"({dil_adi}) 3-4 sentence social caption body. "
            f"Starts where hook left off. Builds curiosity."
        )
        schema_per_lang[f"{d}_uzun_metin"] = (
            f"({dil_adi}) 4-5 paragraph blog article. Curator voice — "
            f"deeper than caption, weaves context + sensory detail + cultural meaning. "
            f"Use natural keywords for SEO. End with reflection, not summary."
        )
        if "carousel" in formatlar:
            schema_per_lang[f"{d}_carousel_slaytlar"] = (
                f"({dil_adi}) Exactly 5 strings. Slide 1=hook, slides 2-4=facts/build, "
                f"slide 5=punchline + 'Save this post 📌'. Each slide MAX 12 words."
            )
        schema_per_lang[f"{d}_cta_soru"] = (
            f"({dil_adi}) Closing comment-bait question, 1 sentence, invites "
            f"personal stories. Not 'what do you think?' — be specific."
        )
        schema_per_lang[f"{d}_hashtags"] = (
            f"({dil_adi}) 5-8 hashtags. Mix #BalkanCulture (global) + niche/local. "
            f"Real searchable tags only."
        )

    # NOT: Pexels arama terimlerini artık Claude üretmiyor.
    # modules/kaynaklar.py'deki PEXELS_TEMA_TERIMLERI havuzu kullanılıyor.
    # Tema-bazlı insan eli ile seçilmiş terimler daha güvenilir sonuç veriyor.

    return f"""TOPIC SEED:
  "{tohum_konusu}"

THEME: {tema}
PRIMARY COUNTRY FOCUS: {ulke_bilgi.get('ad_en', '')} ({ulke_bilgi.get('ad_orj', '')})
ADDITIONAL CONTEXT: {ek_baglam or "None — develop the topic from your knowledge of Balkan culture."}

FORMATS REQUESTED: {', '.join(formatlar)}
LANGUAGES REQUESTED: {', '.join(diller)}

YOUR TASK:
  Develop this seed into a fully realized cultural piece. Treat the seed as a *starting 
  point*, not a prescription. If the seed mentions a specific claim you can't verify, 
  pivot to the broader story it points to.

  For each language, produce the fields in the schema below. All languages should tell 
  the SAME story, but be natively expressed — not literal translations. Idioms, rhythm, 
  and cultural reference points should adapt to each language's natural cadence.

  Stay in CURATOR voice across all fields. Even hooks should feel like a knowledgeable 
  friend grabbing your sleeve, not a TikTok shouter.

OUTPUT — strict flat JSON, no markdown fences. Exactly these fields:
{json.dumps(schema_per_lang, indent=2, ensure_ascii=False)}

Return only the JSON object."""


def icerik_uret_tohumdan(
    tohum_konusu: str,
    ulke_kodu: str,
    tema: str = "tarih_hikaye",
    formatlar: Optional[List[str]] = None,
    ekstra_diller: Optional[List[str]] = None,
    ek_baglam: str = "",
) -> Dict[str, Any]:
    """
    Tek bir konu tohumundan tam içerik üretir.

    Returns:
        {
          "diller": ['TR', 'EN', ...],   # üretilen dil listesi
          "icerik": {                    # Claude'un düz JSON çıktısı
              "TR_hook": "...", "TR_baslik": "...", ...
              "EN_hook": "...", ...
          },
          "format_listesi": ['video', 'carousel'],
          "tema": "muzik",
          "tohum": "Sevdalinka — ...",
        }
    """
    if formatlar is None:
        formatlar = ["video", "carousel"]

    ulke_bilgi = config.ULKELER.get(ulke_kodu)
    if not ulke_bilgi:
        raise ValueError(f"Bilinmeyen ülke kodu: {ulke_kodu}")

    diller = _dil_seti_belirle(ulke_kodu, ekstra_diller)
    kullanici_mesaji = _icerik_uret_promptu(
        tohum_konusu=tohum_konusu,
        tema=tema,
        ulke_bilgi=ulke_bilgi,
        diller=diller,
        formatlar=formatlar,
        ek_baglam=ek_baglam,
    )

    try:
        cevap = _claude_client().messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=8000,  # carousel + uzun metin + 6+ dil = bol token
            system=SISTEM_PROMPTU,
            messages=[{"role": "user", "content": kullanici_mesaji}],
        )
        metin = cevap.content[0].text.strip()

        # ```json fence temizliği
        if metin.startswith("```"):
            metin = metin.split("```", 2)[1]
            if metin.startswith("json"):
                metin = metin[4:]
            metin = metin.strip()

        icerik = json.loads(metin)

    except json.JSONDecodeError as e:
        print(f"   ⚠️  JSON parse hatası: {e}")
        print(f"      Ham yanıtın ilk 500 char: {metin[:500]}")
        icerik = _fallback_icerik(tohum_konusu, diller, formatlar)
    except Exception as e:
        print(f"   ⚠️  Claude hatası: {e}")
        if config.DEBUG:
            traceback.print_exc()
        icerik = _fallback_icerik(tohum_konusu, diller, formatlar)

    return {
        "diller": diller,
        "icerik": icerik,
        "format_listesi": formatlar,
        "tema": tema,
        "tohum": tohum_konusu,
    }


def _fallback_icerik(
    tohum: str,
    diller: List[str],
    formatlar: List[str],
) -> Dict[str, str]:
    """Claude patlarsa elimizde boş dönmeyelim — minimal bir yapı."""
    sonuc: Dict[str, Any] = {}
    for d in diller:
        sonuc[f"{d}_hook"] = tohum
        sonuc[f"{d}_baslik"] = tohum
        sonuc[f"{d}_ozet"] = "İçerik üretilemedi — editörlü düzeltme gerekir."
        sonuc[f"{d}_uzun_metin"] = ""
        if "carousel" in formatlar:
            sonuc[f"{d}_carousel_slaytlar"] = [tohum, "", "", "", "Save this post 📌"]
        sonuc[f"{d}_cta_soru"] = ""
        sonuc[f"{d}_hashtags"] = "#Balkanlardan #BalkanCulture"
    return sonuc


# ============================================================
# TOPLU OTOMATİK ÜRETİM — APScheduler bunu çağırır
# ============================================================
def toplu_otomatik_uretim(
    ulke_kodlari: Optional[List[str]] = None,
    icerik_sayisi: Optional[int] = None,
    log_callback: Callable[[str], None] = print,
    ilerleme_callback: Callable[[int, str], None] = lambda y, e: None,
) -> Dict[str, Any]:
    """
    Otomatik mod: her ülke için belirtilen sayıda içerik üretir.
    Konu tohumları havuzdan rastgele seçilir; aynı tohum 30 gün
    içinde tekrar seçilmesin diye storage'a danışılır.

    Args:
        ulke_kodlari: Hangi ülkeler? Default: tüm Balkan
        icerik_sayisi: Ülke başına kaç içerik? Default: config.ICERIK_SAYISI_PER_DONGUR

    Returns:
        {
          'klasor_adi': ...,
          'klasor_yolu': ...,
          'toplam_basarili': int,
          'ulke_basina': {...},
        }
    """
    if ulke_kodlari is None:
        ulke_kodlari = list(config.ULKELER.keys())
    if icerik_sayisi is None:
        icerik_sayisi = config.ICERIK_SAYISI_PER_DONGUR

    baslangic = datetime.now()
    taslak_kok, klasor_adi = storage.bugunku_taslak_klasoru()
    log_callback(f"📁 Otomatik üretim — klasör: {klasor_adi}")
    log_callback(f"🎯 Hedef: {len(ulke_kodlari)} ülke × {icerik_sayisi} içerik = {len(ulke_kodlari) * icerik_sayisi} taslak")

    sonuc = {
        "klasor_adi": klasor_adi,
        "klasor_yolu": str(taslak_kok),
        "toplam_basarili": 0,
        "ulke_basina": {},
    }

    toplam = len(ulke_kodlari)
    kullanilan_tohumlar: List[str] = []  # bu çalışmada tekrarı önle

    for sira, ulke_kodu in enumerate(ulke_kodlari):
        yuzde = int((sira / toplam) * 100) if toplam else 0
        ulke_adi = config.ULKELER.get(ulke_kodu, {}).get("ad_tr", ulke_kodu)
        ilerleme_callback(yuzde, f"{ulke_adi} ({sira + 1}/{toplam})")

        log_callback(f"\n🌍 {ulke_adi}")
        ulke_klasor, _ = storage.ulke_klasoru_olustur(
            taslak_kok, config.ULKELER[ulke_kodu]["klasor"]
        )
        basarili_ulke = 0

        for i in range(1, icerik_sayisi + 1):
            # Rastgele tema → o temadan tohum çek
            tema = random.choice(config.ICERIK_TEMALARI)
            tohum = kaynaklar.konu_tohumu_sec(
                tema=tema,
                haric_tutulanlar=kullanilan_tohumlar,
            )
            if not tohum:
                # Bu tema bitti, başkasından dene
                tohum = kaynaklar.konu_tohumu_sec(haric_tutulanlar=kullanilan_tohumlar)
            if not tohum:
                log_callback("   ⚠️  Konu tohumu havuzu tükendi, atlanıyor")
                continue

            kullanilan_tohumlar.append(tohum)
            log_callback(f"   [{i}/{icerik_sayisi}] {tema} → {tohum[:60]}…")

            try:
                uretim = icerik_uret_tohumdan(
                    tohum_konusu=tohum,
                    ulke_kodu=ulke_kodu,
                    tema=tema,
                    formatlar=["video", "carousel"],  # default: ikisi de
                )
            except Exception as e:
                log_callback(f"      ❌ Üretim hatası: {e}")
                continue

            # Taslağı diske yaz
            mevcut_sayi = len(list(ulke_klasor.glob("*.json")))
            sira_no = mevcut_sayi + 1

            taslak = storage.yeni_taslak_olustur(
                ulke_bilgi=config.ULKELER[ulke_kodu],
                haber_index=sira_no,
                icerik=uretim["icerik"],
                gercek_url="",
                orj_baslik=tohum,
                orj_ozet=f"Tema: {tema} — otomatik üretim",
                kapak_dosya_adi=None,
                kapak_basari=False,
                kaynak="otomatik_kultur",
            )
            # YENİ alanları metadata'ya işle
            taslak["metadata"]["tema"] = tema
            taslak["metadata"]["tohum"] = tohum
            taslak["metadata"]["format_listesi"] = uretim["format_listesi"]
            taslak["metadata"]["uretilecek_diller"] = uretim["diller"]
            # `diller` alt sözlüğünü yeniden inşa et — yeni alanlarla
            taslak["diller"] = _icerikten_diller_sozlugu(uretim["icerik"], uretim["diller"], uretim["format_listesi"])

            json_dosya = ulke_klasor / f"{config.ULKELER[ulke_kodu]['klasor']}_Kultur_{sira_no:02d}.json"
            if storage.taslak_kaydet(taslak, json_dosya):
                log_callback(f"      ✅ {json_dosya.name}")
                basarili_ulke += 1

        sonuc["ulke_basina"][ulke_kodu] = basarili_ulke
        sonuc["toplam_basarili"] += basarili_ulke

    sure = (datetime.now() - baslangic).total_seconds() / 60
    log_callback(f"\n🎉 Otomatik üretim bitti: {sonuc['toplam_basarili']} taslak, süre: {sure:.1f} dk")
    return sonuc


def _icerikten_diller_sozlugu(
    icerik: Dict[str, Any],
    diller: List[str],
    formatlar: List[str],
) -> Dict[str, Dict[str, Any]]:
    """
    Claude'un düz JSON çıktısını (TR_hook, TR_baslik, ...) 
    storage.py'nin beklediği nested yapıya çevirir:
      { 'TR': {'_dil_adi': 'Türkçe', 'hook': ..., 'baslik': ...}, ... }
    """
    sonuc: Dict[str, Dict[str, Any]] = {}
    for d in diller:
        veri = {
            "_dil_adi": config.DIL_AYARLARI[d]["ad"],
            "hook": icerik.get(f"{d}_hook", "") or "",
            "baslik": icerik.get(f"{d}_baslik", "") or "",
            "ozet": icerik.get(f"{d}_ozet", "") or "",
            "uzun_metin": icerik.get(f"{d}_uzun_metin", "") or "",
            "cta_soru": icerik.get(f"{d}_cta_soru", "") or "",
            "hashtags": icerik.get(f"{d}_hashtags", "") or "",
        }
        if "carousel" in formatlar:
            slaytlar = icerik.get(f"{d}_carousel_slaytlar", [])
            if isinstance(slaytlar, str):
                # Bazen Claude string olarak verir, JSON listeye zorla
                try:
                    slaytlar = json.loads(slaytlar)
                except Exception:
                    slaytlar = [slaytlar]
            veri["carousel_slaytlar"] = list(slaytlar) if slaytlar else []
        sonuc[d] = veri
    return sonuc


# ============================================================
# BAĞLANTI TESTİ
# ============================================================
def claude_baglanti_testi() -> tuple[bool, str]:
    try:
        cevap = _claude_client().messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=10,
            messages=[{"role": "user", "content": "ping"}],
        )
        return True, f"✅ Bağlantı OK — model: {cevap.model}"
    except Exception as e:
        return False, f"❌ Hata: {e}"
