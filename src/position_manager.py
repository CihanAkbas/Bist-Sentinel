"""
Position Manager: Telegram'a giden sinyalleri active_trades.json'da takip eder.
Her 4H bar kapandığında (tarama turunda) açık işlemleri kontrol eder:
- 15 bar geçti ve kâr %1.5 altında → Hantallık mesajı, listeden çıkar
- TP1 veya Stop tetiklendi → Acil bildirim, listeden çıkar
Kapanan işlemler haftalık performans raporu için trade_history.json'a eklenir.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ACTIVE_TRADES_PATH = DATA_DIR / "active_trades.json"
PENDING_APPROVALS_PATH = DATA_DIR / "pending_trade_approvals.json"
TRADE_HISTORY_PATH = DATA_DIR / "trade_history.json"
HANTALLIK_BARS = 15
HANTALLIK_MIN_PROFIT_PCT = 1.5


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_active_trades() -> list[dict[str, Any]]:
    """active_trades.json dosyasından açık işlem listesini okur."""
    _ensure_data_dir()
    if not ACTIVE_TRADES_PATH.is_file():
        return []
    try:
        with open(ACTIVE_TRADES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def get_active_symbols() -> set[str]:
    """Aktif pozisyondaki hisse sembollerini döner (KAP negatif haber uyarısı için)."""
    trades = load_active_trades()
    return {str(t.get("symbol", "")).strip().upper() for t in trades if t.get("symbol")}


def save_active_trades(trades: list[dict[str, Any]]) -> None:
    """Açık işlem listesini active_trades.json'a yazar."""
    _ensure_data_dir()
    with open(ACTIVE_TRADES_PATH, "w", encoding="utf-8") as f:
        json.dump(trades, f, ensure_ascii=False, indent=2)


