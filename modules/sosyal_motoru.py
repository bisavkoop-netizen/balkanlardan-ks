"""
=================================================================
modules/sosyal_motoru.py — Sosyal Medya Dağıtım Motoru (v3 — Global)
=================================================================
Tek global hesap modeli:
  • Bir taslak için TEK caption örülür (4 dil yan yana, bayraklarla)
  • Bu caption + video, AKTİF her platforma BİR KEZ gönderilir
  • Bir Yunanistan haberi de bir Bulgaristan haberi de aynı feed'e akar
  • X için ayrı 'kısa caption' kullanılır (280 karakter limiti)

API NOTLARI:
  • Instagram (v21.0): 3 adımlı handshake — container yarat → polla →
    publish. Video PUBLIC URL'de olmalı; doğrudan dosya yüklenmez.
  • X: Medya yükleme v1.1 (OAuth1), tweet oluşturma v2. Tweepy ikisini
    de tek paketten halleder.
  • TikTok: Content Posting API, FILE_UPLOAD modu — chunked yükleme.
=================================================================
"""
import time
from pathlib import Path
from typing import Dict, Any, Callable, Optional

import requests

from . import config
from . import routing


# Sosyal dağıtımın video PUBLIC URL'ye ihtiyacı var. Yayın motoru
# WordPress'e medya yükledikten sonra public URL elinde — onu buraya
# parametre olarak geçeriz. Diskteki path yedek olarak tutulur.


# ============================================================
# DIŞ API
# ============================================================
def dagit(
    video_yolu: str,
    diller_data: Dict[str, Dict[str, str]],
    public_video_url: Optional[str] = None,
    log_callback: Callable[[str], None] = print,
) -> Dict[str, Any]:
    """
    Bir taslağı TÜM aktif global platformlara dağıtır.

    Args:
        video_yolu: Diskteki video dosyası (X için bayt yükleme).
        diller_data: {'TR': {'baslik','ozet','uzun_metin','hashtags'}, ...}
        public_video_url: WP'ye yüklendikten sonra elde edilen public URL.
                          Instagram ZORUNLU bunu ister (dosya yükleyemez).
                          None ise IG/TT/FB atlanır, sadece X çalışır.

    Returns:
        Her platform için: {'ok': bool, 'post_id'|'hata': str, 'caption_uzunluk': int}
    """
    hedefler = routing.global_hesaplar(sadece_aktifler=True)
    if not hedefler:
        log_callback("   ℹ️  Aktif global sosyal hesap yok — sosyal dağıtım atlanıyor")
        return {}

    # İki caption hazır: uzun (IG/TT/FB/YT) + kısa (X)
    uzun_caption = routing.cok_dilli_caption_or(diller_data)
    kisa_caption = routing.kisa_caption_or(diller_data)

    log_callback(f"   📝 Caption: uzun={len(uzun_caption)}ch, kısa={len(kisa_caption)}ch")
    log_callback(f"   📡 {len(hedefler)} aktif platforma dağıtılacak")

    sonuc: Dict[str, Any] = {}
    for hedef in hedefler:
        log_callback(f"   📤 {hedef.kisa_etiket} → yükleniyor...")
        try:
            if hedef.platform_kodu == "IG":
                hsonuc = _yukle_instagram(hedef, public_video_url, uzun_caption, log_callback)
            elif hedef.platform_kodu == "X":
                hsonuc = _yukle_x(hedef, video_yolu, kisa_caption)
            elif hedef.platform_kodu == "TT":
                hsonuc = _yukle_tiktok(hedef, video_yolu, public_video_url, uzun_caption)
            elif hedef.platform_kodu == "FB":
                hsonuc = _yukle_facebook(hedef, public_video_url, uzun_caption)
            elif hedef.platform_kodu == "YT":
                hsonuc = _yukle_youtube(hedef, video_yolu, uzun_caption)
            else:
                hsonuc = {"ok": False, "hata": f"Bilinmeyen platform: {hedef.platform_kodu}"}
        except Exception as e:
            hsonuc = {"ok": False, "hata": str(e)}
            if config.DEBUG:
                import traceback
                traceback.print_exc()

        # Caption uzunluğu — debug için
        hsonuc.setdefault(
            "caption_uzunluk",
            len(kisa_caption if hedef.platform_kodu == "X" else uzun_caption),
        )
        sonuc[hedef.platform_kodu] = hsonuc

        if hsonuc.get("ok"):
            log_callback(f"      ✅ {hedef.kisa_etiket} OK (id: {hsonuc.get('post_id', '—')})")
        else:
            log_callback(f"      ❌ {hedef.kisa_etiket}: {hsonuc.get('hata')}")

    return sonuc


