"""
=================================================================
modules/auth_motoru.py — Çok Kullanıcılı Kimlik Doğrulama & RBAC (v2)
=================================================================
Balkanlardan platformu için SQLite tabanlı kullanıcı yönetimi.

Özellikler:
  • bcrypt ile şifre hashleme
  • Çoklu rol desteği (bir kullanıcı birden fazla rol taşıyabilir)
  • SQLite — kurulum gerektirmez, proje dizininde taşınabilir
  • Streamlit session_state entegrasyonu
  • Admin paneli yardımcıları

ROL HİYERARŞİSİ:
  admin      → Her şeye erişim + kullanıcı yönetimi
  yayimci    → Editör masası (yayınla butonu dahil)
  editor     → Editör masası (kaydet/düzenle/sil, yayınlama yok)
  yazar      → Sadece "Saha Notu" (manuel ham not girişi)
  yapay_zeka → Sadece "Yeni İçerik Üret" (AI otomatik üretim)

YETKİ MATRİSİ (True = erişim var):
                     admin  yayimci  editor  yazar  yapay_zeka
  uretim              ✓       ✗        ✗       ✗       ✓
  saha                ✓       ✗        ✗       ✓       ✗
  editor_masa         ✓       ✓        ✓       ✗       ✗
  yayinla             ✓       ✓        ✗       ✗       ✗
  yonlendirme         ✓       ✓        ✓       ✗       ✗
  ayarlar             ✓       ✗        ✗       ✗       ✗
  kullanici_yonetimi  ✓       ✗        ✗       ✗       ✗
=================================================================
"""
import sqlite3
import secrets
import string
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import bcrypt

from . import config

# ============================================================
# VERİTABANI YOLU
# ============================================================
DB_YOLU = config.PROJE_KOK / "auth.db"

# ============================================================
# ROL TANIMLARI
# ============================================================
ROLLER = {
    "admin":      {"ad": "Admin",      "ikon": "👑", "aciklama": "Tam erişim + kullanıcı yönetimi"},
    "yayimci":    {"ad": "Yayımcı",   "ikon": "🚀", "aciklama": "Editör masası — düzenle ve yayınla"},
    "editor":     {"ad": "Editör",    "ikon": "✍️", "aciklama": "Editör masası — düzenle (yayınlama yok)"},
    "yazar":      {"ad": "Yazar",     "ikon": "📝", "aciklama": "Sadece Saha Notu (manuel ham not girişi)"},
    "yapay_zeka": {"ad": "Yapay Zeka","ikon": "🤖", "aciklama": "Sadece AI ile otomatik içerik üretimi"},
}

# Yetki matrisi — rol → izin seti
YETKİ_MATRİSİ: Dict[str, List[str]] = {
    "admin":      ["uretim", "saha", "editor_masa", "yayinla", "yonlendirme", "ayarlar", "kullanici_yonetimi"],
    "yayimci":    ["editor_masa", "yayinla", "yonlendirme"],
    "editor":     ["editor_masa", "yonlendirme"],
    "yazar":      ["saha"],
    "yapay_zeka": ["uretim"],
}


