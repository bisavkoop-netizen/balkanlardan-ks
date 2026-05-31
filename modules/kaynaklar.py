"""
=================================================================
modules/kaynaklar.py — Kültür-Sanat Kaynak Kayıt Defteri (v4)
=================================================================
ESKİ whitelist.py'NİN ARDINDAN — Felsefe Değişti:

Eski model: "Güvenilir haber sitelerinden son haberleri çek."
  → GNews + site: operatörü + 24 saat penceresi
  → Sonuç: haber döngüsü içinde sıkışmış içerik

Yeni model: "Balkan kültürünün hazinelerinden ilham al."
  → Üç farklı kaynak tipi paralel çalışır:
     1) RSS feed'leri (yavaş, derin — UNESCO, müzeler, kültür blogları)
     2) Sosyal işaretçiler (Instagram/YT hesap isimleri — sadece referans,
        otomatik scrape DEĞİL; küratörün ilham listesi)
     3) Manuel "saha" girişi (en güçlü — UI üzerinden gelen ham notlar)

Kritik karar: Otomatik mod artık ZAMAN-DUYARSIZ konular üretir.
"Bugün ne oldu?" değil, "Balkanlar'ın hangi unutulmuş hikayesini
bugün gün yüzüne çıkaralım?" sorusunu cevaplar.

RSS feed'leri opsiyonel başlangıç noktasıdır; sistemin ana motoru
trend_motoru.py içindeki "konu havuzu" + Claude'un kendi bilgisidir.
=================================================================
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from . import config


# ============================================================
# KAYNAK TİPLERİ
# ============================================================
@dataclass(frozen=True)
class RSSKaynak:
    """RSS feed kaynakları — periyodik tarama için."""
    url: str
    ad: str
    dil: str
    tema: str  # config.ICERIK_TEMALARI'ndan biri
    ulke_etiketi: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class SosyalIsaretci:
    """
    Bir sosyal medya hesabı — otomatik scrape YAPMAYIZ.
    Bu hesaplar editörün/sistemin "ilham listesi"dir; UI'da
    kullanıcıya "şu hesaba bak, oradan konu çıkar" diye gösterilir.
    """
    platform: str  # 'IG', 'YT', 'TT'
    handle: str    # '@balkaninsight' gibi
    aciklama: str
    temalar: List[str] = field(default_factory=list)


# ============================================================
# RSS FEED LİSTESİ
# Not: URL'ler en yaygın bilinen kültür kaynaklarıdır. Yine de
# canlıya geçmeden önce her birini elle test et — RSS sürekli kırılır.
# ============================================================
RSS_KAYNAKLARI: List[RSSKaynak] = [
    # === Genel Balkan kültürü ===
    RSSKaynak(
        url="https://balkaninsight.com/feed/",
        ad="Balkan Insight — Culture",
        dil="EN",
        tema="tarih_hikaye",
        ulke_etiketi=["TR", "GR", "BG", "RS", "BA", "MK", "HR", "ME", "AL", "XK", "RO", "SI"],
    ),
    RSSKaynak(
        url="https://balkanist.net/feed/",
        ad="Balkanist Magazine",
        dil="EN",
        tema="kayip_kultur",
        ulke_etiketi=["RS", "BA", "MK", "HR", "ME", "AL", "XK", "BG"],
    ),

    # === UNESCO + miras ===
    # UNESCO Memory of the World — bölgesel
    RSSKaynak(
        url="https://en.unesco.org/feed/news/all",
        ad="UNESCO News (filtre: Balkan)",
        dil="EN",
        tema="kayip_kultur",
        ulke_etiketi=["GR", "BG", "RS", "BA", "MK", "HR", "ME", "AL", "RO", "SI"],
    ),

    # === Müzik ===
    # World Music Network sık sık Balkan derlemeleri yayınlıyor
    RSSKaynak(
        url="https://worldmusic.net/blogs/news.atom",
        ad="World Music Network",
        dil="EN",
        tema="muzik",
        ulke_etiketi=["TR", "GR", "BG", "RS", "BA", "MK", "AL"],
    ),

    # === Türkçe kültür kaynakları ===
    RSSKaynak(
        url="https://www.tarihvearkeoloji.com/feeds/posts/default",
        ad="Tarih ve Arkeoloji",
        dil="TR",
        tema="tarih_hikaye",
        ulke_etiketi=["TR", "GR", "BG"],
    ),

    # NOT: Yeni RSS feed eklemek için ekibi düzenli besle. Bu liste 
    # canlı bir belge; ne kadar zengin olursa Claude'un "ilham havuzu" o kadar 
    # geniş olur. Ama unutma: Claude'un kendi bilgisi de büyük bir kaynaktır
    # — bu yüzden RSS olmasa bile sistem konu üretebiliyor olmalı.
]


# ============================================================
# SOSYAL İLHAM HESAPLARI (otomatik scrape YOK!)
# Bunlar editör masasında "konu fikri" tetikleyici olarak gösterilir.
# ============================================================
SOSYAL_ILHAM: List[SosyalIsaretci] = [
    SosyalIsaretci("IG", "@balkanstories",     "Balkan halklarının görsel arşivi",        ["tarih_hikaye", "kisi_portre"]),
    SosyalIsaretci("IG", "@balkan.heritage",   "Mimari ve geleneksel objeler",            ["mimari", "el_sanati"]),
    SosyalIsaretci("IG", "@balkan_folklore",   "Halk dansı + müzik kayıtları",            ["muzik", "gelenek"]),
    SosyalIsaretci("IG", "@ottomanlegacy",     "Osmanlı dönemi mirası, Balkanlar dahil",  ["mimari", "tarih_hikaye"]),
    SosyalIsaretci("YT", "@CovekWorld",        "Balkan müzik fieldwork videoları",        ["muzik", "kayip_kultur"]),
    SosyalIsaretci("YT", "@FolkSeen",          "Geleneksel danslar + canlı kayıtlar",     ["gelenek", "muzik"]),
    SosyalIsaretci("TT", "@balkangirl",        "Yeni nesil Balkan kimliği — viral",       ["dil_edebiyat", "yemek"]),
    SosyalIsaretci("TT", "@balkanfoodlover",   "Yöresel yemek hazırlama videoları",       ["yemek", "gelenek"]),
]


# ============================================================
# "KONU TOHUMU" HAVUZU
# RSS gelmediği günler için Claude'a verilebilecek hazır ilham kıvılcımları.
# Trend motoru bunlardan rastgele seçer → Claude o tohumdan içerik geliştirir.
# ============================================================
KONU_TOHUMLARI: Dict[str, List[str]] = {
    "muzik": [
        "Sevdalinka — Bosna'nın aşk türküsü neden hep hüzünlü?",
        "Rebetiko ve Türk meyhane geleneği — kuzen müzikler",
        "Kaba zurna ve davul: Trakya'nın iki yakası, tek ritim",
        "Bulgaristan'ın 'Mistic Voices' korosu — Grammy nasıl kazandı?",
        "Çalgia: Makedon şehir müziğinin Osmanlı kökeni",
        "Bosna'da Saz ve sevdah — kayıp ustaları",
        "Romanya'nın doina'sı — UNESCO'nun koruduğu hüzün",
        "Arnavut polifonik şarkıları — bin yıllık çok seslilik",
    ],
    "yemek": [
        "Boza — Balkanların ortak içeceği, kimin icadı?",
        "Sarma'nın Türkçe, Yunanca, Sırpça adları — aynı yaprak",
        "Baklava savaşları — Türk mü, Yunan mı, Boşnak mı?",
        "Ćevapi: 5 farklı ülke, 5 farklı versiyon",
        "Tarator ve cacık — Balkan yazının buzlu kaşığı",
        "Halva geleneği — Üsküp'ten İstanbul'a tatlı yol",
        "Bulgar yoğurdu efsanesi — 'lactobacillus bulgaricus' nereden geldi?",
        "Pastırma'nın Bizans kökeni ve adlandırma kavgası",
    ],
    "mimari": [
        "Osmanlı çeşmeleri — Saraybosna'dan Selanik'e su mimarisi",
        "Berat — Arnavutluk'un 'bin pencereli şehri' sırrı",
        "Veliko Tarnovo: Ortaçağ'ın Balkan başkenti",
        "Mostar Köprüsü — yıkım, yeniden doğuş, anlam",
        "Plovdiv'in renkli evleri — Bulgar Ulusal Uyanış mimarisi",
        "Manastırlar ve fresk geleneği — Sırp Aynalı Manastır",
        "Konyalı Ahmet Ağa: Balkan camilerinin meçhul mimarı",
        "Sutivan ve Adriyatik taş mimari",
    ],
    "gelenek": [
        "Nestinari — Bulgar ateş üstünde dans ayini, hâlâ canlı",
        "Kukeri: Bulgar kış maskeleri ve Slav pagan kökeni",
        "Selanik düğün adetleri — Osmanlı'dan kalan ne, ne değil?",
        "Vlachs (Aromanlar) — Balkan'ın görünmez göçebeleri",
        "Romanya'da 'Mărțișor' — Mart bahar bilezikleri",
        "Pomak düğünleri — gelinin yüzü neden kapalı boyalı?",
        "Sırp slava — aile koruyucu azizi geleneği",
        "Arnavut 'besa' kavramı — verilen söze ölesiye sadakat",
    ],
    "tarih_hikaye": [
        "Hasan Kafi Pruščak — 16. yy'da Bosna'nın filozofu, kim?",
        "Skanderbeg'in 25 yıllık direnişi — Arnavut milli kahraman",
        "Karadağ tarihinde 'vilna' — kadınlar erkek gibi yaşadı",
        "Selanikli Müslümanlar — 'Dönmeler'in unutulmuş izi",
        "Plovdiv'in üç ismi: Philippopolis, Filibe, Plovdiv",
        "Boğdan ve Eflak — Romanya'nın 'Osmanlı dönemi' sessizliği",
        "Hıdırellez — Balkanların ortak baharı, farklı isimler",
        "Janissari ocağı — Balkan çocukları İstanbul'da nasıl yönetti?",
    ],
    "el_sanati": [
        "Kilim desenleri — Anadolu'dan Bosna'ya yolculuğu",
        "Filigran (telkari) gümüş işçiliği — Selanik, Üsküp, Saraybosna",
        "İznik çinisinin Balkan camilerine yolculuğu",
        "Pirot kilimleri — Sırbistan'ın UNESCO mirası",
        "Çakır boyası ve Bulgar şal dokuması",
        "Manastır gümüş işçiliği — Bizans'tan kalan teknik",
        "Romanya'nın 'Ie' bluzu — geometri ve büyü",
    ],
    "dil_edebiyat": [
        "Karadeniz'den Adriyatik'e: Türkçe alıntı kelimeler nasıl yolculuk etti?",
        "'Sevda' kelimesinin 7 Balkan dilinde anlamı",
        "Yaşar Kemal ve Balkan göçü edebiyatı",
        "Ivo Andrić ve 'Drina Köprüsü' — Bosna bilinçaltı",
        "Konstantin Kavafis — İskenderiye'deki Balkan sesi",
        "Pomak Türkçesi — Bulgaristan'da hayatta kalan lehçe",
        "Roman dilinin Balkan dağılımı — neden hep hareket halinde?",
    ],
    "festival": [
        "Guča Brass Band Festivali — Sırbistan'ın yıllık ses patlaması",
        "Sziget'in Balkan kuzenleri — EXIT, Mostar, Saraybosna",
        "Edirne Kakava — Balkan Romanlarının baharı",
        "Veliko Tarnovo Ortaçağ Festivali",
        "Ohrid Yaz Festivali — Makedonya'nın klasik buluşması",
    ],
    "kisi_portre": [
        "Esma Redžepova — 'Romanların Kraliçesi' Makedonya'dan",
        "Goran Bregović — Saraybosna'dan dünya sahnesine",
        "Mikis Theodorakis ve Türkiye'yi sevmesi",
        "Sezen Aksu'nun Balkan müziğine borcu",
        "Marija Šerifović — Eurovision'ı kazanan Sırp ses",
        "Yashar Nezhat — Arnavut bir sufi şair",
    ],
    "kayip_kultur": [
        "Aromanlar (Ulahlar) — son nesil çobanları konuşuyor",
        "Pomaklar — Bulgar/Yunan/Türk üçgeninde sıkışmış kimlik",
        "Goranlar — Kosova dağlarında bir İslam-Slav adacığı",
        "Sefarad Yahudilerinin Ladinosu — Selanik'in unutulmuş dili",
        "Yörükler ve Balkan yörükleri — yarı göçebe iz",
        "Karadağ Kuçi aşireti — efsanevi bağımsız dağ topluluğu",
    ],
}


# ============================================================
# API
# ============================================================
def rss_kaynaklari_listele(
    dil: Optional[str] = None,
    tema: Optional[str] = None,
    ulke: Optional[str] = None,
) -> List[RSSKaynak]:
    """Filtreli RSS listesi — UI ve trend motoru çağırır."""
    sonuc = list(RSS_KAYNAKLARI)
    if dil:
        sonuc = [k for k in sonuc if k.dil == dil]
    if tema:
        sonuc = [k for k in sonuc if k.tema == tema]
    if ulke:
        sonuc = [k for k in sonuc if ulke in k.ulke_etiketi]
    return sonuc


def konu_tohumu_sec(
    tema: Optional[str] = None,
    haric_tutulanlar: Optional[List[str]] = None,
) -> Optional[str]:
    """
    Otomatik üretim için: belirtilen temadan (yoksa rastgele temadan)
    bir konu tohumu döndürür. 'haric_tutulanlar' aynı tohumun tekrar
    seçilmesini engellemek için — storage'dan üretim geçmişi gelir.
    """
    import random
    havuz: List[str] = []
    haric = set(haric_tutulanlar or [])

    if tema:
        havuz = [t for t in KONU_TOHUMLARI.get(tema, []) if t not in haric]
    else:
        for t_listesi in KONU_TOHUMLARI.values():
            havuz.extend(t for t in t_listesi if t not in haric)

    return random.choice(havuz) if havuz else None


def tum_temalar() -> List[str]:
    """Sidebar dropdown'ı için."""
    return list(KONU_TOHUMLARI.keys())


