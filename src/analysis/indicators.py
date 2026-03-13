"""
Teknik indikatörler: EMA, Bollinger Bands, Keltner Channels, RSI, Ortalama Hacim.
Tüm periyot ve eşikler src.config.settings üzerinden okunur.
"""
from __future__ import annotations

import pandas as pd
import numpy as np

from src.config import settings

try:
    import pandas_ta as ta
    HAS_PANDAS_TA = True
except ImportError:
    HAS_PANDAS_TA = False


def _ema(series: pd.Series, length: int) -> pd.Series:
    """EMA hesaplar (pandas_ta yoksa)."""
    return series.ewm(span=length, adjust=False).mean()


def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """RSI hesaplar (pandas_ta yoksa)."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 20) -> pd.Series:
    """ATR (Average True Range) hesaplar."""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(span=length, adjust=False).mean()


def add_atr(df: pd.DataFrame, high_col: str = "high", low_col: str = "low", close_col: str = "close", length: int | None = None) -> pd.DataFrame:
    """
    14 (veya config: ATR_PERIOD) periyotluk ATR ekler.
    Trade plan (stop-loss, TP1, TP2) hesaplarında kullanılır.
    """
    length = length or settings.ATR_PERIOD
    df = df.copy()
    high = df[high_col] if high_col in df.columns else df["High"]
    low = df[low_col] if low_col in df.columns else df["Low"]
    close = df[close_col] if close_col in df.columns else df["Close"]
    df["atr14"] = _atr(high, low, close, length)
    return df


def add_ema10(df: pd.DataFrame, price_col: str = "close", length: int = 10) -> pd.DataFrame:
    """Trailing stop sıkılaştırma için EMA10 (agresif trend izleme)."""
    c = df[price_col] if price_col in df.columns else df["Close"]
    df = df.copy()
    if HAS_PANDAS_TA:
        df["ema10"] = ta.ema(c, length=length)
    else:
        df["ema10"] = _ema(c, length)
    return df


def add_ema20(df: pd.DataFrame, price_col: str = "close", length: int = 20) -> pd.DataFrame:
    """4H trailing stop için kısa vadeli EMA20 ekler."""
    c = df[price_col] if price_col in df.columns else df["Close"]
    df = df.copy()
    if HAS_PANDAS_TA:
        df["ema20"] = ta.ema(c, length=length)
    else:
        df["ema20"] = _ema(c, length)
    return df


def add_ema50(df: pd.DataFrame, price_col: str = "close", length: int = 50) -> pd.DataFrame:
    """Orta vade trend: Fiyat > EMA50 (yükselişe yeni girmiş hisseler)."""
    c = df[price_col] if price_col in df.columns else df["Close"]
    df = df.copy()
    if HAS_PANDAS_TA:
        df["ema50"] = ta.ema(c, length=length)
    else:
        df["ema50"] = _ema(c, length)
    return df


def add_ema200(df: pd.DataFrame, price_col: str = "close") -> pd.DataFrame:
    """DataFrame'e EMA (config: EMA_PERIOD) sütunu ekler."""
    c = df[price_col] if price_col in df.columns else df["Close"]
    length = settings.EMA_PERIOD
    if HAS_PANDAS_TA:
        df = df.copy()
        df["ema200"] = ta.ema(c, length=length)
        return df
    df = df.copy()
    df["ema200"] = _ema(c, length)
    return df


def add_bollinger_keltner(df: pd.DataFrame, high_col: str = "high", low_col: str = "low", close_col: str = "close") -> pd.DataFrame:
    """
    Bollinger Bands ve Keltner Channels ekler (config: BB_PERIOD, BB_STD, KC_PERIOD, KC_MULT).
    """
    df = df.copy()
    high = df[high_col] if high_col in df.columns else df["High"]
    low = df[low_col] if low_col in df.columns else df["Low"]
    close = df[close_col] if close_col in df.columns else df["Close"]

    bb_len, bb_std = settings.BB_PERIOD, settings.BB_STD
    mid = close.rolling(bb_len).mean()
    std = close.rolling(bb_len).std()
    df["bb_mid"] = mid
    df["bb_upper"] = mid + bb_std * std
    df["bb_lower"] = mid - bb_std * std

    kc_len, kc_mult = settings.KC_PERIOD, settings.KC_MULT
    atr = _atr(high, low, close, kc_len)
    kc_mid = close.ewm(span=kc_len, adjust=False).mean()
    df["kc_upper"] = kc_mid + kc_mult * atr
    df["kc_lower"] = kc_mid - kc_mult * atr
    df["kc_mid"] = kc_mid

    return df