# ============================================================
# INSTAGRAM REELS — Graph API v21.0
# ============================================================
_IG_GRAPH = "https://graph.facebook.com/v21.0"


def _yukle_instagram(
    hedef: routing.GlobalHesap,
    public_video_url: Optional[str],
    caption: str,
    log_callback: Callable[[str], None],
) -> Dict[str, Any]:
    """
    Instagram Reels publishing — 3 adımlı handshake.
      1) POST /{ig-user-id}/media (media_type=REELS + video_url)
      2) GET /{container-id}?fields=status_code → FINISHED'i bekle
      3) POST /{ig-user-id}/media_publish (creation_id)
    """
    token = hedef.env_anahtarlari.get("TOKEN")
    user_id = hedef.env_anahtarlari.get("USER_ID")

    if not user_id:
        return {"ok": False, "hata": "IG_GLOBAL_USER_ID boş"}
    if not public_video_url:
        return {"ok": False, "hata": "Video public URL'ye yüklenmemiş (IG dosya yüklemez, URL ister)"}

    try:
        # Adım 1: Container yarat
        r = requests.post(
            f"{_IG_GRAPH}/{user_id}/media",
            params={
                "media_type": "REELS",
                "video_url": public_video_url,
                "caption": caption,
                "access_token": token,
            },
            timeout=30,
        )
        if r.status_code != 200:
            return {"ok": False, "hata": f"Container yaratma: HTTP {r.status_code} — {r.text[:200]}"}
        container_id = r.json().get("id")
        if not container_id:
            return {"ok": False, "hata": "Container ID dönmedi"}

        # Adım 2: Polling — FINISHED olana kadar (max 5 dakika, 10sn aralıkla)
        for deneme in range(30):
            time.sleep(10)
            sr = requests.get(
                f"{_IG_GRAPH}/{container_id}",
                params={"fields": "status_code,status", "access_token": token},
                timeout=15,
            )
            if sr.status_code != 200:
                continue
            durum = sr.json().get("status_code", "")
            if durum == "FINISHED":
                break
            if durum == "ERROR":
                hata = sr.json().get("status", "bilinmeyen")
                return {"ok": False, "hata": f"IG video işleme hatası: {hata}"}
        else:
            return {"ok": False, "hata": "IG container 5 dakikada FINISHED olmadı (timeout)"}

        # Adım 3: Yayınla
        p = requests.post(
            f"{_IG_GRAPH}/{user_id}/media_publish",
            params={"creation_id": container_id, "access_token": token},
            timeout=30,
        )
        if p.status_code != 200:
            return {"ok": False, "hata": f"Yayın: HTTP {p.status_code} — {p.text[:200]}"}
        post_id = p.json().get("id")
        return {
            "ok": True,
            "post_id": post_id,
            "url": f"https://www.instagram.com/reel/{post_id}/",
        }

    except requests.RequestException as e:
        return {"ok": False, "hata": f"IG ağ hatası: {e}"}


