"""
Haftalık Performans Karnesi: active_trades, trade_history ve tarama loglarını analiz eder.
Her Pazartesi 09:00'da (veya bot ilk açıldığında) Telegram'a haftalık rapor gönderir.

İçerik: Toplam sinyal / Onaylanan işlem, Win Rate %, Toplam P/L %, En çok kazandıran/kaybettiren 3 sembol.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.config import settings

LOGS_DIR = settings.project_root / "data" / "logs"
LAST_REPORT_SENT_PATH = settings.project_root / "data" / "last_weekly_report.txt"


@dataclass
class WeeklyStats:
    """Haftalık özet istatistikler."""
    total_signals: int
    approved_trades: int
    closed_trades: int
    wins: int
    win_rate_pct: float
    total_pnl_pct: float
    top3_winners: list[tuple[str, float]]  # (symbol, pnl_pct)
    top3_losers: list[tuple[str, float]]


def _parse_ts(s: str) -> datetime | None:
    """ISO veya benzeri timestamp'i datetime'a çevirir."""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _week_range(report_time: datetime | None = None) -> tuple[datetime, datetime]:
    """
    Raporun kapsadığı hafta: bir önceki Pazartesi 00:00 - Pazar 23:59.
    report_time verilmezse şu an kullanılır (Pazartesi 09:00'da çağrılırsa geçen hafta).
    """
    now = report_time or datetime.now()
    # Bu haftanın Pazartesi 00:00
    weekday = now.weekday()  # 0 = Monday
    this_monday = (now - timedelta(days=weekday)).replace(hour=0, minute=0, second=0, microsecond=0)
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(seconds=1)
    return (last_monday, last_sunday)


def _get_scan_signals_in_range(start: datetime, end: datetime) -> int:
    """data/logs içindeki scan_*.json dosyalarından [start, end] aralığındaki toplam sinyal sayısını döner."""
    total = 0
    if not LOGS_DIR.is_dir():
        return 0
    for path in LOGS_DIR.glob("scan_*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                import json
                data = json.load(f)
            ts_str = data.get("timestamp") or data.get("timestamp_str")
            if not ts_str:
                continue
            ts = _parse_ts(ts_str)
            if ts is None:
                continue
            if start <= ts <= end:
                total += int(data.get("signals", 0))
        except Exception:
            continue
    return total


def _get_closed_trades_in_range(
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    """trade_history içinde closed_at [start, end] aralığında olan kayıtları döner."""
    from src.position_manager import load_trade_history

    history = load_trade_history()
    out = []
    for r in history:
        closed_at_str = r.get("closed_at")
        if not closed_at_str:
            continue
        ts = _parse_ts(closed_at_str)
        if ts is None:
            continue
        if start <= ts <= end:
            out.append(r)
    return out


def _get_approved_count_in_range(start: datetime, end: datetime) -> int:
    """
    [start, end] haftasında onaylanan işlem sayısı (İşlemi Onayla ile takibe alınan).
    trade_history ve active_trades içinde added_at bu aralıkta olan kayıtlar.
    """
    from src.position_manager import load_active_trades, load_trade_history

    count = 0
    for r in load_trade_history():
        added_at_str = r.get("added_at")
        if not added_at_str:
            continue
        ts = _parse_ts(added_at_str)
        if ts and start <= ts <= end:
            count += 1
    for t in load_active_trades():
        added_at_str = t.get("added_at")
        if not added_at_str:
            continue
        ts = _parse_ts(added_at_str)
        if ts and start <= ts <= end:
            count += 1
    return count


def compute_weekly_stats(
    start: datetime | None = None,
    end: datetime | None = None,
) -> WeeklyStats:
    """
    Verilen hafta aralığı için istatistikleri hesaplar.
    start/end verilmezse bir önceki takvim haftası kullanılır.
    """
    if start is None or end is None:
        start, end = _week_range()

    total_signals = _get_scan_signals_in_range(start, end)
    approved_trades = _get_approved_count_in_range(start, end)
    closed = _get_closed_trades_in_range(start, end)
    closed_count = len(closed)
    wins = sum(1 for r in closed if (r.get("exit_reason") or "").lower() == "tp1")
    win_rate_pct = (100.0 * wins / closed_count) if closed_count else 0.0
    total_pnl_pct = sum(float(r.get("profit_pct", 0)) for r in closed)

    # Sembol bazında P/L topla
    by_symbol: dict[str, float] = {}
    for r in closed:
        sym = (r.get("symbol") or "").upper()
        if not sym:
            continue
        pnl = float(r.get("profit_pct", 0))
        by_symbol[sym] = by_symbol.get(sym, 0) + pnl

    sorted_symbols = sorted(by_symbol.items(), key=lambda x: x[1], reverse=True)
    top3_winners = sorted_symbols[:3]
    top3_losers = sorted_symbols[-3:] if len(sorted_symbols) >= 3 else sorted_symbols
    top3_losers.reverse()  # En çok kaybedenden başla

    return WeeklyStats(
        total_signals=total_signals,
        approved_trades=approved_trades,
        closed_trades=closed_count,
        wins=wins,
        win_rate_pct=round(win_rate_pct, 1),
        total_pnl_pct=round(total_pnl_pct, 2),
        top3_winners=top3_winners,
        top3_losers=top3_losers,
    )


def format_weekly_report(stats: WeeklyStats, start: datetime, end: datetime) -> str:
    """Rapor metnini Telegram mesajı için formatlar."""
    start_str = start.strftime("%d.%m.%Y")
    end_str = end.strftime("%d.%m.%Y")
    lines = [
        "📊 <b>HAFTALIK PERFORMANS KARNESİ</b>",
        f"📅 {start_str} — {end_str}",
        "",
        f"📤 Toplam üretilen sinyal: <b>{stats.total_signals}</b>",
        f"✅ Onaylanan işlem sayısı: <b>{stats.approved_trades}</b>",
        "",
        f"🏆 Başarı oranı (Win Rate): <b>%{stats.win_rate_pct}</b> ({stats.wins}/{stats.closed_trades} kazanç)",
        f"💰 Toplam P/L: <b>%{stats.total_pnl_pct:+.2f}</b>",
        "",
        "📈 <b>En çok kazandıran 3 sembol:</b>",
    ]
    if stats.top3_winners:
        for sym, pnl in stats.top3_winners:
            lines.append(f"   • #{sym} %{pnl:+.2f}")
    else:
        lines.append("   • —")

    lines.append("")
    lines.append("📉 <b>En çok kaybettiren 3 sembol:</b>")
    if stats.top3_losers:
        for sym, pnl in stats.top3_losers:
            lines.append(f"   • #{sym} %{pnl:+.2f}")
    else:
        lines.append("   • —")

    return "\n".join(lines)


def send_weekly_report(send_telegram: bool = True) -> bool:
    """
    Son tamamlanan hafta için performans raporunu hesaplar ve Telegram'a gönderir.
    """
    start, end = _week_range()
    stats = compute_weekly_stats(start, end)
    text = format_weekly_report(stats, start, end)
    if send_telegram:
        from src.notifications.telegram_bot import send_telegram_message
        return send_telegram_message(text, parse_mode="HTML")
    return True


def should_send_weekly_report_now() -> bool:
    """
    Şu an Pazartesi ve saat 09:00 veya sonrası mı; bu hafta için rapor henüz gönderilmedi mi?
    (Döngü 4 saatte bir çalışsa bile 10:00'da yakalayıp rapor gönderir.)
    """
    now = datetime.now()
    if now.weekday() != 0:  # Pazartesi = 0
        return False
    if now.hour < 9:  # 09:00 ve sonrası
        return False
    key = now.strftime("%Y-%m-%d")  # 2026-02-24
    if not LAST_REPORT_SENT_PATH.is_file():
        return True
    try:
        last = LAST_REPORT_SENT_PATH.read_text(encoding="utf-8").strip()
        return last != key
    except Exception:
        return True


def mark_weekly_report_sent() -> None:
    """Bu hafta için raporun gönderildiğini kaydeder (tekrar gönderilmesin)."""
    LAST_REPORT_SENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_REPORT_SENT_PATH.write_text(datetime.now().strftime("%Y-%m-%d"), encoding="utf-8")