# ============================================================
# VERİTABANI BAŞLATMA
# ============================================================
def db_baslat() -> None:
    """
    Veritabanını ve tabloları oluşturur. Uygulama başlangıcında çağrılır.
    Tablo zaten varsa dokunmaz (idempotent).
    """
    with _baglanti() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS kullanicilar (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                email       TEXT    NOT NULL UNIQUE,
                ad_soyad    TEXT    NOT NULL,
                sifre_hash  TEXT    NOT NULL,
                aktif       INTEGER NOT NULL DEFAULT 1,
                olusturma   TEXT    NOT NULL,
                son_giris   TEXT
            );

            CREATE TABLE IF NOT EXISTS roller (
                kullanici_id  INTEGER NOT NULL,
                rol           TEXT    NOT NULL,
                PRIMARY KEY (kullanici_id, rol),
                FOREIGN KEY (kullanici_id) REFERENCES kullanicilar(id)
            );

            CREATE INDEX IF NOT EXISTS idx_email ON kullanicilar(email);
        """)


def _baglanti() -> sqlite3.Connection:
    """Thread-safe SQLite bağlantısı döner."""
    conn = sqlite3.connect(str(DB_YOLU), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def admin_kurulum_gerekli() -> bool:
    """Sistemde hiç admin yoksa True döner. app.py kurulum ekranını göstermek için kullanır."""
    with _baglanti() as conn:
        var = conn.execute(
            "SELECT kullanicilar.id FROM kullanicilar "
            "JOIN roller ON kullanicilar.id = roller.kullanici_id "
            "WHERE roller.rol = 'admin' LIMIT 1"
        ).fetchone()
    return var is None


def ilk_admin_olustur(email: str, ad_soyad: str, sifre: str) -> Tuple[bool, str]:
    """Kurulum ekranından çağrılır. Sistemde zaten admin varsa reddeder."""
    if not admin_kurulum_gerekli():
        return False, "Sistemde zaten bir admin hesabı mevcut."
    return kullanici_ekle(email=email, ad_soyad=ad_soyad, sifre=sifre, roller=["admin"])


# ============================================================
# ŞİFRE YARDIMCILARI
# ============================================================
def _sifrele(sifre: str) -> str:
    """Şifreyi bcrypt ile hashler, str olarak döner."""
    return bcrypt.hashpw(sifre.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def _sifre_dogrula(sifre: str, sifre_hash: str) -> bool:
    """Şifreyi hash ile karşılaştırır."""
    try:
        return bcrypt.checkpw(sifre.encode("utf-8"), sifre_hash.encode("utf-8"))
    except Exception:
        return False


def _rastgele_sifre(uzunluk: int = 12) -> str:
    """Admin panelinden 'Şifre Sıfırla' için güvenli rastgele şifre üretir."""
    alfabe = string.ascii_letters + string.digits + "!@#$%"
    return "".join(secrets.choice(alfabe) for _ in range(uzunluk))


# ============================================================
# KULLANICI CRUD
# ============================================================
def kullanici_ekle(
    email: str,
    ad_soyad: str,
    sifre: str,
    roller: List[str],
) -> Tuple[bool, str]:
    """
    Yeni kullanıcı ekler.

    Returns:
        (True, "")        → başarılı
        (False, "mesaj")  → hata
    """
    # Geçersiz roller için erken çıkış
    gecersiz = [r for r in roller if r not in ROLLER]
    if gecersiz:
        return False, f"Geçersiz rol(ler): {gecersiz}"
    if not roller:
        return False, "En az bir rol gerekli."

    try:
        sifre_hash = _sifrele(sifre)
        with _baglanti() as conn:
            cursor = conn.execute(
                """
                INSERT INTO kullanicilar (email, ad_soyad, sifre_hash, aktif, olusturma)
                VALUES (?, ?, ?, 1, ?)
                """,
                (email.strip().lower(), ad_soyad.strip(), sifre_hash, datetime.now().isoformat()),
            )
            kullanici_id = cursor.lastrowid
            for rol in roller:
                conn.execute(
                    "INSERT INTO roller (kullanici_id, rol) VALUES (?, ?)",
                    (kullanici_id, rol),
                )
        return True, ""
    except sqlite3.IntegrityError:
        return False, f"Bu e-posta zaten kayıtlı: {email}"
    except Exception as e:
        return False, str(e)


def kullanici_guncelle(
    kullanici_id: int,
    ad_soyad: Optional[str] = None,
    roller: Optional[List[str]] = None,
    aktif: Optional[bool] = None,
) -> Tuple[bool, str]:
    """Ad/soyad, roller veya aktiflik durumunu günceller."""
    try:
        with _baglanti() as conn:
            if ad_soyad is not None:
                conn.execute(
                    "UPDATE kullanicilar SET ad_soyad = ? WHERE id = ?",
                    (ad_soyad.strip(), kullanici_id),
                )
            if aktif is not None:
                conn.execute(
                    "UPDATE kullanicilar SET aktif = ? WHERE id = ?",
                    (1 if aktif else 0, kullanici_id),
                )
            if roller is not None:
                gecersiz = [r for r in roller if r not in ROLLER]
                if gecersiz:
                    return False, f"Geçersiz rol(ler): {gecersiz}"
                if not roller:
                    return False, "En az bir rol gerekli."
                conn.execute("DELETE FROM roller WHERE kullanici_id = ?", (kullanici_id,))
                for rol in roller:
                    conn.execute(
                        "INSERT INTO roller (kullanici_id, rol) VALUES (?, ?)",
                        (kullanici_id, rol),
                    )
        return True, ""
    except Exception as e:
        return False, str(e)


def sifre_sifirla(kullanici_id: int, yeni_sifre: Optional[str] = None) -> Tuple[bool, str]:
    """
    Şifreyi sıfırlar. yeni_sifre verilmezse rastgele üretir.

    Returns:
        (True, yeni_sifre_duz)  → admin UI'da gösterilmek üzere
        (False, hata_mesaji)
    """
    try:
        sifre = yeni_sifre or _rastgele_sifre()
        sifre_hash = _sifrele(sifre)
        with _baglanti() as conn:
            etkilenen = conn.execute(
                "UPDATE kullanicilar SET sifre_hash = ? WHERE id = ?",
                (sifre_hash, kullanici_id),
            ).rowcount
        if etkilenen == 0:
            return False, "Kullanıcı bulunamadı."
        return True, sifre
    except Exception as e:
        return False, str(e)


def kullanici_sil(kullanici_id: int) -> Tuple[bool, str]:
    """Kullanıcıyı ve rollerini siler. Tek admin silinmez."""
    with _baglanti() as conn:
        # Bu kişi admin mi ve tek admin mi?
        admin_sayisi = conn.execute(
            "SELECT COUNT(*) FROM roller WHERE rol = 'admin'"
        ).fetchone()[0]
        bu_admin = conn.execute(
            "SELECT COUNT(*) FROM roller WHERE kullanici_id = ? AND rol = 'admin'",
            (kullanici_id,),
        ).fetchone()[0]
        if bu_admin and admin_sayisi <= 1:
            return False, "Sistemdeki tek admin silinemez."
        try:
            conn.execute("DELETE FROM roller WHERE kullanici_id = ?", (kullanici_id,))
            conn.execute("DELETE FROM kullanicilar WHERE id = ?", (kullanici_id,))
            return True, ""
        except Exception as e:
            return False, str(e)


def tum_kullanicilar() -> List[Dict]:
    """Tüm kullanıcıları rolleriyle birlikte döndürür."""
    with _baglanti() as conn:
        rows = conn.execute(
            """
            SELECT k.id, k.email, k.ad_soyad, k.aktif, k.olusturma, k.son_giris,
                   GROUP_CONCAT(r.rol, ',') AS roller
            FROM kullanicilar k
            LEFT JOIN roller r ON k.id = r.kullanici_id
            GROUP BY k.id
            ORDER BY k.olusturma DESC
            """
        ).fetchall()
    return [
        {
            "id": r["id"],
            "email": r["email"],
            "ad_soyad": r["ad_soyad"],
            "aktif": bool(r["aktif"]),
            "olusturma": r["olusturma"],
            "son_giris": r["son_giris"],
            "roller": r["roller"].split(",") if r["roller"] else [],
        }
        for r in rows
    ]


# ============================================================
# GİRİŞ / OTURUM
# ============================================================
def giris_yap(email: str, sifre: str) -> Tuple[bool, Optional[Dict], str]:
    """
    E-posta ve şifre ile giriş dener.

    Returns:
        (True, kullanici_dict, "")         → başarılı
        (False, None, "hata mesajı")       → başarısız
    """
    with _baglanti() as conn:
        row = conn.execute(
            """
            SELECT k.id, k.email, k.ad_soyad, k.aktif, k.sifre_hash,
                   GROUP_CONCAT(r.rol, ',') AS roller
            FROM kullanicilar k
            LEFT JOIN roller r ON k.id = r.kullanici_id
            WHERE k.email = ?
            GROUP BY k.id
            """,
            (email.strip().lower(),),
        ).fetchone()

    if not row:
        return False, None, "Bu e-posta adresi kayıtlı değil."
    if not row["aktif"]:
        return False, None, "Hesabınız devre dışı bırakılmış. Adminle iletişime geçin."
    if not _sifre_dogrula(sifre, row["sifre_hash"]):
        return False, None, "Şifre hatalı."

    # son_giris güncelle
    with _baglanti() as conn:
        conn.execute(
            "UPDATE kullanicilar SET son_giris = ? WHERE id = ?",
            (datetime.now().isoformat(), row["id"]),
        )

    kullanici = {
        "id": row["id"],
        "email": row["email"],
        "ad_soyad": row["ad_soyad"],
        "roller": row["roller"].split(",") if row["roller"] else [],
    }
    return True, kullanici, ""


# ============================================================
# YETKİ KONTROL YARDIMCILARI
# ============================================================
def yetkiler(kullanici: Dict) -> List[str]:
    """
    Bir kullanıcının sahip olduğu tüm yetkileri (izin listesi) döndürür.
    Kullanıcı birden fazla rol taşıyabilir — birleşim kümesi alınır.
    """
    izinler: set = set()
    for rol in kullanici.get("roller", []):
        izinler.update(YETKİ_MATRİSİ.get(rol, []))
    return list(izinler)


def yetkisi_var_mi(kullanici: Dict, izin: str) -> bool:
    """Tek bir izin kontrolü. Streamlit koşullarında `if yetki(...)` gibi kullanılır."""
    return izin in yetkiler(kullanici)


def rol_ikonu(kullanici: Dict) -> str:
    """Sidebar için: en yüksek role göre ikon döner."""
    for rol in ["admin", "yayimci", "editor", "yazar"]:
        if rol in kullanici.get("roller", []):
            return ROLLER[rol]["ikon"]
    return "👤"


# ============================================================
# STREAMLIT OTURUM YÖNETİMİ
# ============================================================
SESSION_KEY = "auth_kullanici"   # st.session_state anahtarı


def oturum_kullanicisi():
    """Aktif session'daki kullanıcı dict'ini döner. Giriş yoksa None."""
    import streamlit as st
    return st.session_state.get(SESSION_KEY)


def oturumu_kaydet(kullanici: Dict) -> None:
    """Başarılı girişten sonra kullanıcıyı session'a yazar."""
    import streamlit as st
    st.session_state[SESSION_KEY] = kullanici


def oturumu_kapat() -> None:
    """Çıkış yapar — session'ı temizler."""
    import streamlit as st
    st.session_state.pop(SESSION_KEY, None)
    # Eski tek-kullanıcı anahtarını da temizle (geçiş uyumluluğu)
    st.session_state.pop("giris_yapildi", None)