# ============================================================
# X (TWITTER) — Tweepy ile (medya v1.1, tweet v2)
# ============================================================
def _yukle_x(
    hedef: routing.GlobalHesap,
    video_yolu: str,
    caption: str,
) -> Dict[str, Any]:
    """
    X video tweet'i. Free tier'da:
      - Medya yükleme v1.1 (OAuth1) → API.media_upload(chunked)
      - Tweet oluşturma v2 (OAuth1 user context) → Client.create_tweet
    """
    eksikler = [a for a in ["API_KEY", "API_SECRET", "ACCESS_TOKEN", "ACCESS_SECRET"]
                if not hedef.env_anahtarlari.get(a)]
    if eksikler:
        return {"ok": False, "hata": f"X eksik alanlar: {', '.join(eksikler)}"}

    if not video_yolu or not Path(video_yolu).exists():
        return {"ok": False, "hata": "Video dosyası diskte bulunamadı"}

    try:
        import tweepy
    except ImportError:
        return {"ok": False, "hata": "tweepy kütüphanesi yüklü değil (pip install tweepy)"}

    try:
        # v1.1 — medya yükleme için
        auth = tweepy.OAuth1UserHandler(
            consumer_key=hedef.env_anahtarlari["API_KEY"],
            consumer_secret=hedef.env_anahtarlari["API_SECRET"],
            access_token=hedef.env_anahtarlari["ACCESS_TOKEN"],
            access_token_secret=hedef.env_anahtarlari["ACCESS_SECRET"],
        )
        api_v1 = tweepy.API(auth, wait_on_rate_limit=True)

        # Chunked upload + processing bekleme (X büyük dosyada işliyor)
        media = api_v1.media_upload(
            filename=video_yolu,
            media_category="tweet_video",
            chunked=True,
            wait_for_async_finalize=True,
        )

        # v2 — tweet oluşturma
        client = tweepy.Client(
            consumer_key=hedef.env_anahtarlari["API_KEY"],
            consumer_secret=hedef.env_anahtarlari["API_SECRET"],
            access_token=hedef.env_anahtarlari["ACCESS_TOKEN"],
            access_token_secret=hedef.env_anahtarlari["ACCESS_SECRET"],
        )
        cevap = client.create_tweet(text=caption, media_ids=[media.media_id])
        tweet_id = cevap.data["id"] if cevap and cevap.data else None
        if not tweet_id:
            return {"ok": False, "hata": "Tweet oluşturuldu ama ID dönmedi"}
        return {
            "ok": True,
            "post_id": str(tweet_id),
            "url": f"https://x.com/i/web/status/{tweet_id}",
        }

    except Exception as e:
        return {"ok": False, "hata": f"X hatası: {e}"}


# ============================================================
# TIKTOK — Content Posting API (FILE_UPLOAD)
# ============================================================
_TT_BASE = "https://open.tiktokapis.com/v2"


def _yukle_tiktok(
    hedef: routing.GlobalHesap,
    video_yolu: str,
    public_video_url: Optional[str],
    caption: str,
) -> Dict[str, Any]:
    """
    TikTok Content Posting API:
      - PULL_FROM_URL modu: public_video_url verirsen TikTok kendi indirir
      - FILE_UPLOAD modu: dosyayı chunked yüklersin (büyük dosya için)
    """
    token = hedef.env_anahtarlari.get("TOKEN")
    if not token:
        return {"ok": False, "hata": "TT_GLOBAL_TOKEN boş"}

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # PULL_FROM_URL daha basit — public URL varsa onu tercih et
    if public_video_url:
        try:
            payload = {
                "post_info": {
                    "title": caption[:150],   # TikTok title max 150 karakter
                    "privacy_level": "PUBLIC_TO_EVERYONE",
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                    "video_cover_timestamp_ms": 1000,
                },
                "source_info": {
                    "source": "PULL_FROM_URL",
                    "video_url": public_video_url,
                },
            }
            r = requests.post(
                f"{_TT_BASE}/post/publish/video/init/",
                headers=headers, json=payload, timeout=30,
            )
            if r.status_code != 200:
                return {"ok": False, "hata": f"TT init: HTTP {r.status_code} — {r.text[:200]}"}
            veri = r.json().get("data", {})
            publish_id = veri.get("publish_id")
            if not publish_id:
                return {"ok": False, "hata": "TT publish_id dönmedi"}
            # NOT: TikTok asenkron işliyor. publish_id'yi sakla, durum sorgulayabilirsin.
            return {
                "ok": True,
                "post_id": publish_id,
                "url": "https://www.tiktok.com/@balkanlardanhaber",
                "not": "TikTok işleme asenkron, durumu /post/publish/status/fetch/ ile sorgula",
            }
        except requests.RequestException as e:
            return {"ok": False, "hata": f"TT ağ hatası: {e}"}

    # FILE_UPLOAD modu (public URL yoksa)
    return {"ok": False, "hata": "FILE_UPLOAD modu henüz implement edilmedi (public URL kullan)"}


