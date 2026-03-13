"""
Piyasa Bağlamı: BIST100 (XU100) endeks trendi ve risk modu (Market Regime).
Tarama öncesi 4H ve Günlük veri çekilir; endeks EMA50 altında veya RSI < 40 ise piyasa riskli kabul edilir.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from src.config import settings

# BIST100 endeks sembolü (TradingView: BIST:XU100)
INDEX_SYMBOL = "XU100"
INDEX_EXCHANGE = "BIST"
INDEX_EMA_PERIOD = 20   # RS / mevcut risk metinleri için (EMA20)
INDEX_EMA_REGIME = 50   # Market Regime: endeks EMA50 altındaysa riskli
REGIME_RSI_MIN = 40     # Endeks RSI bu değerin altındaysa piyasa riskli
RS_BARS = 10  # Göreceli güç için son 10 bar (gün)


def _index_rsi(close: pd.Series, period: int = 14) -> Optional[float]:
    """Endeks kapanış serisinden son bar RSI değerini döner."""
    import numpy as np
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


INDEX_VOLUME_MA_DAYS = 10  # Hacim ortalaması için son 10 gün


@dataclass
class MarketContext:
    """Tarama öncesi piyasa durumu (Market Regime)."""
    market_risk: bool          # Endeks EMA50 altında veya RSI < 40
    index_close: float
    index_ema20: float         # Mevcut metinler (XU100 < EMA20) ile uyum için
    index_df: Optional[pd.DataFrame] = None  # RS hesabı için (günlük)
    index_ema50: float = 0.0
    index_rsi: Optional[float] = None
    # XU100 hacim: endeks yükselirken hacim ortalamanın altındaysa boğa tuzağı uyarısı
    index_volume_below_avg: bool = False
    index_volume_warning: Optional[str] = None  # Sinyal mesajına eklenecek uyarı metni


def get_market_context(n_bars: int = 60) -> MarketContext:
    """
    XU100 endeksinin 4H ve günlük verisini çeker.
    Endeks fiyatı EMA50'nin altındaysa veya RSI < 40 ise market_risk=True.
    """
    try:
        from src.scrapers.tv_scraper import get_ohlc_bist
    except Exception:
        return MarketContext(
            market_risk=False, index_close=0.0, index_ema20=0.0,
            index_df=None, index_ema50=0.0, index_rsi=None,
        )

    data = get_ohlc_bist(
        INDEX_SYMBOL, INDEX_EXCHANGE,
        interval_4h=True, interval_daily=True,
        n_bars=n_bars,
    )
    df_d = data.get("1d")
    if df_d is None or df_d.empty or len(df_d) < max(INDEX_EMA_PERIOD, INDEX_EMA_REGIME):
        return MarketContext(
            market_risk=False, index_close=0.0, index_ema20=0.0,
            index_df=None, index_ema50=0.0, index_rsi=None,
        )

    close = df_d["Close"] if "Close" in df_d.columns else df_d["close"]
    ema20 = close.ewm(span=INDEX_EMA_PERIOD, adjust=False).mean()
    ema50 = close.ewm(span=INDEX_EMA_REGIME, adjust=False).mean()
    last_close = float(close.iloc[-1])
    last_ema20 = float(ema20.iloc[-1])
    last_ema50 = float(ema50.iloc[-1])
    index_rsi = _index_rsi(close, period=getattr(settings, "RSI_PERIOD", 14))

    # Market Regime: riskli = fiyat EMA50 altında VEYA RSI < 40
    market_risk = last_close < last_ema50 or (index_rsi is not None and index_rsi < REGIME_RSI_MIN)

    # XU100 hacim analizi: endeks yükselirken hacim 10 günlük ortalamanın altındaysa uyarı
    index_volume_below_avg = False
    index_volume_warning = None
    vol_col = None
    for c in ("Volume", "volume"):
        if c in df_d.columns:
            vol_col = df_d[c]
            break
    if vol_col is not None and len(df_d) >= INDEX_VOLUME_MA_DAYS + 1:
        vol_ma = vol_col.rolling(INDEX_VOLUME_MA_DAYS).mean()
        last_vol = float(vol_col.iloc[-1])
        vol_ma_last = float(vol_ma.iloc[-1])
        prev_close = float(close.iloc[-2])
        index_rising = last_close > prev_close
        if index_rising and vol_ma_last > 0 and last_vol < vol_ma_last:
            index_volume_below_avg = True
            index_volume_warning = (
                "Dikkat: Endeks yükseliyor ama hacim ortalamanın altında; sahte bir kırılım olabilir."
            )

    return MarketContext(
        market_risk=market_risk,
        index_close=last_close,
        index_ema20=last_ema20,
        index_df=df_d,
        index_ema50=last_ema50,
        index_rsi=index_rsi,
        index_volume_below_avg=index_volume_below_avg,
        index_volume_warning=index_volume_warning,
    )


def get_market_context_at_date(
    index_df: pd.DataFrame,
    as_of_date: pd.Timestamp,
) -> MarketContext:
    """
    Backtest için: Verilen tarihe kadar olan endeks verisiyle piyasa bağlamı hesaplar.
    EMA50 ve RSI < 40 ile Market Regime riski; RS hesabı için slice döner.
    """
    min_bars = max(INDEX_EMA_PERIOD, INDEX_EMA_REGIME)
    if index_df is None or index_df.empty or len(index_df) < min_bars:
        return MarketContext(
            market_risk=False, index_close=0.0, index_ema20=0.0,
            index_df=None, index_ema50=0.0, index_rsi=None,
        )
    try:
        cutoff = pd.Timestamp(as_of_date).normalize()
    except Exception:
        cutoff = as_of_date
    mask = index_df.index <= cutoff
    if not mask.any():
        return MarketContext(
            market_risk=False, index_close=0.0, index_ema20=0.0,
            index_df=None, index_ema50=0.0, index_rsi=None,
        )
    slice_df = index_df.loc[mask]
    if len(slice_df) < min_bars:
        return MarketContext(
            market_risk=False, index_close=0.0, index_ema20=0.0,
            index_df=None, index_ema50=0.0, index_rsi=None,
        )
    close = slice_df["Close"] if "Close" in slice_df.columns else slice_df["close"]
    ema20 = close.ewm(span=INDEX_EMA_PERIOD, adjust=False).mean()
    ema50 = close.ewm(span=INDEX_EMA_REGIME, adjust=False).mean()
    last_close = float(close.iloc[-1])
    last_ema20 = float(ema20.iloc[-1])
    last_ema50 = float(ema50.iloc[-1])
    index_rsi = _index_rsi(close, period=getattr(settings, "RSI_PERIOD", 14))
    market_risk = last_close < last_ema50 or (index_rsi is not None and index_rsi < REGIME_RSI_MIN)
    return MarketContext(
        market_risk=market_risk,
        index_close=last_close,
        index_ema20=last_ema20,
        index_df=slice_df,
        index_ema50=last_ema50,
        index_rsi=index_rsi,
    )


def compute_relative_strength(
    stock_daily: pd.DataFrame,
    index_daily: pd.DataFrame,
    bars: int = RS_BARS,
) -> Optional[float]:
    """
    Hisse getirisi / Endeks getirisi oranı.
    Son `bars` günde: RS = (hisse % değişim) / (endeks % değişim).
    RS > 1 => hisse endeksten daha iyi performans (göreceli güç).
    """
    if stock_daily is None or stock_daily.empty or index_daily is None or index_daily.empty:
        return None
    if len(stock_daily) < bars + 1 or len(index_daily) < bars + 1:
        return None

    c_s = stock_daily["Close"] if "Close" in stock_daily.columns else stock_daily["close"]
    c_i = index_daily["Close"] if "Close" in index_daily.columns else index_daily["close"]

    stock_return = (float(c_s.iloc[-1]) - float(c_s.iloc[-(bars + 1)])) / float(c_s.iloc[-(bars + 1)])
    index_return = (float(c_i.iloc[-1]) - float(c_i.iloc[-(bars + 1)])) / float(c_i.iloc[-(bars + 1)])

    if abs(index_return) < 1e-9:
        return 2.0 if stock_return > 0 else 0.0  # Endeks düz, hisse yükselmişse güçlü
    return round(stock_return / index_return, 4)