def add_rsi(df: pd.DataFrame, close_col: str = "close", length: int | None = None) -> pd.DataFrame:
    """RSI ekler (config: RSI_PERIOD)."""
    length = length or settings.RSI_PERIOD
    df = df.copy()
    close = df[close_col] if close_col in df.columns else df["Close"]
    if HAS_PANDAS_TA:
        df["rsi"] = ta.rsi(close, length=length)
    else:
        df["rsi"] = _rsi(close, length)
    return df


def add_momentum(df: pd.DataFrame, close_col: str = "close", length: int | None = None) -> pd.DataFrame:
    """Momentum osilatörü (config: MOMENTUM_PERIOD). Squeeze yön tayininde 0'ın üstü = bullish."""
    length = length or settings.MOMENTUM_PERIOD
    df = df.copy()
    close = df[close_col] if close_col in df.columns else df["Close"]
    df["momentum"] = close - close.rolling(length).mean()
    return df


def add_volume_ma(df: pd.DataFrame, volume_col: str = "volume", length: int | None = None) -> pd.DataFrame:
    """Ortalama hacim ekler (config: VOLUME_MA_PERIOD). Sütun adı volume_ma20 uyumluluk için."""
    length = length or settings.VOLUME_MA_PERIOD
    df = df.copy()
    vol = df[volume_col] if volume_col in df.columns else df["Volume"]
    df["volume_ma20"] = vol.rolling(length).mean()
    return df


def get_same_slot_avg_volume(
    df: pd.DataFrame,
    bar_index: int,
    n_slots: int | None = None,
    volume_col: str = "volume",
) -> tuple[float | None, float | None]:
    """
    Göreceli hacim (RVOL): Aynı saat dilimindeki son n_slots bar'ın hacim ortalaması.
    4H veride "aynı saat dilimi" = aynı (saat, dakika) kapanan barlar (örn. 10:00–14:00 her gün).

    Returns:
        (current_volume, same_slot_avg_volume). Hesaplanamazsa (None, None) veya (current, None).
    """
    n_slots = n_slots or settings.RVOL_SAME_SLOT_DAYS
    if bar_index < 0:
        bar_index = len(df) + bar_index
    vol = df[volume_col] if volume_col in df.columns else df.get("Volume")
    if vol is None or bar_index < 0 or bar_index >= len(df):
        return (None, None)
    try:
        current = float(vol.iloc[bar_index])
    except (TypeError, ValueError):
        return (None, None)
    if pd.isna(current):
        return (None, None)

    index = df.index
    try:
        bar_ts = index[bar_index]
        bar_time = pd.Timestamp(bar_ts).time()
    except Exception:
        return (float(current), None)

    # Aynı saat dilimindeki bar indeksleri (mevcut dahil, kronolojik)
    same_slot_indices = [
        i for i in range(len(df))
        if pd.Timestamp(index[i]).time() == bar_time and i <= bar_index
    ]
    # Mevcut bar hariç son n_slots bar'ın ortalaması
    past = [i for i in same_slot_indices if i < bar_index][-n_slots:]
    if len(past) < 1:
        return (float(current), None)
    avg = vol.iloc[past].mean()
    if pd.isna(avg) or avg <= 0:
        return (float(current), None)
    return (float(current), float(avg))


def normalize_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """TradingView bazen 'Open','High','Low','Close','Volume' döndürür; hepsini küçük harfe çevirir."""
    df = df.copy()
    rename = {}
    for c in df.columns:
        if c == "Open": rename[c] = "open"
        elif c == "High": rename[c] = "high"
        elif c == "Low": rename[c] = "low"
        elif c == "Close": rename[c] = "close"
        elif c == "Volume": rename[c] = "volume"
    df = df.rename(columns=rename)
    return df


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Tek bir OHLC DataFrame'e tüm strateji indikatörlerini ekler (ATR, EMA10/20/50/200 trailing dahil)."""
    df = normalize_column_names(df)
    df = add_ema10(df, "close")
    df = add_ema20(df, "close")
    df = add_ema50(df, "close")
    df = add_ema200(df, "close")
    df = add_bollinger_keltner(df, "high", "low", "close")
    df = add_atr(df, "high", "low", "close")
    df = add_momentum(df, "close")
    df = add_rsi(df, "close")
    df = add_volume_ma(df, "volume")
    return df