# ============================================================
# FACEBOOK — Page video
# ============================================================
def _yukle_facebook(
    hedef: routing.GlobalHesap,
    public_video_url: Optional[str],
    caption: str,
) -> Dict[str, Any]:
    """Facebook Page'e video paylaşma (Graph API)."""
    token = hedef.env_anahtarlari.get("TOKEN")
    page_id = hedef.env_anahtarlari.get("PAGE_ID")
    if not page_id:
        return {"ok": False, "hata": "FB_GLOBAL_PAGE_ID boş"}
    if not public_video_url:
        return {"ok": False, "hata": "FB için video public URL gerekli"}

    try:
        r = requests.post(
            f"https://graph.facebook.com/v21.0/{page_id}/videos",
            params={
                "file_url": public_video_url,
                "description": caption,
                "access_token": token,
            },
            timeout=60,
        )
        if r.status_code != 200:
            return {"ok": False, "hata": f"FB: HTTP {r.status_code} — {r.text[:200]}"}
        veri = r.json()
        return {
            "ok": True,
            "post_id": veri.get("id", ""),
            "url": f"https://www.facebook.com/{page_id}",
        }
    except requests.RequestException as e:
        return {"ok": False, "hata": f"FB ağ hatası: {e}"}


# ============================================================
# YOUTUBE SHORTS — Data API v3
# ============================================================
def _yukle_youtube(
    hedef: routing.GlobalHesap,
    video_yolu: str,
    caption: str,
) -> Dict[str, Any]:
    """
    YouTube Shorts. Data API v3 üzerinden upload.
    OAuth2 access token gerekir; refresh ayrıca yönetilmeli.
    Not: Shorts olabilmesi için video #Shorts hashtag'i içermeli VE
    dikey + <= 60 saniye olmalı.
    """
    token = hedef.env_anahtarlari.get("TOKEN")
    if not token:
        return {"ok": False, "hata": "YT_GLOBAL_TOKEN boş"}
    if not video_yolu or not Path(video_yolu).exists():
        return {"ok": False, "hata": "Video dosyası diskte bulunamadı"}

    # Caption'a #Shorts ekle (zaten varsa duplikat eklemez)
    yt_caption = caption if "#Shorts" in caption else f"{caption}\n#Shorts"

    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google.oauth2.credentials import Credentials
    except ImportError:
        return {"ok": False, "hata": "google-api-python-client yüklü değil"}

    try:
        creds = Credentials(token=token)
        yt = build("youtube", "v3", credentials=creds)
        baslik_kisa = yt_caption.split("\n")[0][:100]  # YT max 100 karakter
        body = {
            "snippet": {
                "title": baslik_kisa,
                "description": yt_caption,
                "categoryId": "25",  # News & Politics
            },
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
        }
        medya = MediaFileUpload(video_yolu, chunksize=-1, resumable=True, mimetype="video/mp4")
        istek = yt.videos().insert(part="snippet,status", body=body, media_body=medya)
        cevap = istek.execute()
        return {
            "ok": True,
            "post_id": cevap.get("id"),
            "url": f"https://youtube.com/shorts/{cevap.get('id')}",
        }
    except Exception as e:
        return {"ok": False, "hata": f"YT hatası: {e}"}
