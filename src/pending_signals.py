"""
Bekleyen sinyaller: High breakout yapılmadan 3 bar geçen sinyaller iptal edilir,
Telegram'a "Sinyal İptal: Momentum onayı gelmedi." mesajı gönderilir.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PENDING_PATH = ROOT / "data" / "pending_signals.json"
ENTRY_HIGH_CONFIRM_BARS = 3


def _ensure_data_dir() -> None:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)


def load_pending() -> list[dict[str, Any]]:
    """Bekleyen sinyal listesini dosyadan okur."""
    _ensure_data_dir()
    if not PENDING_PATH.is_file():
        return []
    try:
        with open(PENDING_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_pending(items: list[dict[str, Any]]) -> None:
    """Bekleyen sinyal listesini dosyaya yazar."""
    _ensure_data_dir()
    with open(PENDING_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=0)


def add_pending_signal(
    symbol: str,
    exchange: str,
    signal_bar_high: float,
    signal_bar_time: pd.Timestamp,
) -> None:
    """Yeni sinyal gönderildiğinde bekleyen listesine ekler."""
    items = load_pending()
    items.append({
        "symbol": symbol,
        "exchange": exchange,
        "signal_bar_high": float(signal_bar_high),
        "signal_bar_time": pd.Timestamp(signal_bar_time).isoformat(),
    })
    save_pending(items)


def check_pending_signal_cancellations(
    send_telegram: bool = True,
    delay_seconds: float = 0.5,
) -> int:
    """
    Bekleyen sinyalleri kontrol eder: 3 bar geçtiyse ve high kırılımı yoksa iptal mesajı gönderir.
    Returns: İptal edilen sinyal sayısı.
    """
    import time
    from src.scrapers.tv_scraper import get_ohlc_bist
    from src.notifications.telegram_bot import send_telegram_message

    pending = load_pending()
    if not pending:
        return 0

    remaining = []
    cancelled_count = 0

    for item in pending:
        symbol = item.get("symbol")
        exchange = item.get("exchange", "BIST")
        signal_bar_high = item.get("signal_bar_high")
        signal_bar_time_str = item.get("signal_bar_time")
        if not symbol or signal_bar_high is None or not signal_bar_time_str:
            continue

        try:
            data = get_ohlc_bist(symbol, exchange, n_bars=100)
            df_4h = data.get("4h")
            if df_4h is None or df_4h.empty:
                remaining.append(item)
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                continue

            df_4h = df_4h.copy()
            if hasattr(df_4h.index, "tz_localize"):
                pass
            try:
                signal_ts = pd.Timestamp(signal_bar_time_str)
            except Exception:
                remaining.append(item)
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                continue

            # Sinyal barının konumunu bul (o tarihte kapanan bar)
            try:
                idx = int(df_4h.index.get_indexer([signal_ts], method="ffill")[0])
            except Exception:
                mask = (df_4h.index <= signal_ts)
                if not mask.any():
                    remaining.append(item)
                    if delay_seconds > 0:
                        time.sleep(delay_seconds)
                    continue
                # Son bar where index <= signal_ts (son True)
                idx = int(len(mask) - 1 - (mask[::-1].argmax())) if mask.any() else -1

            if idx < 0:
                remaining.append(item)
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                continue

            # Sonraki 3 bar var mı?
            need_bars = idx + 1 + ENTRY_HIGH_CONFIRM_BARS
            if len(df_4h) < need_bars:
                remaining.append(item)
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                continue

            # Sonraki 3 bar içinde high >= signal_bar_high oldu mu?
            high_broken = False
            for j in range(1, ENTRY_HIGH_CONFIRM_BARS + 1):
                k = idx + j
                if k >= len(df_4h):
                    break
                row = df_4h.iloc[k]
                high = row.get("high") if "high" in row else row.get("High")
                if high is not None and not pd.isna(high) and float(high) >= float(signal_bar_high):
                    high_broken = True
                    break

            if high_broken:
                remaining.append(item)
            else:
                cancelled_count += 1
                if send_telegram:
                    msg = f"❌ Sinyal İptal: #{symbol.upper()} — Momentum onayı gelmedi."
                    send_telegram_message(msg)

            if delay_seconds > 0:
                time.sleep(delay_seconds)
        except Exception:
            remaining.append(item)
            if delay_seconds > 0:
                time.sleep(delay_seconds)

    save_pending(remaining)
    return cancelled_count
