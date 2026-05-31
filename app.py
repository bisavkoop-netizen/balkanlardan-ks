"""
=================================================================
BALKANLARDAN — app.py (v5 — RBAC + Çok Kullanıcılı)
=================================================================
v4 → v5 değişiklikleri:
  • Tek kullanıcı .env sistemi → SQLite tabanlı RBAC sistemi
  • auth_motoru.py entegrasyonu
  • Dinamik sekme görünürlüğü (role göre göster/gizle)
  • Admin kullanıcı yönetim paneli
  • Editör masasındaki yayınla butonu → "yayimci" ve "admin" rolüne özel
=================================================================
"""
from datetime import datetime
from pathlib import Path
import tempfile

import streamlit as st

from modules import config
from modules import storage
from modules import kaynaklar
from modules import trend_motoru
from modules import carousel_uretimi
from modules import routing
from modules import wp_motoru
from modules import yayin_motoru
from modules import auth_motoru   # ← YENİ


# ============================================================
# SAYFA YAPILANDIRMASI
# ============================================================
st.set_page_config(
    page_title="Balkanlardan — Kültür Platformu",
    page_icon="🎭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Uygulama başlangıcında DB'yi garantiye al (idempotent)
auth_motoru.db_baslat()


# ============================================================
# GİRİŞ EKRANI — Oturum yoksa göster, varsa atla
# ============================================================
def _kurulum_ekrani() -> bool:
    """
    Sistemde hiç admin yoksa ilk kurulum formunu gösterir.
    Admin oluşturulunca True döner ve normal akış başlar.
    Zaten admin varsa doğrudan True döner.
    """
    if not auth_motoru.admin_kurulum_gerekli():
        return True  # Kurulum tamamlanmış, geç

    st.markdown(
        "<h2 style='text-align:center;margin-top:60px'>🎭 Balkanlardan</h2>"
        "<h4 style='text-align:center;color:#888'>İlk Kurulum — Admin Hesabı Oluştur</h4>"
        "<p style='text-align:center;color:#aaa;font-size:0.9em'>"
        "Bu ekran yalnızca bir kez görünür.</p>",
        unsafe_allow_html=True,
    )
    _, col, _ = st.columns([1, 1.2, 1])
    with col:
        st.info("Sisteme giriş yapabilmek için önce bir **Admin** hesabı oluşturman gerekiyor.")
        with st.form("kurulum_formu"):
            ad_soyad = st.text_input("👤 Ad Soyad *")
            email    = st.text_input("📧 E-posta *", placeholder="ornek@balkanlardan.com")
            sifre    = st.text_input("🔒 Şifre *", type="password")
            sifre2   = st.text_input("🔒 Şifre tekrar *", type="password")
            kur      = st.form_submit_button("🚀 Admin Hesabını Oluştur", type="primary", use_container_width=True)

        if kur:
            if not ad_soyad or not email or not sifre:
                st.warning("Tüm alanlar zorunludur.")
            elif sifre != sifre2:
                st.error("❌ Şifreler eşleşmiyor.")
            elif len(sifre) < 8:
                st.error("❌ Şifre en az 8 karakter olmalı.")
            else:
                ok, hata = auth_motoru.ilk_admin_olustur(
                    email=email, ad_soyad=ad_soyad, sifre=sifre
                )
                if ok:
                    st.success("✅ Admin hesabı oluşturuldu! Giriş sayfasına yönlendiriliyorsun...")
                    st.rerun()
                else:
                    st.error(f"❌ {hata}")
    return False


def _giris_ekrani() -> bool:
    if auth_motoru.oturum_kullanicisi():
        return True

    st.markdown(
        "<h2 style='text-align:center;margin-top:80px'>🎭 Balkanlardan</h2>"
        "<p style='text-align:center;color:#888'>İçerik Üretim Platformu</p>",
        unsafe_allow_html=True,
    )
    _, col, _ = st.columns([1, 1.2, 1])
    with col:
        with st.form("giris_formu", clear_on_submit=False):
            email = st.text_input("📧 E-posta", placeholder="ornek@balkanlardan.com")
            sifre = st.text_input("🔒 Şifre", type="password")
            girdi = st.form_submit_button("Giriş Yap", type="primary", use_container_width=True)

        if girdi:
            if not email or not sifre:
                st.warning("E-posta ve şifre boş bırakılamaz.")
            else:
                ok, kullanici, hata = auth_motoru.giris_yap(email, sifre)
                if ok:
                    auth_motoru.oturumu_kaydet(kullanici)
                    st.rerun()
                else:
                    st.error(f"❌ {hata}")
    return False


# Önce kurulum, sonra giriş
if not _kurulum_ekrani():
    st.stop()

if not _giris_ekrani():
    st.stop()

# Aktif kullanıcıyı bir kez al — tüm uygulama bunu kullanır
aktif_kullanici = auth_motoru.oturum_kullanicisi()


# ============================================================
# SIDEBAR — logo + metrikler + kullanıcı bilgisi + çıkış
# ============================================================
with st.sidebar:
    if config.LOGO_TAM_YOLU.exists():
        st.image(str(config.LOGO_TAM_YOLU))
    st.markdown("## 🎭 Balkanlardan")
    st.caption(f"Kültür Platformu · {datetime.now().strftime('%d.%m.%Y')}")
    st.divider()

    taslak_klasorleri = storage.taslak_klasorlerini_listele()
    toplam_taslak = sum(s["haber_sayisi"] for s in taslak_klasorleri)
    st.metric("📚 Toplam taslak", toplam_taslak)
    st.metric("🌍 Aktif ülke", len(config.ULKELER))
    st.metric("🗣️ Desteklenen dil", len(config.DIL_AYARLARI))

    st.divider()
    st.caption(f"🤖 Model: `{config.CLAUDE_MODEL}`")
    if config.DEBUG:
        st.warning("🐛 DEBUG modu açık")

    st.divider()

    # ── Kullanıcı bilgisi ve çıkış ──────────────────────────
    ikon = auth_motoru.rol_ikonu(aktif_kullanici)
    roller_str = " · ".join(
        auth_motoru.ROLLER[r]["ad"]
        for r in aktif_kullanici["roller"]
        if r in auth_motoru.ROLLER
    )
    st.markdown(f"**{ikon} {aktif_kullanici['ad_soyad']}**")
    st.caption(f"📧 {aktif_kullanici['email']}")
    st.caption(f"🏷️ {roller_str}")

    if st.button("🚪 Çıkış Yap", use_container_width=True):
        auth_motoru.oturumu_kapat()
        st.rerun()


# ============================================================
# DİNAMİK SEKME SİSTEMİ — rol bazlı görünürlük
# ============================================================
_SEKME_TANIMI = [
    # (görünen etiket,           yetki_kodu,              dahili anahtar)
    ("✨ Yeni İçerik Üret",      "uretim",                "uretim"),
    ("✍️ Saha Notu",              "saha",                  "saha"),
    ("📝 Editör Masası",          "editor_masa",           "editor"),
    ("🗺️ Yönlendirme",            "yonlendirme",           "yonlendirme"),
    ("⚙️ Ayarlar",                "ayarlar",               "ayarlar"),
    ("👥 Kullanıcı Yönetimi",     "kullanici_yonetimi",    "kullanici_ynt"),
]

gorunen = [
    (etiket, anahtar)
    for etiket, yetki, anahtar in _SEKME_TANIMI
    if auth_motoru.yetkisi_var_mi(aktif_kullanici, yetki)
]

if not gorunen:
    st.error("Hiçbir sekmeye erişim yetkiniz yok. Lütfen adminle iletişime geçin.")
    st.stop()

st.title("🎭 Balkanlardan — Kültür İçerik Platformu")
st.caption("Balkanların unutulmuş hikayelerini, Z-kuşağına viral içerik olarak anlatıyoruz.")

sekme_nesneleri = st.tabs([etiket for etiket, _ in gorunen])
sekme_map = {
    anahtar: nesne
    for (_, anahtar), nesne in zip(gorunen, sekme_nesneleri)
}


# ============================================================
# SEKME: YENİ İÇERİK ÜRET
# ============================================================
if "uretim" in sekme_map:
    with sekme_map["uretim"]:
        st.header("✨ Yeni İçerik Üret")
        st.markdown(
            "Bir **konu tohumu** seç (havuzdan rastgele veya kendin yaz). "
            "Claude bunu hook, carousel slaytları ve uzun blog metni olarak geliştirir."
        )

        sol, sag = st.columns([3, 2])
        with sol:
            st.subheader("1️⃣ Konu Tohumu")

            tohum_modu = st.radio(
                "Tohum nereden gelsin?",
                options=["🎲 Havuzdan rastgele seç", "✍️ Kendim yazacağım"],
                horizontal=True,
                key="tohum_modu",
            )

            if tohum_modu == "🎲 Havuzdan rastgele seç":
                tema_secim = st.selectbox(
                    "Tema",
                    options=["(rastgele tema)"] + kaynaklar.tum_temalar(),
                    format_func=lambda t: t.replace("_", " ").title() if t != "(rastgele tema)" else t,
                    key="tema_secim",
                )

                if st.button("🎲 Yeni tohum öner", key="tohum_oneri"):
                    secili_tema = None if tema_secim == "(rastgele tema)" else tema_secim
                    kullanilanlar = storage.son_n_gun_kullanilan_tohumlar(30)
                    onerilen = kaynaklar.konu_tohumu_sec(tema=secili_tema, haric_tutulanlar=kullanilanlar)
                    st.session_state["aktif_tohum"] = onerilen or "Havuz tükendi"
                    st.session_state["aktif_tema"] = secili_tema or "tarih_hikaye"

                aktif = st.session_state.get("aktif_tohum", "")
                if aktif:
                    st.success(f"💡 **Aktif tohum:**\n\n{aktif}")
                    st.caption(f"Tema: `{st.session_state.get('aktif_tema', '?')}`")
                else:
                    st.info("👆 'Yeni tohum öner' butonuna bas")
            else:
                kendi_tohum = st.text_area(
                    "Konu tohumun (1-2 cümle)",
                    placeholder="Örn: Üsküp'ün Türk Bedesteni neden hâlâ ayakta?",
                    height=80,
                    key="kendi_tohum",
                )
                kendi_tema = st.selectbox(
                    "Bu tohumun teması",
                    options=kaynaklar.tum_temalar(),
                    format_func=lambda t: t.replace("_", " ").title(),
                    key="kendi_tema",
                )
                if kendi_tohum.strip():
                    st.session_state["aktif_tohum"] = kendi_tohum.strip()
                    st.session_state["aktif_tema"] = kendi_tema

        with sag:
            st.subheader("2️⃣ Ülke ve Diller")
            ulke_kodu = st.selectbox(
                "Hangi ülke odaklı?",
                options=list(config.ULKELER.keys()),
                format_func=lambda k: f"{config.ULKELER[k]['ad_tr']}",
                key="uretim_ulke",
            )
            ulke_bilgi = config.ULKELER[ulke_kodu]

            varsayilan_diller = ulke_bilgi.get("varsayilan_diller", ["TR", "EN"])
            secili_diller = st.multiselect(
                "Hangi dillere çevirilecek?",
                options=list(config.DIL_AYARLARI.keys()),
                default=varsayilan_diller,
                format_func=lambda d: config.DIL_AYARLARI[d]["ad"],
                key="uretim_diller",
            )

            st.subheader("3️⃣ Format")
            format_secimi = []
            if st.checkbox("🎬 Video (Reels/TikTok)", value=True, key="fmt_video"):
                format_secimi.append("video")
            if st.checkbox("🖼️ Carousel (5 slayt)", value=True, key="fmt_carousel"):
                format_secimi.append("carousel")
            if st.checkbox("📝 Sadece blog yazısı", value=False, key="fmt_blog"):
                format_secimi.append("blog")

        st.divider()

        aktif_tohum = st.session_state.get("aktif_tohum", "")
        aktif_tema = st.session_state.get("aktif_tema", "tarih_hikaye")
        uret_buton_aktif = bool(aktif_tohum and secili_diller and format_secimi)

        if st.button(
            "🚀 İçeriği Üret",
            type="primary",
            disabled=not uret_buton_aktif,
            use_container_width=True,
        ):
            with st.spinner(f"Claude '{aktif_tohum[:60]}...' konusunda içerik üretiyor (30-90 sn)..."):
                try:
                    uretim = trend_motoru.icerik_uret_tohumdan(
                        tohum_konusu=aktif_tohum,
                        ulke_kodu=ulke_kodu,
                        tema=aktif_tema,
                        formatlar=format_secimi,
                        ekstra_diller=secili_diller,
                    )

                    taslak_kok, _ = storage.bugunku_taslak_klasoru()
                    ulke_klasor, _ = storage.ulke_klasoru_olustur(taslak_kok, ulke_bilgi["klasor"])
                    mevcut_sayi = len(list(ulke_klasor.glob("*.json")))
                    sira_no = mevcut_sayi + 1

                    ulke_bilgi_kopya = dict(ulke_bilgi)
                    ulke_bilgi_kopya["uretilecek_diller"] = uretim["diller"]

                    taslak = storage.yeni_taslak_olustur(
                        ulke_bilgi=ulke_bilgi_kopya,
                        haber_index=sira_no,
                        icerik=uretim["icerik"],
                        gercek_url="",
                        orj_baslik=aktif_tohum,
                        orj_ozet=f"Tema: {aktif_tema}",
                        kapak_dosya_adi=None,
                        kapak_basari=False,
                        kaynak="manuel_kultur" if st.session_state.get("tohum_modu") != "🎲 Havuzdan rastgele seç" else "otomatik_kultur",
                    )
                    taslak["metadata"]["tema"] = aktif_tema
                    taslak["metadata"]["tohum"] = aktif_tohum
                    taslak["metadata"]["format_listesi"] = format_secimi

                    json_yolu = ulke_klasor / f"{ulke_bilgi['klasor']}_Kultur_{sira_no:02d}.json"
                    if storage.taslak_kaydet(taslak, json_yolu):
                        st.success(f"✅ Taslak kaydedildi: `{json_yolu.name}`")
                        st.balloons()

                        st.markdown("### 👁️ Hızlı Önizleme")
                        on_dil = uretim["diller"][0]
                        icerik = uretim["icerik"]
                        p1, p2 = st.columns(2)
                        with p1:
                            st.markdown(f"**Hook ({on_dil}):**")
                            st.info(icerik.get(f"{on_dil}_hook", ""))
                            st.markdown(f"**Başlık ({on_dil}):**")
                            st.markdown(f"_{icerik.get(f'{on_dil}_baslik', '')}_")
                        with p2:
                            st.markdown(f"**Carousel slaytları ({on_dil}):**")
                            for i, s in enumerate(icerik.get(f"{on_dil}_carousel_slaytlar", []), 1):
                                st.caption(f"{i}. {s}")
                        st.info("📝 Tüm dilleri görmek ve düzenlemek için **Editör Masası** sekmesine geç.")
                    else:
                        st.error("❌ Taslak kaydedilemedi.")
                except Exception as e:
                    st.error(f"❌ Üretim hatası: {e}")
                    if config.DEBUG:
                        st.exception(e)
        elif not aktif_tohum:
            st.warning("⚠️ Önce bir konu tohumu seç (sol panel).")
        elif not secili_diller:
            st.warning("⚠️ En az bir dil seç (sağ panel).")
        elif not format_secimi:
            st.warning("⚠️ En az bir format seç (sağ panel).")


# ============================================================
# SEKME: SAHA NOTU
# ============================================================
if "saha" in sekme_map:
    with sekme_map["saha"]:
        st.header("✍️ Saha Notu — Manuel Giriş")
        st.markdown(
            "Sahadan, röportajdan veya kendi araştırmandan aldığın ham notları "
            "buraya yapıştır. Claude bunu küratör tonunda viral içeriğe dönüştürür."
        )

        with st.form("saha_notu_formu", clear_on_submit=False):
            c1, c2 = st.columns([2, 1])
            with c1:
                ham_notlar = st.text_area(
                    "Ham notların",
                    height=300,
                    placeholder=(
                        "Örnek:\n"
                        "- Plovdiv eski şehrinde, 19. yy Bulgar Ulusal Uyanış dönemi evleri\n"
                        "- Renkli ahşap cephe + kapalı balkonlar (kerpik)\n"
                        "- Mimari etkisi: Osmanlı + Avrupa Barok karışımı\n"
                        "- Konuştuğum yerel rehber Yana, en eski evin 1842 yapımı olduğunu söyledi"
                    ),
                    key="saha_ham",
                )
                ek_baglam = st.text_input(
                    "Ek bağlam (opsiyonel)",
                    placeholder="Örn: 'Eylül 2024'te yerinde ziyaret', 'BBC röportajından özet'",
                    key="saha_baglam",
                )
            with c2:
                saha_ulke = st.selectbox(
                    "Bu not hangi ülkeyle ilgili?",
                    options=list(config.ULKELER.keys()),
                    format_func=lambda k: config.ULKELER[k]['ad_tr'],
                    key="saha_ulke",
                )
                saha_tema = st.selectbox(
                    "Tema",
                    options=kaynaklar.tum_temalar(),
                    format_func=lambda t: t.replace("_", " ").title(),
                    key="saha_tema",
                )
                saha_kapak = st.file_uploader(
                    "Kapak fotoğrafı (opsiyonel)",
                    type=["jpg", "jpeg", "png", "webp"],
                    key="saha_kapak",
                )
                if saha_kapak:
                    st.image(saha_kapak, use_container_width=True)
            gonder = st.form_submit_button("🤖 İçeriğe Dönüştür", type="primary")

        if gonder:
            if not ham_notlar.strip():
                st.warning("Lütfen ham notlarını gir.")
            else:
                with st.spinner("Claude saha notunu içeriğe dönüştürüyor (30-90 sn)..."):
                    try:
                        ulke_bilgi_saha = config.ULKELER[saha_ulke]
                        tohum_olarak = (ham_notlar.strip().split("\n")[0])[:200]
                        ek_metin = f"{ek_baglam}\n\nHAM NOTLAR:\n{ham_notlar}" if ek_baglam else f"HAM NOTLAR:\n{ham_notlar}"

                        uretim = trend_motoru.icerik_uret_tohumdan(
                            tohum_konusu=tohum_olarak,
                            ulke_kodu=saha_ulke,
                            tema=saha_tema,
                            formatlar=["video", "carousel"],
                            ek_baglam=ek_metin,
                        )

                        taslak_kok, _ = storage.bugunku_taslak_klasoru()
                        ulke_klasor, kapak_klasor = storage.ulke_klasoru_olustur(taslak_kok, ulke_bilgi_saha["klasor"])
                        mevcut_sayi = len(list(ulke_klasor.glob("*.json")))
                        sira_no = mevcut_sayi + 1

                        kapak_dosya_adi = None
                        kapak_basarili = False
                        if saha_kapak is not None:
                            try:
                                uzanti = Path(saha_kapak.name).suffix.lower() or ".jpg"
                                kapak_dosya_adi = f"{ulke_bilgi_saha['klasor']}_Saha_{sira_no:02d}_kapak{uzanti}"
                                kapak_yolu = kapak_klasor / kapak_dosya_adi
                                with open(kapak_yolu, "wb") as f:
                                    f.write(saha_kapak.getbuffer())
                                kapak_basarili = True
                            except Exception as e:
                                st.warning(f"Kapak kaydedilemedi: {e}")

                        ulke_bilgi_kopya = dict(ulke_bilgi_saha)
                        ulke_bilgi_kopya["uretilecek_diller"] = uretim["diller"]

                        taslak = storage.yeni_taslak_olustur(
                            ulke_bilgi=ulke_bilgi_kopya,
                            haber_index=sira_no,
                            icerik=uretim["icerik"],
                            gercek_url="",
                            orj_baslik=tohum_olarak,
                            orj_ozet=ek_baglam or ham_notlar[:300],
                            kapak_dosya_adi=kapak_dosya_adi,
                            kapak_basari=kapak_basarili,
                            kaynak="saha",
                        )
                        taslak["metadata"]["tema"] = saha_tema
                        taslak["metadata"]["tohum"] = tohum_olarak
                        taslak["metadata"]["ham_notlar"] = ham_notlar

                        json_yolu = ulke_klasor / f"{ulke_bilgi_saha['klasor']}_Saha_{sira_no:02d}.json"
                        if storage.taslak_kaydet(taslak, json_yolu):
                            st.success(f"✅ Saha taslağı kaydedildi: `{json_yolu.name}`")
                            st.info("📝 Düzenlemek için **Editör Masası** sekmesine geç.")
                    except Exception as e:
                        st.error(f"❌ Üretim hatası: {e}")
                        if config.DEBUG:
                            st.exception(e)


# ============================================================
# SEKME: EDİTÖR MASASI
# ============================================================
if "editor" in sekme_map:
    with sekme_map["editor"]:
        st.header("📝 Editör Masası")
        st.markdown(
            "Üretilen taslakları gör, düzenle, kaydet, carousel önizlemesi üret "
            "veya **WordPress'e yayınla**. Yayın butonu her taslağın altında."
        )

        if not taslak_klasorleri:
            st.info("Henüz hiç taslak yok. **Yeni İçerik Üret** sekmesinden başla.")
        else:
            secili_klasor = st.selectbox(
                "Hangi günün taslakları?",
                options=[s["ad"] for s in taslak_klasorleri],
                format_func=lambda a: next(
                    f"{s['ad']} ({s['haber_sayisi']} taslak)" for s in taslak_klasorleri if s["ad"] == a
                ),
                key="editor_klasor",
            )

            secili_klasor_yolu = Path(next(s["yol"] for s in taslak_klasorleri if s["ad"] == secili_klasor))
            taslaklar = storage.taslaklari_yukle(secili_klasor_yolu)

            if not taslaklar:
                st.info("Bu klasörde taslak yok.")
            else:
                mevcut_ulkeler = sorted(set(t["metadata"]["ulke_kodu"] for t in taslaklar))
                filtre = st.multiselect(
                    "Ülke filtresi (boş = hepsi)",
                    options=mevcut_ulkeler,
                    format_func=lambda k: config.ULKELER.get(k, {}).get("ad_tr", k),
                    key="editor_filtre",
                )

                filtreli = [t for t in taslaklar if not filtre or t["metadata"]["ulke_kodu"] in filtre]
                st.caption(f"📊 {len(filtreli)} taslak görüntüleniyor")

                for idx, taslak in enumerate(filtreli):
                    meta = taslak["metadata"]
                    ulke_kodu = meta["ulke_kodu"]
                    ulke_adi = meta.get("ulke_adi_tr", ulke_kodu)
                    yayinlandi = meta.get("yayinlandi", False)
                    tema = meta.get("tema", "?")
                    tohum = meta.get("tohum", meta.get("orijinal_baslik", ""))[:80]
                    kapak_yolu = taslak.get("_kapak_tam_yolu")
                    durum_isareti = "✅" if yayinlandi else "📝"

                    with st.expander(
                        f"{durum_isareti} **{ulke_adi}** · {tema} · {tohum}",
                        expanded=False,
                    ):
                        m1, m2, m3 = st.columns([2, 1, 1])
                        with m1:
                            st.caption(f"🌱 Tohum: _{meta.get('tohum', '—')}_")
                            st.caption(f"📂 Dosya: `{Path(taslak['_json_yolu']).name}`")
                        with m2:
                            st.caption(f"🏷️ Tema: `{tema}`")
                            st.caption(f"📦 Kaynak: `{meta.get('kaynak_tipi', '?')}`")
                        with m3:
                            formatlar = meta.get("format_listesi", ["video"])
                            st.caption(f"🎨 Format: `{', '.join(formatlar)}`")
                            if kapak_yolu and Path(kapak_yolu).exists():
                                st.caption("🖼️ Kapak: ✅")

                        if kapak_yolu and Path(kapak_yolu).exists():
                            st.image(kapak_yolu, width=200)

                        diller_listesi = list(taslak.get("diller", {}).keys())
                        if not diller_listesi:
                            st.warning("Bu taslakta dil verisi yok.")
                            continue

                        dil_etiketleri = [f"{d}" for d in diller_listesi]
                        dil_sekmeleri = st.tabs(dil_etiketleri)

                        guncellenmis_diller = {}

                        for dil_sek, dil_kodu in zip(dil_sekmeleri, diller_listesi):
                            with dil_sek:
                                dil_data = taslak["diller"][dil_kodu]
                                dil_adi = config.DIL_AYARLARI.get(dil_kodu, {}).get("ad", dil_kodu)
                                st.caption(f"📖 {dil_adi}")

                                yeni_hook = st.text_input(
                                    "🎯 Hook (scroll-stopper)",
                                    value=dil_data.get("hook", ""),
                                    key=f"hook_{idx}_{dil_kodu}_{ulke_kodu}",
                                )
                                yeni_baslik = st.text_input(
                                    "📰 Başlık (SEO)",
                                    value=dil_data.get("baslik", ""),
                                    key=f"baslik_{idx}_{dil_kodu}_{ulke_kodu}",
                                )
                                yeni_ozet = st.text_area(
                                    "📝 Özet (caption gövdesi)",
                                    value=dil_data.get("ozet", ""),
                                    height=100,
                                    key=f"ozet_{idx}_{dil_kodu}_{ulke_kodu}",
                                )
                                yeni_uzun = st.text_area(
                                    "📜 Uzun metin (blog)",
                                    value=dil_data.get("uzun_metin", ""),
                                    height=180,
                                    key=f"uzun_{idx}_{dil_kodu}_{ulke_kodu}",
                                )

                                mevcut_slaytlar = dil_data.get("carousel_slaytlar", [])
                                if not isinstance(mevcut_slaytlar, list):
                                    mevcut_slaytlar = []
                                while len(mevcut_slaytlar) < 5:
                                    mevcut_slaytlar.append("")

                                st.markdown("**🖼️ Carousel slaytları (her biri ≤12 kelime)**")
                                yeni_slaytlar = []
                                for s_no in range(5):
                                    slayt_metni = st.text_input(
                                        f"Slayt {s_no + 1}",
                                        value=mevcut_slaytlar[s_no],
                                        key=f"slayt_{idx}_{dil_kodu}_{s_no}_{ulke_kodu}",
                                    )
                                    yeni_slaytlar.append(slayt_metni)

                                yeni_cta = st.text_input(
                                    "💬 Kapanış sorusu",
                                    value=dil_data.get("cta_soru", ""),
                                    key=f"cta_{idx}_{dil_kodu}_{ulke_kodu}",
                                )
                                yeni_hashtags = st.text_area(
                                    "🏷️ Hashtag'ler",
                                    value=dil_data.get("hashtags", ""),
                                    height=60,
                                    key=f"htag_{idx}_{dil_kodu}_{ulke_kodu}",
                                )

                                guncellenmis_diller[dil_kodu] = {
                                    "hook": yeni_hook,
                                    "baslik": yeni_baslik,
                                    "ozet": yeni_ozet,
                                    "uzun_metin": yeni_uzun,
                                    "carousel_slaytlar": yeni_slaytlar,
                                    "cta_soru": yeni_cta,
                                    "hashtags": yeni_hashtags,
                                }

                        st.divider()
                        b1, b2, b3 = st.columns([1, 1, 1])
                        with b1:
                            if st.button("💾 Değişiklikleri Kaydet", key=f"kaydet_{idx}_{ulke_kodu}"):
                                ok = storage.taslak_guncelle(taslak, {"diller": guncellenmis_diller})
                                st.success("✅ Kaydedildi.") if ok else st.error("❌ Kaydedilemedi.")
                        with b2:
                            if "carousel" in meta.get("format_listesi", []):
                                if st.button("🖼️ Carousel Önizlemesi Üret", key=f"carousel_{idx}_{ulke_kodu}"):
                                    storage.taslak_guncelle(taslak, {"diller": guncellenmis_diller})
                                    onizleme_kok = Path(secili_klasor_yolu) / meta["ulke_klasor"] / "Carousel_Onizleme"
                                    onizleme_kok.mkdir(parents=True, exist_ok=True)

                                    with st.spinner("Carousel slaytları render ediliyor..."):
                                        for dil_kodu, dil_data in guncellenmis_diller.items():
                                            slaytlar = dil_data.get("carousel_slaytlar", [])
                                            if not any(s.strip() for s in slaytlar):
                                                continue
                                            badge = config.ULKELER[ulke_kodu]["badgeler"].get(
                                                dil_kodu, config.ULKELER[ulke_kodu]["ad_tr"].upper()
                                            )
                                            on_ek = f"{meta['ulke_klasor']}_Kultur_{meta['haber_index']:02d}_{dil_kodu}"
                                            try:
                                                carousel_uretimi.carousel_uret(
                                                    slaytlar=slaytlar,
                                                    cta_soru=dil_data.get("cta_soru", ""),
                                                    badge_metni=badge,
                                                    kapak_yolu=kapak_yolu,
                                                    cikti_klasoru=onizleme_kok,
                                                    dosya_on_eki=on_ek,
                                                    log_callback=lambda m: None,
                                                )
                                            except Exception as e:
                                                st.error(f"❌ {dil_kodu} carousel hatası: {e}")
                                                continue

                                        pngler = sorted(onizleme_kok.glob("*.png"))
                                        if pngler:
                                            st.success(f"✅ {len(pngler)} slayt üretildi → `{onizleme_kok.name}/`")
                                            for dil_kodu in guncellenmis_diller.keys():
                                                dil_pngler = sorted(onizleme_kok.glob(f"*_{dil_kodu}_carousel_*.png"))
                                                if dil_pngler:
                                                    st.markdown(f"**🌐 {dil_kodu}**")
                                                    cols = st.columns(5)
                                                    for col, png in zip(cols, dil_pngler):
                                                        with col:
                                                            st.image(str(png), use_container_width=True)
                                        else:
                                            st.warning("Hiç slayt üretilmedi.")
                        with b3:
                            if st.button("🗑️ Sil", key=f"sil_{idx}_{ulke_kodu}"):
                                if storage.taslak_sil(taslak):
                                    st.success("Silindi. Sayfayı yenileyin.")
                                else:
                                    st.error("Silinemedi.")

                        # ── YAYIN BÖLÜMÜ — Sadece yayimci ve admin ──────────
                        st.divider()
                        if auth_motoru.yetkisi_var_mi(aktif_kullanici, "yayinla"):
                            yayin_basligi = "🚀 WordPress'e Yayınla"
                            if yayinlandi:
                                yayin_basligi += "  ⚠️ (Bu taslak daha önce yayınlanmış)"
                            st.markdown(f"### {yayin_basligi}")
                            st.caption(
                                "Tıklayınca: her dil için **video + carousel üretilir**, "
                                "WordPress'e **post atılır** (kapak + video + 5'li grid + metin). "
                                "İşlem 2-5 dakika sürebilir."
                            )

                            st.markdown("**🎞️ Arkaplan video(ları) — opsiyonel**")
                            st.caption(
                                "Canva Pro veya başka kaynaktan seçtiğin kaliteli stok video(ları) "
                                "buraya yükleyebilirsin. **Hiç yüklemezsen** sistem Pexels'ten otomatik "
                                "arkaplan üretir."
                            )
                            yuklenen_videolar = st.file_uploader(
                                "Video dosyası seç (mp4, mov)",
                                type=["mp4", "mov", "m4v"],
                                accept_multiple_files=True,
                                key=f"video_yukle_{idx}_{ulke_kodu}",
                            )
                            if yuklenen_videolar:
                                toplam_mb = sum(v.size for v in yuklenen_videolar) / (1024 * 1024)
                                st.success(f"✅ {len(yuklenen_videolar)} video yüklendi (toplam {toplam_mb:.1f} MB)")
                            else:
                                st.info("ℹ️  Video yüklemedin — Pexels otomatik arkaplanı kullanılacak")

                            onay_anahtari = f"yayin_onay_{idx}_{ulke_kodu}"
                            onay = st.checkbox(
                                "✅ Onaylıyorum, bu taslağı **gerçek WordPress'e** yayınla",
                                key=onay_anahtari,
                            )

                            yayinla_buton = st.button(
                                "🚀 Yayınlamayı Başlat",
                                key=f"yayinla_{idx}_{ulke_kodu}",
                                disabled=not onay,
                                type="primary",
                                use_container_width=True,
                            )

                            if yayinla_buton and onay:
                                storage.taslak_guncelle(taslak, {"diller": guncellenmis_diller})

                                kullanici_video_yollari: list = []
                                if yuklenen_videolar:
                                    yukleme_klasoru = Path(tempfile.mkdtemp(prefix="balkan_yukleme_"))
                                    for v in yuklenen_videolar:
                                        hedef = yukleme_klasoru / v.name
                                        with open(hedef, "wb") as f:
                                            f.write(v.getbuffer())
                                        kullanici_video_yollari.append(hedef)

                                st.info("⏳ Yayın akışı başladı...")
                                log_kutusu = st.empty()
                                log_satirlari: list = []

                                def _yayin_log(mesaj: str) -> None:
                                    log_satirlari.append(mesaj)
                                    log_kutusu.code("\n".join(log_satirlari[-50:]), language="text")

                                try:
                                    sonuc = yayin_motoru.taslagi_yayinla(
                                        taslak=taslak,
                                        log_callback=_yayin_log,
                                        kullanici_video_yollari=kullanici_video_yollari if kullanici_video_yollari else None,
                                    )

                                    st.success("🎉 Yayın akışı tamamlandı!")
                                    wp_postlar = sonuc.get("wp_postlar", {})
                                    if wp_postlar:
                                        st.markdown("#### 📝 WordPress Post'ları")
                                        for dk, wp_s in wp_postlar.items():
                                            if wp_s.get("ok"):
                                                post_id = wp_s.get("post_id")
                                                site_kok = config.WP_URL.split("/wp-json")[0]
                                                post_url = f"{site_kok}/?p={post_id}"
                                                kat_ad = ", ".join(k["ad"] for k in wp_s.get("kategoriler", []))
                                                st.markdown(f"- ✅ **{dk}** → Post `{post_id}` ({kat_ad}) — [Aç →]({post_url})")
                                            else:
                                                st.markdown(f"- ❌ **{dk}** → `{wp_s.get('hata', 'bilinmeyen hata')}`")

                                    with st.expander("🔍 Üretilen medya dosyaları"):
                                        st.json(sonuc.get("video_yollari", {}))
                                        st.json(sonuc.get("carousel_yollari", {}))

                                except Exception as e:
                                    st.error(f"❌ Yayın akışında hata: {e}")
                                    if config.DEBUG:
                                        st.exception(e)
                        else:
                            # Editör rolü: yayınlama yetkisi yok
                            st.info(
                                "🔒 Bu taslağı yayınlamak için **Yayımcı** veya **Admin** yetkisi gerekiyor. "
                                "Değişikliklerinizi kaydedebilir, ardından bir Yayımcıdan yayınlamasını isteyebilirsiniz."
                            )


# ============================================================
# SEKME: YÖNLENDİRME
# ============================================================
if "yonlendirme" in sekme_map:
    with sekme_map["yonlendirme"]:
        st.header("🗺️ Yayın Yönlendirme Haritası")

        matris_sol, matris_sag = st.columns([1, 1])

        with matris_sol:
            st.subheader("📡 Global Sosyal Hesaplar")
            st.caption("Token şablonu: `{PLATFORM}_GLOBAL_TOKEN` (.env içinde)")

            tum_hesaplar = routing.global_hesaplar(sadece_aktifler=False)
            for h in tum_hesaplar:
                durum_simge = "🟢" if h.aktif else "⚪️"
                durum_metin = "Aktif" if h.aktif else "Boş — devre dışı"
                with st.container(border=True):
                    st.markdown(f"{durum_simge} {h.ikon} **{h.platform_adi}**")
                    st.caption(f"_{durum_metin}_")
                    alanlar = routing.PLATFORMLAR[h.platform_kodu].env_alanlari
                    for a in alanlar:
                        env_adi = f"{h.platform_kodu}_GLOBAL_{a}"
                        deger = h.env_anahtarlari.get(a, "")
                        isaret = "✅" if deger else "❌"
                        st.markdown(f"&nbsp;&nbsp;{isaret} `{env_adi}`", unsafe_allow_html=True)

        with matris_sag:
            st.subheader("📝 WordPress Kategori Haritası")
            st.caption("Her dil postu = ülke kategorisi + dil kategorisi")

            for uk, ub in config.ULKELER.items():
                with st.expander(f"**{ub['ad_tr']}** ({len(ub.get('varsayilan_diller', []))} dil)"):
                    for dk in ub.get("varsayilan_diller", []):
                        try:
                            kats = routing.wp_kategorileri(uk, dk)
                            if len(kats) >= 2:
                                st.markdown(
                                    f"- **{config.DIL_AYARLARI[dk]['ad']}** "
                                    f"→ `{kats[0]}` + `{kats[1]}`"
                                )
                        except Exception:
                            st.caption(f"- {dk}: kategori bilgisi alınamadı")


# ============================================================
# SEKME: AYARLAR
# ============================================================
if "ayarlar" in sekme_map:
    with sekme_map["ayarlar"]:
        st.header("⚙️ Sistem Ayarları")

        ca, cb = st.columns(2)
        with ca:
            st.subheader("🔌 Bağlantı Testleri")
            if st.button("🤖 Claude API'yi test et"):
                ok, m = trend_motoru.claude_baglanti_testi()
                (st.success if ok else st.error)(m)
            if st.button("📝 WordPress'i test et"):
                ok, m = wp_motoru.wp_baglanti_testi()
                (st.success if ok else st.error)(m)
        with cb:
            st.subheader("📂 Klasör Durumu")
            st.code(
                f"Proje kökü:  {config.PROJE_KOK}\n"
                f"Arşiv:       {config.ARSIV_KLASORU}\n"
                f"Auth DB:     {auth_motoru.DB_YOLU}\n"
                f"Logo var mı: {'✅' if config.LOGO_TAM_YOLU.exists() else '❌'}\n"
                f"Toplam ülke: {len(config.ULKELER)}\n"
                f"Toplam dil:  {len(config.DIL_AYARLARI)}"
            )

        st.divider()
        st.subheader("🌱 Konu Tohumu Havuzu")
        st.caption("Otomatik üretim ve 'rastgele tohum öner' butonu bu havuzdan çeker.")

        tema_secimi = st.selectbox(
            "Hangi temayı görmek istersin?",
            options=kaynaklar.tum_temalar(),
            format_func=lambda t: t.replace("_", " ").title(),
            key="tohum_havuz_tema",
        )
        tohumlar = kaynaklar.KONU_TOHUMLARI.get(tema_secimi, [])
        st.caption(f"📊 {len(tohumlar)} tohum bu temada")
        for i, t in enumerate(tohumlar, 1):
            st.markdown(f"{i}. {t}")

        st.divider()
        st.subheader("📰 RSS Kaynakları")
        rss_listesi = kaynaklar.rss_kaynaklari_listele()
        st.caption(f"📊 {len(rss_listesi)} RSS kaynağı kayıtlı")
        for r in rss_listesi:
            st.markdown(f"- **{r.ad}** ({r.dil}) — `{r.tema}` — {r.url}")


# ============================================================
# SEKME: KULLANICI YÖNETİMİ (SADECE ADMİN GÖRÜR)
# ============================================================
if "kullanici_ynt" in sekme_map:
    with sekme_map["kullanici_ynt"]:
        st.header("👥 Kullanıcı Yönetimi")
        st.caption("Sisteme yeni kullanıcı ekle, mevcut kullanıcıları düzenle veya şifre sıfırla.")

        # ── Rol Referans Kartı ──────────────────────────────────
        with st.expander("📋 Rol ve Yetki Matrisi", expanded=False):
            matris_data = {
                "Rol": [], "✨ Üret": [], "✍️ Saha": [],
                "📝 Editör": [], "🚀 Yayınla": [], "🗺️ Yönlendirme": [],
                "⚙️ Ayarlar": [], "👥 Kullanıcı Ynt": [],
            }
            yetki_adlari = ["uretim", "saha", "editor_masa", "yayinla", "yonlendirme", "ayarlar", "kullanici_yonetimi"]
            sutun_adlari = ["✨ Üret", "✍️ Saha", "📝 Editör", "🚀 Yayınla", "🗺️ Yönlendirme", "⚙️ Ayarlar", "👥 Kullanıcı Ynt"]
            for rol_kodu, rol_bilgi in auth_motoru.ROLLER.items():
                matris_data["Rol"].append(f"{rol_bilgi['ikon']} {rol_bilgi['ad']}")
                rol_yetkileri = auth_motoru.YETKİ_MATRİSİ.get(rol_kodu, [])
                for yetki, sutun in zip(yetki_adlari, sutun_adlari):
                    matris_data[sutun].append("✅" if yetki in rol_yetkileri else "—")

            import pandas as pd
            st.dataframe(pd.DataFrame(matris_data), use_container_width=True, hide_index=True)

        # ── Yeni Kullanıcı Ekle ─────────────────────────────────
        with st.expander("➕ Yeni Kullanıcı Ekle", expanded=False):
            with st.form("yeni_kullanici_formu"):
                nc1, nc2 = st.columns(2)
                with nc1:
                    yeni_email  = st.text_input("E-posta *")
                    yeni_ad     = st.text_input("Ad Soyad *")
                with nc2:
                    yeni_sifre  = st.text_input(
                        "Şifre (boş bırakırsan rastgele üretilir)",
                        type="password",
                    )
                    yeni_roller = st.multiselect(
                        "Roller *",
                        options=list(auth_motoru.ROLLER.keys()),
                        format_func=lambda r: (
                            f"{auth_motoru.ROLLER[r]['ikon']} "
                            f"{auth_motoru.ROLLER[r]['ad']} — "
                            f"{auth_motoru.ROLLER[r]['aciklama']}"
                        ),
                    )
                ekle_buton = st.form_submit_button("✅ Kullanıcıyı Ekle", type="primary")

            if ekle_buton:
                if not yeni_email or not yeni_ad or not yeni_roller:
                    st.warning("E-posta, ad soyad ve en az bir rol zorunludur.")
                else:
                    kullanilacak_sifre = yeni_sifre.strip() or auth_motoru._rastgele_sifre()
                    ok, hata = auth_motoru.kullanici_ekle(
                        email=yeni_email,
                        ad_soyad=yeni_ad,
                        sifre=kullanilacak_sifre,
                        roller=yeni_roller,
                    )
                    if ok:
                        mesaj = f"✅ **{yeni_email}** eklendi."
                        if not yeni_sifre.strip():
                            mesaj += f"\n\n🔑 Üretilen şifre: `{kullanilacak_sifre}` _(bir kez görünür, kopyala!)_"
                        st.success(mesaj)
                        st.rerun()
                    else:
                        st.error(f"❌ {hata}")

        # ── Mevcut Kullanıcılar ─────────────────────────────────
        st.subheader("📋 Kayıtlı Kullanıcılar")
        kullanicilar = auth_motoru.tum_kullanicilar()
        st.caption(f"{len(kullanicilar)} kullanıcı kayıtlı")

        for kul in kullanicilar:
            kul_roller_str = ", ".join(
                f"{auth_motoru.ROLLER[r]['ikon']} {auth_motoru.ROLLER[r]['ad']}"
                for r in kul["roller"] if r in auth_motoru.ROLLER
            )
            aktif_badge = "🟢 Aktif" if kul["aktif"] else "🔴 Pasif"
            son_giris_str = kul.get("son_giris", "")[:16] if kul.get("son_giris") else "Hiç giriş yapılmadı"
            kendi_hesap = kul["id"] == aktif_kullanici["id"]

            with st.expander(
                f"{aktif_badge} · **{kul['ad_soyad']}** ({kul['email']}) · {kul_roller_str}"
                + (" ← sen" if kendi_hesap else "")
            ):
                du1, du2, du3 = st.columns([2, 1, 1])

                with du1:
                    yeni_rol_sec = st.multiselect(
                        "Rolleri güncelle",
                        options=list(auth_motoru.ROLLER.keys()),
                        default=kul["roller"],
                        format_func=lambda r: f"{auth_motoru.ROLLER[r]['ikon']} {auth_motoru.ROLLER[r]['ad']}",
                        key=f"rol_{kul['id']}",
                    )
                    yeni_ad_soyad = st.text_input(
                        "Ad Soyad",
                        value=kul["ad_soyad"],
                        key=f"ad_{kul['id']}",
                    )
                    if st.button("💾 Güncelle", key=f"guncelle_{kul['id']}"):
                        ok, hata = auth_motoru.kullanici_guncelle(
                            kullanici_id=kul["id"],
                            ad_soyad=yeni_ad_soyad,
                            roller=yeni_rol_sec,
                        )
                        if ok:
                            st.success("✅ Güncellendi.")
                            st.rerun()
                        else:
                            st.error(f"❌ {hata}")

                with du2:
                    st.caption(f"Son giriş: {son_giris_str}")
                    st.caption(f"Oluşturma: {kul['olusturma'][:10]}")
                    toggle_label = "🔴 Pasife Al" if kul["aktif"] else "🟢 Aktife Al"
                    if st.button(toggle_label, key=f"aktif_{kul['id']}", disabled=kendi_hesap):
                        ok, hata = auth_motoru.kullanici_guncelle(
                            kullanici_id=kul["id"],
                            aktif=not kul["aktif"],
                        )
                        if ok:
                            st.rerun()
                        else:
                            st.error(f"❌ {hata}")
                    if kendi_hesap:
                        st.caption("_(kendi hesabın — toggle devre dışı)_")

                with du3:
                    if st.button("🔑 Şifre Sıfırla", key=f"sifre_{kul['id']}"):
                        ok, sonuc = auth_motoru.sifre_sifirla(kul["id"])
                        if ok:
                            st.success(f"🔑 Yeni şifre: `{sonuc}`\n\n_(bir kez görünür, kopyala!)_")
                        else:
                            st.error(f"❌ {sonuc}")

                    if not kendi_hesap:
                        if st.button(
                            "🗑️ Kullanıcıyı Sil",
                            key=f"sil_kul_{kul['id']}",
                            type="secondary",
                        ):
                            ok, hata = auth_motoru.kullanici_sil(kul["id"])
                            if ok:
                                st.success("✅ Kullanıcı silindi.")
                                st.rerun()
                            else:
                                st.error(f"❌ {hata}")
                    else:
                        st.caption("_(kendi hesabın — silemezsin)_")
