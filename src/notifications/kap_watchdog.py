"""
KAP Watchdog (Nöbetçi): 7/24 KAP bildirimlerini dinler, hisse kodunu (ticker) yakalar.
BIST100 içindeki semboller için Gemini haber filtresinden geçirir; kritik haberlerde
run_single_symbol_scan(ticker) tetiklenir. Ayrıca aktif pozisyondaki hisseler için
negatif haber (Gemini: Negatif, puan 7+) geldiğinde acil satış uyarısı gönderilir.

Veri kaynağı önceliği: 1) Investing.com (cloudscraper), 2) RSS, 3) KAP API (cloudscraper),
4) HTML fallback. Investing haberlerinde '(KAP)' veya 4-5 harfli hisse kodu filtresi uygulanır;
Gemini analyze_news ile Kritik + Puan >= 7 ise on_critical_news tetiklenir.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import requests

# Proje kökü (import'lar için) — döngü içinde değil, fonksiyon içinde kullanıldığı için
# load_bist_symbols run_kap_watchdog_loop'ta _load_bist100_set üzerinden kullanılır
def _get_load_bist_symbols():
    try:
        from src.scrapers.tv_scraper import load_bist_symbols
        return load_bist_symbols
    except Exception:
        return None

# Investing.com borsa haberleri (KAP / BIST içerikli; cloudscraper ile taranır)
INVESTING_NEWS_URL = "https://tr.investing.com/news/stock-market-news"
# Başlıkta hisse kodu: 4-5 büyük harf (BIST sembolleri)
TICKER_REGEX = re.compile(r"\b([A-Z]{4,5})\b")

KAP_DISCLOSURES_API = "https://www.kap.org.tr/tr/api/disclosures"
# RSS / anlık listele sayfaları (feedparser ile dene)
KAP_RSS_URLS = [
    "https://www.kap.org.tr/tr/bildirimleri-anlik-listele",
    "https://www.kap.org.tr/tr/bildirimleri-id-sirali-listele",
    "https://www.kap.org.tr/tr/rss",
    "https://www.kap.org.tr/tr/feed",
]
KAP_POLL_INTERVAL_SEC = 60
USER_AGENT = "BIST100Sentinel/1.0 (Education; kap.org.tr)"

# Kotayı korumak: Sadece bu anahtar kelimelerden en az biri geçen haberler Gemini'ye gider
GEMINI_PRE_FILTER_KEYWORDS = [
    "temettü", "temettu", "ihale", "satış", "satis", "kâr", "kar ", "kar.", "kar,", "sözleşme",
    "sozlesme", "sermaye", "dava", "ceza", "kar payı", "kar payi", "anlaşma", "anlasma",
    "ihracat", "şirket", "sirket", "açıklama", "aciklama", "büyük", "buyuk", "ortaklık",
    "ortaklik", "hisse", "devir", "alım", "alim", "satın", "satin", "ihraç", "ihrac",
]
# Her Gemini analizi arası bekleme (saniye) — Free Tier RPM limitini aşmamak için
GEMINI_DELAY_BETWEEN_CALLS_SEC = 4


@dataclass
class KapDisclosure:
    """Tek bir KAP bildirimi."""
    disclosure_id: str
    title: str
    summary: str
    ticker: Optional[str] = None
    link: Optional[str] = None
    published: Optional[datetime] = None


def _load_bist100_set(symbol_list_path: Optional[Path] = None) -> set[str]:
    """BIST100 hisse kodları seti (örn. {'THYAO', 'AKBNK'})."""
    load_bist_symbols = _get_load_bist_symbols()
    if load_bist_symbols is None:
        return set()
    symbols = load_bist_symbols(symbol_list_path)
    return {sym.upper() for sym, _ in symbols}


def _title_looks_potentially_critical(title: str, summary: str = "") -> bool:
    """
    Kotayı korumak: Başlık/özet potansiyel kritik anahtar kelime içeriyorsa True.
    Sadece bu haberler Gemini'ye gönderilir.
    """
    if not title:
        return False
    text = (title + " " + (summary or "")).lower()
    for kw in GEMINI_PRE_FILTER_KEYWORDS:
        if kw.lower() in text:
            return True
    return False


def _extract_ticker_from_text(text: str, bist100_set: set[str]) -> Optional[str]:
    """
    Metinden BIST100'de olan hisse kodunu bulur.
    Önce tam eşleşme (parça kelime), sonra 2-5 harfli borsa kodu aranır.
    """
    if not text or not bist100_set:
        return None
    text_upper = text.upper()
    # Tam sembol geçiyor mu? (kelime sınırı: boşluk, parantez, virgül vb.)
    for ticker in bist100_set:
        # "THYAO" veya "(THYAO)" veya "THYAO -" gibi
        if re.search(r"\b" + re.escape(ticker) + r"\b", text_upper):
            return ticker
    # KAP bazen "KAP'da THYAO için bildirim" gibi yazar
    for ticker in bist100_set:
        if ticker in text_upper:
            return ticker
    return None


def _title_matches_kap_or_ticker(title: str) -> bool:
    """Başlık '(KAP)' içeriyor veya 4-5 harfli büyük harf (ticker) içeriyor mu?"""
    if not title or len(title) < 4:
        return False
    if "(KAP)" in title or "(Kap)" in title:
        return True
    return bool(TICKER_REGEX.search(title))


def _extract_tickers_from_title(title: str) -> list[str]:
    """Başlıktan [A-Z]{4,5} formatındaki olası hisse kodlarını döner (BIST100 sonradan filtrelenir)."""
    return list(TICKER_REGEX.findall(title))


def _fetch_via_investing(limit: int, timeout: int = 20) -> list[KapDisclosure]:
    """
    Investing.com borsa haberleri sayfasını cloudscraper ile tarar.
    Başlığında '(KAP)' geçen veya 4-5 harfli büyük harf (ticker) içeren haberleri filtreler;
    başlık + özet KapDisclosure olarak döner (ticker başlıktan regex ile ayıklanır, BIST100 loop'ta filtrelenir).
    """
    result: list[KapDisclosure] = []
    try:
        import cloudscraper
        scraper = cloudscraper.create_scraper()
        resp = scraper.get(INVESTING_NEWS_URL, timeout=timeout)
        if not resp.ok or not resp.text:
            return result
        html = resp.text
    except Exception:
        return result

    # Haber linki: tr.investing.com/news/... ile başlayan, başlık </a> ile biten
    link_re = re.compile(
        r'<a[^>]+href="(https://tr\.investing\.com/news/[^"]+)"[^>]*>([^<]+)</a>',
        re.IGNORECASE,
    )
    seen = set()
    for m in link_re.finditer(html):
        url, title = m.group(1), re.sub(r"\s+", " ", m.group(2).strip())
        if not title or len(title) < 10:
            continue
        if not _title_matches_kap_or_ticker(title):
            continue
        if url in seen:
            continue
        seen.add(url)

        # Özet: </a> sonrası 800 karakterde HTML tag'leri temizlenip ilk anlamlı metin alınır
        summary = ""
        rest = html[m.end() : m.end() + 800]
        rest_clean = re.sub(r"<[^>]+>", " ", rest)
        rest_clean = re.sub(r"\s+", " ", rest_clean).strip()
        if len(rest_clean) >= 20:
            summary = rest_clean[:2000]

        # Ticker loop'ta _extract_ticker_from_text(title+summary, bist100) ile BIST100'e göre ayıklanır
        result.append(KapDisclosure(
            disclosure_id=url,
            title=title,
            summary=summary,
            ticker=None,
            link=url,
        ))
        if len(result) >= limit:
            break
    return result


def _parse_iso_date(entry) -> Optional[datetime]:
    """feedparser entry'den published_parsed veya updated_parsed ile datetime döner."""
    for key in ("published_parsed", "updated_parsed"):
        val = getattr(entry, key, None)
        if val and isinstance(val, time.struct_time):
            try:
                return datetime(*val[:6])
            except Exception:
                pass
    return None


def _fetch_via_rss(limit: int, timeout: int = 15) -> list[KapDisclosure]:
    """
    RSS beslemesinden son bildirimleri çeker. KAP_RSS_URLs listesindeki URL'ler
    sırayla feedparser ile denenir; geçerli RSS/Atom dönen ilk kaynaktan doldurulur.
    """
    result: list[KapDisclosure] = []
    try:
        import feedparser
    except ImportError:
        return result
    for url in KAP_RSS_URLS:
        try:
            feed = feedparser.parse(url, request_headers={"User-Agent": USER_AGENT}, timeout=timeout)
            if getattr(feed, "bozo", False) and not feed.entries:
                continue
            entries = getattr(feed, "entries", [])[:limit]
            for e in entries:
                title = (e.get("title") or "").strip()
                if not title or len(title) < 5:
                    continue
                link = e.get("link") or ""
                summary = (e.get("summary") or e.get("description") or "").strip()
                if isinstance(summary, dict) and summary.get("value"):
                    summary = summary["value"]
                summary = summary[:2000] if summary else ""
                did = str(e.get("id") or e.get("link") or hash(title + link))
                pub = _parse_iso_date(e)
                result.append(KapDisclosure(
                    disclosure_id=did,
                    title=title,
                    summary=summary,
                    ticker=None,
                    link=link if link else None,
                    published=pub,
                ))
            if result:
                return result[:limit]
        except Exception:
            continue
    return result


def _fetch_via_cloudscraper_api(limit: int, timeout: int = 20) -> list[KapDisclosure]:
    """
    Cloudscraper ile KAP API'ye istek atar; 666 / Cloudflare engelini aşmayı dener.
    """
    result: list[KapDisclosure] = []
    try:
        import cloudscraper
        scraper = cloudscraper.create_scraper()
        resp = scraper.get(
            KAP_DISCLOSURES_API,
            params={"limit": limit},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return result
        data = resp.json()
    except Exception:
        return result

    items = data.get("body") or data.get("result") or data.get("items") or []
    if isinstance(items, dict):
        items = items.get("list", items) if isinstance(items.get("list"), list) else []

    for raw in items:
        if not isinstance(raw, dict):
            continue
        did = str(raw.get("id") or raw.get("disclosureId") or raw.get("oid") or "")
        title = (raw.get("title") or raw.get("baslik") or raw.get("headline") or "").strip()
        summary = (raw.get("summary") or raw.get("ozet") or raw.get("body") or "").strip()
        if not did and not title:
            continue
        if not did:
            did = str(hash(title + summary) % (10 ** 10))
        ticker = None
        codes = raw.get("stockCodes") or raw.get("stockCode") or raw.get("sembol") or raw.get("ticker")
        if isinstance(codes, list) and codes:
            ticker = str(codes[0]).strip().upper()
        elif isinstance(codes, str) and codes.strip():
            ticker = codes.strip().upper()
        result.append(KapDisclosure(
            disclosure_id=did,
            title=title,
            summary=summary[:2000] if summary else "",
            ticker=ticker,
            link=raw.get("link") or raw.get("url"),
        ))
    return result


def fetch_latest_disclosures(
    limit: int = 30,
    timeout: int = 15,
) -> list[KapDisclosure]:
    """
    Haber/bildirim listesini çeker. Öncelik: 1) Investing.com (cloudscraper),
    2) RSS, 3) KAP API (cloudscraper), 4) HTML fallback. Investing'de '(KAP)' veya
    4-5 harfli ticker içeren başlıklar filtrelenir; Gemini analyze_news ile Kritik + Puan >= 7
    ise run_kap_watchdog_loop içinde on_critical_news tetiklenir.
    """
    # 1) Investing.com borsa haberleri (KAP/BIST içerikli)
    result = _fetch_via_investing(limit=limit, timeout=timeout)
    if result:
        return result

    # 2) RSS
    result = _fetch_via_rss(limit=limit, timeout=timeout)
    if result:
        return result

    # 3) Cloudscraper ile KAP API
    result = _fetch_via_cloudscraper_api(limit=limit, timeout=timeout)
    if result:
        return result

    # 4) HTML fallback (kap_listener / KAP ana sayfa)
    return _fetch_disclosures_fallback(limit)


def get_recent_disclosures_for_symbol(
    symbol: str,
    limit: int = 3,
    disclosures: Optional[list[KapDisclosure]] = None,
) -> list[KapDisclosure]:
    """
    Belirli bir hisse kodu için son KAP bildirimlerini döner.
    Telegram /bak komutu gibi sorgularda kullanılır.
    Önce API (veya fallback) listesinden ticker/başlık ile eşleşenleri arar;
    hiç bulamazsa kap_listener.get_headlines_for_symbol ile HTML'den sembol geçen başlıkları çeker.
    """
    symbol_upper = symbol.upper().strip()
    if disclosures is None:
        disclosures = fetch_latest_disclosures(limit=100)
    result = []
    for d in disclosures:
        ticker = d.ticker
        if ticker and ticker.upper() == symbol_upper:
            result.append(d)
        elif not ticker and (symbol_upper in (d.title + " " + d.summary).upper()):
            result.append(d)
        if len(result) >= limit:
            break
    # API'de/fallback'te sembole ait kayıt yoksa: kap_listener ile başlıkta sembol geçenleri al
    if len(result) < limit:
        try:
            from src.scrapers.kap_listener import get_headlines_for_symbol
            for h in get_headlines_for_symbol(symbol_upper):
                if len(result) >= limit:
                    break
                result.append(KapDisclosure(
                    disclosure_id=h.link or str(hash(h.title)),
                    title=h.title,
                    summary="",
                    ticker=symbol_upper,
                    link=h.link,
                ))
        except Exception:
            pass
    return result[:limit]


def _fetch_disclosures_fallback(max_items: int = 25) -> list[KapDisclosure]:
    """
    RSS ve cloudscraper API sonuç vermezse: önce cloudscraper ile ana sayfa HTML çekilir,
    bildirim benzeri linkler parse edilir; yoksa kap_listener.get_latest_kap_headlines kullanılır.
    """
    result: list[KapDisclosure] = []
    # Cloudscraper ile ana sayfa (666/Cloudflare aşımı)
    try:
        import cloudscraper
        scraper = cloudscraper.create_scraper()
        r = scraper.get("https://www.kap.org.tr/tr", timeout=15)
        if r.ok and r.text:
            # kap_listener ile aynı mantık: bildirim/özet/detay linkleri ve başlıklar
            link_pattern = re.compile(
                r'<a[^>]+href="(/tr/[^"]+)"[^>]*>([^<]{10,200})</a>',
                re.IGNORECASE | re.DOTALL,
            )
            seen = set()
            for m in link_pattern.finditer(r.text):
                path, title = m.group(1), re.sub(r"\s+", " ", m.group(2).strip())
                if "bildirim" in path.lower() or "ozet" in path.lower() or "detay" in path.lower():
                    link = path if path.startswith("http") else ("https://www.kap.org.tr".rstrip("/") + path)
                    if link not in seen and len(title) > 10:
                        seen.add(link)
                        result.append(KapDisclosure(
                            disclosure_id=link or str(hash(title)),
                            title=title,
                            summary="",
                            ticker=None,
                            link=link,
                        ))
                        if len(result) >= max_items:
                            return result
    except Exception:
        pass
    if result:
        return result
    # Son çare: kap_listener (requests ile ana sayfa)
    try:
        from src.scrapers.kap_listener import get_latest_kap_headlines
        for h in get_latest_kap_headlines(max_items=max_items):
            result.append(KapDisclosure(
                disclosure_id=h.link or str(hash(h.title)),
                title=h.title,
                summary="",
                ticker=None,
                link=h.link,
            ))
    except Exception:
        pass
    return result


def run_kap_watchdog_loop(
    on_critical_news: Callable[..., None],  # (ticker, news_result, title, summary, published_at=None)
    symbol_list_path: Optional[Path] = None,
    poll_interval_sec: float = KAP_POLL_INTERVAL_SEC,
    min_news_score: int = 7,
    on_urgent_negative_alert: Optional[Callable[[str, object, str, str], None]] = None,
) -> None:
    """
    Sonsuz döngü: Her poll_interval_sec saniyede KAP'tan bildirim çeker.
    - Yeni bildirimde hisse kodu BIST100 içindeyse Gemini haber süzgecinden geçirir.
    - Aktif pozisyondaki hisse için Negatif + puan >= 7 ise on_urgent_negative_alert çağrılır (zarar kes uyarısı).
    - puan >= min_news_score ise on_critical_news(ticker, news_result, title, summary, published_at=d.published) çağrılır.
    """
    from src.analysis.news_analyzer import analyze_news

    seen_ids: set[str] = set()
    bist100 = _load_bist100_set(symbol_list_path)
    if not bist100:
        root = Path(__file__).resolve().parent.parent.parent
        bist100 = _load_bist100_set(root / "data" / "BIST100.txt")

    while True:
        try:
            disclosures = fetch_latest_disclosures(limit=25)
            active_symbols: set[str] = set()
            if on_urgent_negative_alert:
                try:
                    from src.position_manager import get_active_symbols
                    active_symbols = get_active_symbols()
                except Exception:
                    pass

            for d in disclosures:
                if d.disclosure_id in seen_ids:
                    continue
                seen_ids.add(d.disclosure_id)
                ticker = d.ticker or _extract_ticker_from_text(d.title + " " + d.summary, bist100)
                if not ticker or ticker not in bist100:
                    continue
                # Kotayı korumak: Sadece potansiyel kritik anahtar kelime içeren haberleri Gemini'ye gönder
                if not _title_looks_potentially_critical(d.title, d.summary):
                    continue
                analysis = analyze_news(d.title, d.summary)
                if analysis is None:
                    continue

                # Aktif pozisyonlar için negatif haber uyarısı (Kritik + puan 7+ ve duyarlılık Negatif)
                if on_urgent_negative_alert and ticker in active_symbols:
                    sentiment = (getattr(analysis, "sentiment", "") or "").strip().lower()
                    is_critical = getattr(analysis, "critical", True)
                    if is_critical and sentiment == "negatif" and analysis.score >= min_news_score:
                        on_urgent_negative_alert(ticker, analysis, d.title, d.summary)

                # Fırsat taraması: Kritik ve Puan >= 7 ise on_critical_news tetiklenir (published = haber bayatlaması için)
                is_critical = getattr(analysis, "critical", True)
                if is_critical and analysis.score >= min_news_score:
                    on_critical_news(ticker, analysis, d.title, d.summary, d.published)

                # Free Tier RPM: Her Gemini çağrısı sonrası kısa bekleme
                time.sleep(GEMINI_DELAY_BETWEEN_CALLS_SEC)
        except Exception:
            pass
        time.sleep(poll_interval_sec)
