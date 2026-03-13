"""
BIST (Borsa İstanbul) açılış saatleri: Bot sadece borsa açıkken tarama yapar.
Hafta sonu (Cumartesi/Pazar) ve mesai dışı saatlerde döngü duraklar.
"""
from __future__ import annotations

from datetime import time

# BIST hisse piyasası: Pazartesi–Cuma, 09:30–18:00 İstanbul saati (sürekli seans)
BIST_TIMEZONE = "Europe/Istanbul"
BIST_OPEN = time(9, 30)   # 09:30
BIST_CLOSE = time(18, 0)  # 18:00
BIST_WEEKEND = (5, 6)     # 5=Saturday, 6=Sunday


def _get_istanbul_tz():
    """Europe/Istanbul için zone; Python 3.9+ zoneinfo, yoksa UTC+3 kabul."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(BIST_TIMEZONE)
    except Exception:
        try:
            import pytz
            return pytz.timezone(BIST_TIMEZONE)
        except Exception:
            return None


def is_bist_open(now=None) -> bool:
    """
    Verilen an (veya şu an) BIST hisse piyasasının açık olduğu zaman diliminde mi?
    Hafta sonu ve 09:30–18:00 dışında False döner.
    """
    tz = _get_istanbul_tz()
    if tz is None:
        return True  # Saat dilimi alınamazsa kısıtlamayı devre dışı bırak
    from datetime import datetime
    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)
    if now.weekday() in BIST_WEEKEND:
        return False
    t = now.time()
    return BIST_OPEN <= t <= BIST_CLOSE


def next_open_in_seconds(now=None) -> float:
    """
    BIST bir sonra ne zaman açılacak? (saniye cinsinden bekleme süresi.)
    Şu an açıksa 0 döner.
    """
    tz = _get_istanbul_tz()
    if tz is None:
        return 0.0
    from datetime import datetime, timedelta
    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)
    if is_bist_open(now):
        return 0.0
    # Yarın 09:30 veya bugün 09:30 (henüz açılmadıysa)
    today_open = now.replace(hour=BIST_OPEN.hour, minute=BIST_OPEN.minute, second=0, microsecond=0)
    if now.weekday() in BIST_WEEKEND:
        # Pazartesi 09:30'a kadar ilerle
        days = 7 - now.weekday()  # Sat=1, Sun=2
        next_open = today_open + timedelta(days=days)
    elif now.time() < BIST_OPEN:
        next_open = today_open
    else:
        # Bugün kapandı; yarın 09:30 (Cuma 18:00 sonrası = Pazartesi 09:30)
        next_open = today_open + timedelta(days=1)
        while next_open.weekday() in BIST_WEEKEND:
            next_open += timedelta(days=1)
    delta = next_open - now
    return max(0.0, delta.total_seconds())
