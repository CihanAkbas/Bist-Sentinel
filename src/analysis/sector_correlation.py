"""
Sektör korelasyonu: Bir hisse için sinyal geldiğinde, aynı sektördeki benzer hisselerin
teknik durumuna bakarak "Sektör Dağılımı" notu üretir. Tüm sektör birlikte hareket ediyorsa
sinyal sektörel destekli kabul edilir.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent.parent
SECTOR_PEERS_PATH = ROOT / "data" / "sector_peers.json"


def _load_sector_peers() -> dict:
    """data/sector_peers.json dosyasından sembol -> {sector, peers} eşlemesini okur."""
    if not SECTOR_PEERS_PATH.is_file():
        return {}
    try:
        with open(SECTOR_PEERS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_sector_context(
    symbol: str,
    exchange: str = "BIST",
    delay_seconds: float = 0.5,
    symbol_list_path: Optional[Path] = None,
) -> str:
    """
    Sinyal veren hissenin sektöründeki diğer hisselerin teknik durumuna bakar.
    Son günlük değişim ve EMA200 üstü/altı bilgisiyle kısa bir özet döner.

    Returns:
        "Sektör Dağılımı: Havacılık — PGSUS +1.2%, TAVHL +0.8% (günlük EMA200 üstü); sinyal sektörel destekli."
        veya sektör verisi yoksa / hata durumunda boş string.
    """
    peers_data = _load_sector_peers()
    symbol_upper = symbol.upper().strip()
    info = peers_data.get(symbol_upper)
    if not info:
        return ""

    sector_name = info.get("sector") or "Sektör"
    peer_symbols = info.get("peers") or []
    # Kendisi ve BIST100'de olmayanları atla (liste sonradan genişletilebilir)
    try:
        from src.scrapers.tv_scraper import load_bist_symbols
        bist_set = {s[0].upper() for s in load_bist_symbols(symbol_list_path)}
    except Exception:
        bist_set = set()
    peers = [p for p in peer_symbols if isinstance(p, str) and p.upper() in bist_set][:5]

    if not peers:
        return f"Sektör: {sector_name} (benzer hisse verisi yok)"

    try:
        from src.scrapers.tv_scraper import get_ohlc_bist
    except Exception:
        return ""

    parts = []
    above_ema_count = 0
    positive_change_count = 0

    for sym in peers:
        if sym.upper() == symbol_upper:
            continue
        try:
            data = get_ohlc_bist(sym, exchange, n_bars=60, interval_4h=False, interval_daily=True)
            df = data.get("1d")
            if df is None or df.empty or len(df) < 22:
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                continue
            close = df["Close"] if "Close" in df.columns else df["close"]
            ema200 = close.ewm(span=200, adjust=False).mean()
            last_close = float(close.iloc[-1])
            last_ema = float(ema200.iloc[-1])
            prev_close = float(close.iloc[-2]) if len(close) >= 2 else last_close
            pct = ((last_close - prev_close) / prev_close * 100) if prev_close else 0
            above_ema = last_close > last_ema
            if above_ema:
                above_ema_count += 1
            if pct > 0:
                positive_change_count += 1
            parts.append(f"{sym} %{pct:+.1f}" + (" (EMA200 üstü)" if above_ema else ""))
            if delay_seconds > 0:
                time.sleep(delay_seconds)
        except Exception:
            continue

    if not parts:
        return f"Sektör: {sector_name}"

    n = len(parts)
    if above_ema_count == n and positive_change_count >= (n // 2 + 1):
        support = "sinyal sektörel destekli."
    elif above_ema_count >= n // 2:
        support = "sektör karışık."
    else:
        support = "sektör zayıf; dikkatli olun."

    return f"Sektör Dağılımı: {sector_name} — " + ", ".join(parts) + f". {support.capitalize()}"