def load_trade_history() -> list[dict[str, Any]]:
    """Kapanan işlemlerin geçmişini okur (haftalık rapor için)."""
    _ensure_data_dir()
    if not TRADE_HISTORY_PATH.is_file():
        return []
    try:
        with open(TRADE_HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def append_closed_trade(record: dict[str, Any]) -> None:
    """Kapanan bir işlemi trade_history.json'a ekler."""
    _ensure_data_dir()
    history = load_trade_history()
    history.append(record)
    with open(TRADE_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def add_trade(
    symbol: str,
    exchange: str,
    entry_price: float,
    stop_loss: float,
    tp1: float,
    tp2: float,
    entry_bar_time: str,
) -> None:
    """
    Yeni bir işlemi takibe ekler (sinyal Telegram'a gittiğinde veya onay sonrası).
    entry_bar_time: 4H barın kapanış zamanı (isoformat).
    added_at: Haftalık raporda "onaylanan işlem" sayısı için kullanılır.
    tp1_hit: TP1'e ulaşınca True yapılır; stop breakeven'e çekilir, TP2'ye kadar takip edilir.
    """
    trades = load_active_trades()
    trades = [t for t in trades if t.get("symbol") != symbol]
    trades.append({
        "symbol": symbol,
        "exchange": exchange,
        "entry_price": float(entry_price),
        "stop_loss": float(stop_loss),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "entry_bar_time": entry_bar_time,
        "added_at": datetime.now().isoformat(),
        "tp1_hit": False,
    })
    save_active_trades(trades)


def add_trade_from_signal(signal: Any, entry_bar_time: Any, exchange: str) -> None:
    """
    Sinyal (Signal dataclass) ve giriş barı zamanı ile takibe ekler.
    entry, stop_loss, tp1, tp2 yoksa ekleme yapılmaz.
    """
    entry = getattr(signal, "entry", None)
    stop_loss = getattr(signal, "stop_loss", None)
    tp1 = getattr(signal, "tp1", None)
    tp2 = getattr(signal, "tp2", None)
    if entry is None or stop_loss is None or tp1 is None or tp2 is None:
        return
    ts = entry_bar_time.isoformat() if hasattr(entry_bar_time, "isoformat") else str(entry_bar_time)
    add_trade(
        symbol=signal.symbol,
        exchange=exchange,
        entry_price=float(entry),
        stop_loss=float(stop_loss),
        tp1=float(tp1),
        tp2=float(tp2),
        entry_bar_time=ts,
    )


def _load_pending_approvals() -> dict[str, dict[str, Any]]:
    """Onay bekleyen sinyaller: key = SYMBOL:ENTRY (örn. THYAO:45.50)."""
    _ensure_data_dir()
    if not PENDING_APPROVALS_PATH.is_file():
        return {}
    try:
        with open(PENDING_APPROVALS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_pending_approvals(approvals: dict[str, dict[str, Any]]) -> None:
    _ensure_data_dir()
    with open(PENDING_APPROVALS_PATH, "w", encoding="utf-8") as f:
        json.dump(approvals, f, ensure_ascii=False, indent=2)


def add_pending_trade_approval(signal: Any, entry_bar_time: Any, exchange: str) -> None:
    """
    Sinyal Telegram'a gittiğinde onay bekleyen listesine ekler.
    Kullanıcı 'İşlemi Onayla' butonuna basınca add_trade ile active_trades'e alınır.
    """
    entry = getattr(signal, "entry", None)
    stop_loss = getattr(signal, "stop_loss", None)
    tp1 = getattr(signal, "tp1", None)
    tp2 = getattr(signal, "tp2", None)
    if entry is None or stop_loss is None or tp1 is None or tp2 is None:
        return
    ts = entry_bar_time.isoformat() if hasattr(entry_bar_time, "isoformat") else str(entry_bar_time)
    key = f"{signal.symbol.upper()}:{float(entry):.2f}"
    approvals = _load_pending_approvals()
    approvals[key] = {
        "symbol": signal.symbol,
        "exchange": exchange,
        "entry_price": float(entry),
        "stop_loss": float(stop_loss),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "entry_bar_time": ts,
    }
    _save_pending_approvals(approvals)


def get_and_remove_pending_approval(symbol: str, entry_price: float) -> Optional[dict[str, Any]]:
    """
    Onay bekleyen listeden sembol+giriş fiyatına göre kaydı alır ve siler.
    entry_price 2 ondalıklı eşleşme için kullanılır (key SYMBOL:45.50 formatında).
    """
    key = f"{symbol.upper()}:{float(entry_price):.2f}"
    approvals = _load_pending_approvals()
    data = approvals.pop(key, None)
    if data is not None:
        _save_pending_approvals(approvals)
    return data


def _get_ohlc(row, key_high: str = "high", key_low: str = "low", key_close: str = "close") -> tuple[float, float, float]:
    high = row.get(key_high) or row.get("High")
    low = row.get(key_low) or row.get("Low")
    close = row.get(key_close) or row.get("Close")
    return (float(high), float(low), float(close))


def check_active_trades(
    send_telegram: bool = True,
    delay_seconds: float = 0.5,
) -> int:
    """
    active_trades.json'daki her işlem için 4H veri çeker; 15 bar + %1.5 altı kâr ise Hantallık,
    TP1/Stop tetiklenmişse acil bildirim gönderir ve işlemi listeden çıkarır.
    Returns: Kapatılan (çıkarılan) işlem sayısı.
    """
    from src.scrapers.tv_scraper import get_ohlc_bist
    from src.notifications.telegram_bot import send_telegram_message

    trades = load_active_trades()
    if not trades:
        return 0

    remaining = []
    closed_count = 0

    for t in trades:
        symbol = t.get("symbol")
        exchange = t.get("exchange", "BIST")
        entry_price = t.get("entry_price")
        stop_loss = t.get("stop_loss")
        tp1 = t.get("tp1")
        tp2 = t.get("tp2")
        entry_bar_time_str = t.get("entry_bar_time")
        tp1_hit = t.get("tp1_hit", False)
        if not symbol or entry_price is None or stop_loss is None or tp1 is None or not entry_bar_time_str:
            remaining.append(t)
            continue

        try:
            data = get_ohlc_bist(symbol, exchange, n_bars=100)
            df_4h = data.get("4h")
            if df_4h is None or df_4h.empty:
                remaining.append(t)
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                continue

            df_4h = df_4h.copy()
            try:
                entry_ts = pd.Timestamp(entry_bar_time_str)
            except Exception:
                remaining.append(t)
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                continue

            # Giriş barının indeksi (o tarihte kapanan 4H bar)
            try:
                pos = df_4h.index.get_indexer([entry_ts], method="ffill")[0]
                idx = int(pos) if pos >= 0 else -1
                if idx < 0:
                    where = np.where(df_4h.index <= entry_ts)[0]
                    idx = int(where[-1]) if len(where) else -1
            except Exception:
                where = np.where(df_4h.index <= entry_ts)[0]
                idx = int(where[-1]) if len(where) else -1
            if idx < 0:
                remaining.append(t)
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                continue

            # Bar sayısı (giriş barından sonra kaç bar geçti)
            bars_held = (len(df_4h) - 1) - idx
            current_close = float(df_4h.iloc[-1].get("close") or df_4h.iloc[-1].get("Close"))
            profit_pct = (current_close - entry_price) / entry_price * 100.0

            # TP1 veya Stop tetiklendi mi? (giriş barından sonraki barlarda kontrol)
            # tp1_hit ise artık sadece breakeven stop ve TP2 kontrol edilir
            hit_stop = False
            hit_tp1 = False
            hit_tp2 = False
            effective_stop = float(entry_price) if tp1_hit else float(stop_loss)
            for j in range(idx + 1, len(df_4h)):
                row = df_4h.iloc[j]
                high, low, close = _get_ohlc(row)
                if low <= effective_stop:
                    hit_stop = True
                    break
                if not tp1_hit and high >= tp1:
                    hit_tp1 = True
                    break
                if tp1_hit and tp2 is not None and high >= tp2:
                    hit_tp2 = True
                    break

            def _save_closed_and_remove(exit_reason: str) -> None:
                record = {
                    "symbol": symbol,
                    "entry_price": float(entry_price),
                    "stop_loss": float(stop_loss),
                    "tp1": float(tp1),
                    "tp2": float(tp2),
                    "entry_bar_time": entry_bar_time_str,
                    "added_at": t.get("added_at") or datetime.now().isoformat(),
                    "closed_at": datetime.now().isoformat(),
                    "exit_reason": exit_reason,
                    "exit_price": current_close,
                    "profit_pct": round(profit_pct, 2),
                }
                append_closed_trade(record)

            if hit_stop and send_telegram:
                send_telegram_message(
                    f"🚨 ACİL — #{symbol.upper()} Stop tetiklendi! "
                    f"Stop: {stop_loss} TL. Pozisyonu kapatın veya gözden geçirin."
                )
                _save_closed_and_remove("stop")
                closed_count += 1
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                continue
            if hit_tp1 and send_telegram:
                # Multi-TP: Pozisyonu kapatma; stop'u breakeven yap, TP2'ye kadar taşı
                send_telegram_message(
                    f"🎯 TP1 — #{symbol.upper()} TP1 seviyesine ulaştı! "
                    f"TP1: {tp1} TL. Stop'u maliyete (breakeven) çekin; kalan yarıyla TP2'yi bekleyin."
                )
                t["tp1_hit"] = True
                t["stop_loss"] = float(entry_price)
                remaining.append(t)
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                continue
            if hit_tp2 and send_telegram:
                send_telegram_message(
                    f"🏁 TP2 — #{symbol.upper()} TP2 seviyesine ulaştı! "
                    f"TP2: {tp2} TL. Tebrikler, pozisyon kapatıldı."
                )
                _save_closed_and_remove("tp2")
                closed_count += 1
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                continue

            # Hantallık: 15 bar geçti, kâr %1.5 altında
            if bars_held >= HANTALLIK_BARS and profit_pct < HANTALLIK_MIN_PROFIT_PCT:
                if send_telegram:
                    send_telegram_message(
                        f"⚠️ Hantallık — #{symbol.upper()} {bars_held} bar geçti, kâr %{profit_pct:.2f} "
                        f"(hedef %{HANTALLIK_MIN_PROFIT_PCT}). Pozisyonu değerlendirin."
                    )
                _save_closed_and_remove("hantallik")
                closed_count += 1
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                continue

            remaining.append(t)
            if delay_seconds > 0:
                time.sleep(delay_seconds)
        except Exception:
            remaining.append(t)
            if delay_seconds > 0:
                time.sleep(delay_seconds)

    save_active_trades(remaining)
    return closed_count