# ============================================================
# PEXELS TEMA TERİMLERİ — STOK VİDEO ARAMA HAVUZU
# ============================================================
# Bu havuzlar deneye deneye iyileştirilir. Buradaki kural:
#   • Niş kültürel terim YOK (zurna, sevdalinka, ćevapi gibi —
#     Pexels'te bulunmaz).
#   • Görsel cue'lar somut: nesne + eylem + ortam.
#   • İngilizce — Pexels'in arama motoru EN'de en güçlü.
#   • Her tema için 12-15 terim — birden fazla video üretirken
#     çeşitlilik için.
#
# YENİ TERİM EKLEMEK İÇİN:
#   1) Pexels'te (https://www.pexels.com/videos/) elle ara
#   2) En az 5-10 sonuç varsa ve göreseller temaya uyuyorsa ekle
#   3) Test et: python -c "from modules import kaynaklar; ..."
# ============================================================
PEXELS_TEMA_TERIMLERI: Dict[str, List[str]] = {
    "muzik": [
        # Genel (mevcut)
        "musicians playing outdoor",
        "hands playing acoustic guitar",
        "string instrument close up",
        "wooden flute outdoor player",
        "drum hands rhythm",
        "village festival music",
        "dance circle traditional",
        "old vinyl record spinning",
        "musician playing alone street",
        "folk band performing outdoor",
        # Coğrafi imli (Balkan/Akdeniz)
        "mediterranean village musician",
        "greek traditional music",
        "turkish folk musician",
        "european village band",
        "rustic guitar player outdoor",
        "old man playing instrument village",
        # Spesifik enstrümanlar (Parça 6 cilalama)
        "bouzouki player",
        "mandolin player outdoor",
        "accordion folk music",
        "acoustic guitar singer outdoor",
    ],
    "yemek": [
        # Genel (mevcut)
        "wooden table rustic food",
        "hands kneading dough",
        "steam from clay pot",
        "old market vegetables",
        "bread baking oven",
        "pouring hot drink cup",
        "herbs spices wooden spoon",
        "grandmother cooking traditional",
        "open fire cooking outdoor",
        "tea ceremony pouring",
        "olive oil bottle pour",
        "cheese aging cellar",
        "honey jar dipping",
        "stone oven bread",
        # Coğrafi imli (Balkan/Akdeniz)
        "turkish coffee old cup",
        "mediterranean kitchen cooking",
        "greek olive oil traditional",
        "european market old town",
        "rustic bread village oven",
        "turkish tea glass pouring",
        # Konu-bağı cilalama (Parça 6)
        "yogurt making traditional",
        "turkish coffee brewing copper",
        "grilling meat skewers outdoor",
        "grape harvest vineyard",
        "mediterranean street food vendor",
    ],
    "mimari": [
        # Genel + coğrafi karışık (mimari zaten coğrafi)
        "old stone wall ancient",
        "ottoman architecture building",
        "cobblestone street old town",
        "fountain water flowing historic",
        "wooden door old carved",
        "mosque minaret sky",
        "monastery courtyard quiet",
        "bridge stone river old",
        "rooftops city aerial historic",
        "window shutters mediterranean",
        "ruins sunset golden",
        "church bell tower",
        "ancient column ruins",
        "narrow alley old city",
        # Daha coğrafi imli
        "mediterranean village stone",
        "greek island white houses",
        "turkish mosque sunset",
        "european old town square",
        "balkan village rooftops",
        # Cilalama — Osmanlı/Bizans/manastır estetiği
        "stone fountain old city",
        "carved marble detail ancient",
        "arched doorway stone building",
        "byzantine church mosaic",
        "ottoman bath hammam interior",
        "stone staircase old town",
        "medieval fortress wall aerial",
        "greek orthodox monastery",
        "tile roof mediterranean aerial",
        "old bazaar covered market",
    ],
    "gelenek": [
        "traditional costume dance",
        "folk dancers circle",
        "village wedding celebration",
        "religious ceremony candle",
        "festival fire night",
        "elders gathering village",
        "ritual hands prayer",
        "traditional fabric weaving",
        "procession street celebration",
        "bonfire night gathering",
        "harvest field workers",
        "incense smoke ritual",
        # Coğrafi imli
        "mediterranean village celebration",
        "greek traditional dancers",
        "turkish wedding traditional",
        "european folk costume",
        "balkan village ritual",
        # Konu-bağı cilalama (Parça 6) — ateş, halka, dilek ritüelleri
        "spring bonfire ritual",
        "jumping over fire celebration",
        "wishing tree ribbons",
        "european folk musicians street",
        "greek women folk dance",
        "european lantern night festival",
    ],
    "tarih_hikaye": [
        "old map vintage paper",
        "ancient manuscript book",
        "ruins archaeological site",
        "old letter handwritten",
        "candle dark medieval",
        "stone carving ancient text",
        "museum artifact display",
        "old library books dust",
        "abandoned village stones",
        "sepia old photograph",
        "fortress wall sunset",
        "shadow figure walking historic",
        # Coğrafi imli
        "mediterranean ruins ancient",
        "greek temple stone",
        "turkish ottoman manuscript",
        "european medieval castle",
        "balkan fortress ruins",
        # Cilalama — arşiv, mozaik, harabe, müze
        "old coins scattered table",
        "ancient mosaic floor detail",
        "museum glass display artifact",
        "crumbling stone wall ivy",
        "torch flame stone corridor",
        "aged parchment scroll close",
        "archaeological dig site workers",
        "medieval sword armor display",
        "historical document ink faded",
        "ruins overgrown sunset golden",
    ],
    "el_sanati": [
        "hands weaving loom",
        "potter clay wheel turning",
        "blacksmith hammer forge",
        "wood carving hands",
        "silver jewelry making",
        "embroidery hands needle",
        "leather craftsman work",
        "knitting wool hands",
        "carpet weaving traditional",
        "calligraphy ink brush",
        "stone carving sculptor",
        "ceramic painting hands",
        # Coğrafi imli
        "turkish carpet weaving",
        "mediterranean pottery hands",
        "ottoman calligraphy ink",
        "european craftsman workshop",
        "balkan traditional craft",
        # Cilalama — dokuma, gümüş, çini, ahşap oyma
        "silver filigree jewelry close up",
        "copper hammering craftsman",
        "silk thread loom detail",
        "hand painted ceramic tile",
        "glassblowing traditional craft",
        "wool dyeing natural color",
        "basket weaving hands close",
        "goldsmith fine work bench",
        "woodworking chisel hands",
        "felt making wool pressing",
    ],
    "dil_edebiyat": [
        "old book pages turning",
        "handwritten letter ink",
        "library reading quiet",
        "poet writing notebook",
        "open book candlelight",
        "scroll ancient text",
        "fountain pen writing",
        "bookshelf wooden old",
        "manuscript illuminated",
        "person reading window light",
        "stack books vintage",
        # Coğrafi imli
        "ottoman manuscript old",
        "greek ancient scroll",
        "european old library",
        "turkish calligraphy writing",
        # Cilalama — kütüphane, yazma eser, kaligraf
        "ink bottle quill feather",
        "aged book cover leather",
        "reading lamp desk old",
        "dusty books archive shelf",
        "writing desk candle night",
        "illuminated manuscript gold",
        "letter sealing wax stamp",
        "open journal blank pages",
        "typewriter vintage keys",
        "pressed flower old book",
    ],
    "festival": [
        "festival lights crowd",
        "street parade flags",
        "fireworks night celebration",
        "lanterns night festival",
        "crowd dancing outdoor",
        "bonfire celebration night",
        "drums marching parade",
        "open air market festival",
        "torch procession night",
        "village square celebration",
        # Coğrafi imli
        "mediterranean village festival",
        "greek island celebration",
        "turkish festival traditional",
        "european folk festival",
        "balkan village square celebration",
        # Konu-bağı cilalama (Parça 6) — bahar şenliği, sokak yemeği, dans yakın çekim
        "spring festival outdoor crowd",
        "street food festival",
        "traditional dance feet close up",
        "wine festival celebration",
        "mediterranean street celebration",
    ],
    "kisi_portre": [
        "portrait elder face wisdom",
        "hands wrinkled old story",
        "thoughtful person window",
        "old man cafe pensive",
        "woman traditional dress portrait",
        "craftsman workshop portrait",
        "elderly hands prayer",
        "shepherd portrait field",
        "weathered face portrait",
        # Coğrafi imli
        "mediterranean old man portrait",
        "greek elder fisherman",
        "turkish old man cafe",
        "european village elder",
        "balkan shepherd mountain",
        # Cilalama — yaşlı müzisyen, çoban, yerel esnaf
        "old musician playing alone",
        "elderly woman embroidery portrait",
        "fisherman net mending portrait",
        "farmer field sunset portrait",
        "old woman market vendor",
        "mountain man rugged portrait",
        "village elder storytelling",
        "artisan hands close up portrait",
        "grandmother kitchen portrait",
        "monk monastery portrait",
    ],
    "kayip_kultur": [
        "abandoned village empty",
        "old photograph faded",
        "ruins overgrown nature",
        "empty chair memory",
        "fading sunset window",
        "elderly hands holding photo",
        "abandoned house door",
        "rural mountain remote",
        "lonely shepherd field",
        "weathered wooden sign",
        "broken pottery ground",
        "misty mountain village",
        "fog forest atmospheric",
        "candle burning low",
        # Coğrafi imli
        "mediterranean abandoned village",
        "greek deserted island",
        "balkan mountain ruins",
        "european forgotten village",
        "turkish ottoman ruins",
        # Cilalama — eski fotoğraf, terk edilmiş, hayalet kasaba
        "overgrown cemetery stone",
        "dust covered old room",
        "cracked wall peeling paint",
        "old clock stopped time",
        "empty school desk old",
        "abandoned church interior",
        "faded family portrait wall",
        "worn stone path forest",
        "last light village window",
        "fog mountain abandoned road",
    ],
}


def pexels_terimleri_sec(tema: str, sayi: int = 4) -> List[str]:
    """
    Belirtilen tema için rastgele N adet Pexels arama terimi döner.

    Args:
        tema: config.ICERIK_TEMALARI'ndan biri (örn 'muzik')
        sayi: Kaç terim istiyorsun (varsayılan 4 = video özet sahnesi sayısı)

    Returns:
        Karıştırılmış N terim listesi. Eğer tema bulunamazsa
        'tarih_hikaye'ye düşer (en geniş havuz). Hiç bulamazsa boş liste.
    """
    import random
    havuz = PEXELS_TEMA_TERIMLERI.get(tema)
    if not havuz:
        # Bilinmeyen tema → güvenli fallback
        havuz = PEXELS_TEMA_TERIMLERI.get("tarih_hikaye", [])
    if not havuz:
        return []
    # Kopyala ki orijinal listenin sırası bozulmasın
    karisik = list(havuz)
    random.shuffle(karisik)
    return karisik[:sayi]


def tum_pexels_temalari() -> List[str]:
    """Hangi temalar için Pexels havuzu var? — UI/test için."""
    return list(PEXELS_TEMA_TERIMLERI.keys())
