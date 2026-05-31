from modules import trend_motoru, config

print("Claude tum dillerde icerik uretiyor, 30-60 saniye...")
print()

sonuc = trend_motoru.icerik_uret_tohumdan(
    tohum_konusu="Sevdalinka - Bosna ask turkusunun huznu nereden gelir?",
    ulke_kodu="BA",
    tema="muzik",
    formatlar=["video", "carousel"],
)

icerik = sonuc["icerik"]
diller = sonuc["diller"]

print(f"Uretilen diller: {diller}")
print()

for dil in diller:
    dil_adi = config.DIL_AYARLARI.get(dil, {}).get("ad", dil)
    print("=" * 60)
    print(f"  {dil} ({dil_adi})")
    print("=" * 60)
    print()
    print(f"  HOOK    : {icerik.get(dil + '_hook', '')}")
    print()
    print(f"  BASLIK  : {icerik.get(dil + '_baslik', '')}")
    print()
    print(f"  OZET    : {icerik.get(dil + '_ozet', '')}")
    print()
    print("  CAROUSEL:")
    slaytlar = icerik.get(dil + "_carousel_slaytlar", [])
    for i, s in enumerate(slaytlar, 1):
        print(f"    {i}. {s}")
    print()
    print(f"  KAPANIS : {icerik.get(dil + '_cta_soru', '')}")
    print()
    print(f"  HASHTAG : {icerik.get(dil + '_hashtags', '')}")
    print()
    uzun = icerik.get(dil + "_uzun_metin", "")
    print("  UZUN METIN (ilk 300 karakter):")
    print(f"    {uzun[:300]}{'...' if len(uzun) > 300 else ''}")
    print()