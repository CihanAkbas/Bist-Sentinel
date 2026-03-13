"""
Fiyat aksiyonu: RSI uyumsuzluğu (divergence) ve Fair Value Gap (FVG) tespiti.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd


def _get_high_low_close(row: pd.Series) -> tuple[float, float, float]:
    h = row.get("high") or row.get("High")
    l = row.get("low") or row.get("Low")
    c = row.get("close") or row.get("Close")
    return (float(h), float(l), float(c))


def detect_bearish_rsi_divergence(
    df: pd.DataFrame,
    lookback: int = 50,
    swing_len: int = 5,
) -> bool:
    """
    Ayı uyumsuzluğu: Fiyat yeni tepe yaparken RSI daha düşük tepe yapıyorsa True.
    Son `lookback` bar içinde son iki fiyat tepesi ve RSI tepesi karşılaştırılır.
    """
    if df is None or len(df) < lookback or "rsi" not in df.columns:
        return False
    df = df.tail(lookback).copy()
    high = df["high"] if "high" in df.columns else df["High"]
    rsi = df["rsi"]

    # Swing high: i. bar ortada, sol/sağda swing_len bar
    swing_highs: list[tuple[int, float, float]] = []  # (index, high, rsi)
    for i in range(swing_len, len(df) - swing_len):
        left = high.iloc[i - swing_len : i].max()
        right = high.iloc[i + 1 : i + 1 + swing_len].max()
        if high.iloc[i] >= left and high.iloc[i] >= right:
            swing_highs.append((i, float(high.iloc[i]), float(rsi.iloc[i])))

    if len(swing_highs) < 2:
        return False
    # Son iki swing high
    s1, s2 = swing_highs[-2], swing_highs[-1]
    price_up = s2[1] > s1[1]
    rsi_down = s2[2] < s1[2]
    return bool(price_up and rsi_down)


def detect_fvg_below_entry(
    df: pd.DataFrame,
    entry_price: float,
    atr: Optional[float] = None,
    atr_mult: float = 2.0,
    lookback: int = 30,
) -> Optional[tuple[float, float]]:
    """
    Giriş fiyatının altında Bullish FVG (Fair Value Gap) var mı?
    FVG: 3 ardışık mumda 1. mumun high < 3. mumun low -> boşluk [high_1, low_3].
    Girişin altında ve (opsiyonel) atr_mult * ATR mesafesinde bir FVG varsa (mıknatıs destek) döner.

    Returns:
        (fvg_low, fvg_high) veya None
    """
    if df is None or len(df) < 4:
        return None
    df = df.tail(lookback)
    high = df["high"] if "high" in df.columns else df["High"]
    low = df["low"] if "low" in df.columns else df["Low"]

    best_fvg: Optional[tuple[float, float]] = None
    best_dist = float("inf")

    for i in range(len(df) - 2):
        h0 = float(high.iloc[i])
        l2 = float(low.iloc[i + 2])
        if l2 > h0:  # Bullish FVG: gap [h0, l2]
            fvg_low, fvg_high = h0, l2
            if fvg_high >= entry_price:
                continue
            dist = entry_price - fvg_high
            if atr is not None and atr > 0 and dist > atr_mult * atr:
                continue
            if dist < best_dist:
                best_dist = dist
                best_fvg = (fvg_low, fvg_high)

    return best_fvg
