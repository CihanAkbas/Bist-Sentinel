"""
KAP/Investing haberleri + Gemini yorumlama testi.
Tüm çekilen haberler içinden BIST100'e eşleşenleri bulur, her biri için
Gemini analyze_news çağırır ve sonucu (Kritik, Puan, Duyarlılık) yazar.

Gereksinim: .env içinde GEMINI_API_KEY tanımlı olmalı.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.config import settings
from src.notifications.kap_watchdog import (
    fetch_latest_disclosures,
    _load_bist100_set,
    _extract_ticker_from_text,
)
from src.analysis.news_analyzer import analyze_news


def main():
    print("=== KAP haberleri + Gemini yorumlama (BIST100) ===\n")
    if not (getattr(settings, "GEMINI_API_KEY", "") or "").strip():
        print("Uyari: .env icinde GEMINI_API_KEY yok; Gemini analizi atlanacak.\n")

    # 1) Haberleri çek (Investing öncelikli)
    print("1) fetch_latest_disclosures(50)...")
    disclosures = fetch_latest_disclosures(limit=50)
    print(f"   Toplam haber: {len(disclosures)}\n")

    if not disclosures:
        print("   Haber yok, cikis.")
        return

    bist100 = _load_bist100_set()
    if not bist100:
        print("   BIST100 listesi yuklenemedi.")
        return
    print(f"   BIST100 sembol sayisi: {len(bist100)}\n")

    # 2) BIST100'e eşleşen haberleri bul
    matched = []
    for d in disclosures:
        ticker = d.ticker or _extract_ticker_from_text(d.title + " " + d.summary, bist100)
        if ticker and ticker in bist100:
            matched.append((ticker, d))

    print(f"2) BIST100 eslesen haber sayisi: {len(matched)}\n")
    if not matched:
        print("   BIST100 sembolu gecen haber yok.")
        print("   Gemini testi icin ilk haberi yine de analiz ediyoruz (ticker: TEST)...\n")
        # En az bir kez Gemini cagirip hata varsa gorelim
        first = disclosures[0]
        try:
            a = analyze_news(first.title, first.summary)
            if a:
                print(f"   [TEST] Kritik: {a.critical} | Puan: {a.score} | Duyarlilik: {a.sentiment}")
            else:
                print("   [TEST] Gemini None dondu (API key veya hata).")
        except Exception as e:
            print(f"   [TEST] Gemini exception: {e}")
        return

    # 3) Gemini ile yorumla (max 15 tane, rate limit icin arada kisa bekleme)
    max_analyze = 15
    print(f"3) Gemini analyze_news (en fazla {max_analyze} haber)...\n")
    print("-" * 70)

    for i, (ticker, d) in enumerate(matched[:max_analyze], 1):
        title_short = (d.title[:65] + "...") if len(d.title) > 65 else d.title
        print(f"\n[{i}] #{ticker} | {title_short}")
        try:
            analysis = analyze_news(d.title, d.summary)
        except Exception as e:
            print(f"    -> Gemini hata: {e}")
            continue
        if analysis is None:
            print("    -> Gemini yanit yok (API key veya hata)")
            continue
        kritik = "Evet" if analysis.critical else "Hayir"
        print(f"    Kritik: {kritik} | Puan: {analysis.score}/10 | Duyarlilik: {analysis.sentiment}")
        if analysis.comment and len(analysis.comment) > 10:
            comment_short = analysis.comment[:120].strip() + "..." if len(analysis.comment) > 120 else analysis.comment
            print(f"    Yorum: {comment_short}")
        if i < len(matched[:max_analyze]):
            time.sleep(0.8)
    print("\n" + "-" * 70)
    print("Test tamamlandi.")


if __name__ == "__main__":
    main()
