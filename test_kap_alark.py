"""KAP/Investing testi: Investing -> RSS -> API -> fallback + get_recent_disclosures_for_symbol."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.notifications.kap_watchdog import (
    fetch_latest_disclosures,
    get_recent_disclosures_for_symbol,
    _fetch_via_investing,
    _fetch_via_rss,
    _fetch_via_cloudscraper_api,
)

def main():
    print("=== KAP/Investing testi (oncelik: Investing -> RSS -> API -> fallback) ===\n")

    print("1) Investing.com (_fetch_via_investing)...")
    try:
        inv = _fetch_via_investing(limit=20, timeout=20)
        print(f"   Kayit: {len(inv)} (KAP veya 4-5 harf ticker iceren basliklar)")
        for i, d in enumerate(inv[:5], 1):
            print(f"   [{i}] {d.title[:72]}")
    except Exception as e:
        print(f"   Hata: {e}")

    print("\n2) fetch_latest_disclosures(30) (tum zincir)...")
    try:
        all_d = fetch_latest_disclosures(limit=30)
        print(f"   Toplam: {len(all_d)}")
        for i, d in enumerate(all_d[:5], 1):
            print(f"   [{i}] {d.title[:68]}")
    except Exception as e:
        print(f"   Hata: {e}")

    print("\n3) get_recent_disclosures_for_symbol('ALARK', 5)...")
    try:
        alark = get_recent_disclosures_for_symbol("ALARK", limit=5)
        print(f"   ALARK: {len(alark)} bildirim.")
        for i, d in enumerate(alark, 1):
            print(f"   [{i}] {d.title[:80]}")
    except Exception as e:
        print(f"   Hata: {e}")

if __name__ == "__main__":
    main()
