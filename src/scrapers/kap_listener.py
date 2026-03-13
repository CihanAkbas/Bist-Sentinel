"""
KAP (Kamuyu Açıklama Platformu) listener - en son düşen başlıkları kontrol eder.
Anahtar kelime (keyword) ile sentiment filtresi: GPT entegrasyonuna kadar geçici çözüm.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Literal

import requests

# KAP ana sayfa / son bildirimler (yapı değişebilir; gerekirse güncellenir)
KAP_BASE = "https://www.kap.org.tr"
KAP_ANASAYFA = "https://www.kap.org.tr/tr"
# RSS varsa ileride buraya eklenebilir: KAP_RSS = "..."

USER_AGENT = "BIST100Bot/1.0 (Education; kap.org.tr)"

# --- Sentiment köprüsü (GPT entegrasyonuna kadar anahtar kelime filtresi) ---
POSITIVE_KEYWORDS = [
    "sözleşme", "sozlesme",
    "yeni iş ilişkisi", "yeni is iliskisi", "iş birliği", "is birligi",
    "pay geri alımı", "pay geri alimi", "geri alım", "geri alim",
    "kâr payı", "kar payi", "temettü", "temettu", "dividend",
    "ihale", "sözleşme imzalandı", "sozlesme imzalandi",
    "artış", "artis", "yükseliş", "yukselis", "büyüme", "buyume",
]
NEGATIVE_KEYWORDS = [
    "dava", "tazminat", "tazminat",
    "sermaye azaltımı", "sermaye azaltimi", "sermaye azalimi",
    "faaliyet durdurma", "faaliyet durdurma", "durduruldu",
    "zarar", "kayıp", "kayip", "ceza", "iptal", "fesih",
    "düşüş", "dusus", "düşüş", "küçülme", "kuculme",
]

SentimentLabel = Literal["pozitif", "negatif", "nötr"]


@dataclass
class KapHeadline:
    """KAP başlık kaydı."""
    title: str
    link: str
    published: Optional[datetime] = None
    raw_html_snippet: Optional[str] = None
    sentiment: Optional[SentimentLabel] = field(default=None, repr=False)


def get_latest_kap_headlines(
    url: Optional[str] = None,
    max_items: int = 50,
    timeout: int = 15,
) -> list[KapHeadline]:
    """
    KAP sitesinden veya (ileride) RSS servisinden en son düşen başlıkları getirir.

    Şu an KAP ana sayfa veya bildirim listesi HTML'inden link/başlık çıkarır.
    KAP resmi API için Borsa İstanbul ile sözleşme gerekir; bu modül ücretsiz
    kamu sayfalarıyla sınırlı bir 'listener' zeminidir.

    Returns:
        Son başlıklar listesi (en yeniden eskiye).
    """
    target = url or KAP_ANASAYFA
    headlines: list[KapHeadline] = []

    try:
        resp = requests.get(
            target,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
        resp.raise_for_status()
        html = resp.text
    except requests.RequestException:
        return headlines

    # Basit regex ile bildirim linkleri ve başlıklar (KAP sayfa yapısına göre uyarlanabilir)
    # Örnek pattern: /tr/ozet/... veya /tr/bildirim-detay/...
    link_pattern = re.compile(
        r'<a[^>]+href="(/tr/[^"]+)"[^>]*>([^<]{10,200})</a>',
        re.IGNORECASE | re.DOTALL,
    )
    for m in link_pattern.finditer(html):
        path, title = m.group(1), m.group(2).strip()
        title = re.sub(r"\s+", " ", title)
        if "bildirim" in path.lower() or "ozet" in path.lower() or "detay" in path.lower():
            link = path if path.startswith("http") else (KAP_BASE.rstrip("/") + path)
            headlines.append(KapHeadline(title=title, link=link))

    # Tekrarları kaldır (link'e göre)
    seen = set()
    unique = []
    for h in headlines:
        if h.link not in seen:
            seen.add(h.link)
            unique.append(h)
    headlines = unique[:max_items]

    return headlines


def _classify_sentiment(title: str) -> SentimentLabel:
    """
    Başlığı anahtar kelimelere göre pozitif / negatif / nötr sınıflandırır.
    GPT entegrasyonuna kadar geçici çözüm.
    """
    t = title.lower().strip()
    for kw in POSITIVE_KEYWORDS:
        if kw in t:
            for neg in NEGATIVE_KEYWORDS:
                if neg in t:
                    return "nötr"  # İkisi de varsa nötr
            return "pozitif"
    for kw in NEGATIVE_KEYWORDS:
        if kw in t:
            return "negatif"
    return "nötr"


def classify_headlines_sentiment(headlines: list[KapHeadline]) -> list[KapHeadline]:
    """Başlık listesindeki her öğeye sentiment atar (yerinde günceller)."""
    for h in headlines:
        h.sentiment = _classify_sentiment(h.title)
    return headlines


def get_headlines_for_symbol(
    symbol: str,
    headlines: Optional[list[KapHeadline]] = None,
) -> list[KapHeadline]:
    """
    Belirli bir hisse sembolüyle ilgili başlıkları filtreler.
    Başlık metninde sembol veya şirket adı geçenler döner.
    (İleride KAP API ile şirket kodu eşlemesi yapılabilir.)
    """
    if headlines is None:
        headlines = get_latest_kap_headlines()
    symbol_upper = symbol.upper()
    return [h for h in headlines if symbol_upper in h.title.upper()]


def get_sentiment_for_symbol(
    symbol: str,
    headlines: Optional[list[KapHeadline]] = None,
) -> SentimentLabel:
    """
    Bir hisse için KAP başlıklarına göre özet sentiment döner.
    Pozitif başlık varsa 'pozitif', sadece negatif varsa 'negatif', yoksa veya karışıksa 'nötr'.
    """
    related = get_headlines_for_symbol(symbol, headlines)
    if not related:
        return "nötr"
    classify_headlines_sentiment(related)
    has_pos = any(h.sentiment == "pozitif" for h in related)
    has_neg = any(h.sentiment == "negatif" for h in related)
    if has_pos and not has_neg:
        return "pozitif"
    if has_neg and not has_pos:
        return "negatif"
    return "nötr"


if __name__ == "__main__":
    items = get_latest_kap_headlines(max_items=10)
    print(f"Son {len(items)} KAP başlığı:")
    for h in items[:5]:
        print(f"  - {h.title[:80]}...")
        print(f"    {h.link}")
