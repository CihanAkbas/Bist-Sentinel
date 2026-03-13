"""
Grafik Snapshot: Sinyal görsel kanıtı — son N bar mum grafiği, EMA seviyeleri ve sinyal noktası.
mplfinance ile PNG üretilir; data/charts/{symbol}.png olarak kaydedilir, Telegram send_photo ile gönderilir.
"""
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

BARS_DEFAULT = 100

# Grafiklerin kaydedileceği varsayılan klasör
CHARTS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "charts"


def _ensure_ohlcv_columns(df: pd.DataFrame) -> pd.DataFrame:
    """mplfinance için Open, High, Low, Close, Volume sütunlarını sağlar."""
    df = df.copy()
    for lower, title in [("open", "Open"), ("high", "High"), ("low", "Low"), ("close", "Close"), ("volume", "Volume")]:
        if title not in df.columns and lower in df.columns:
            df[title] = df[lower]
    return df


def build_squeeze_chart(
    df_4h: pd.DataFrame,
    symbol: str,
    output_path: Optional[Path] = None,
    bars: int = BARS_DEFAULT,
    signal_entry: Optional[float] = None,
) -> Optional[Path]:
    """
    Son `bars` barın mum grafiğini çizer: EMA seviyeleri, BB/KC ve isteğe bağlı sinyal noktası.
    PNG olarak data/charts/{symbol}.png (veya output_path) kaydedilir.

    Args:
        df_4h: 4 saatlik OHLC + indicators (ema10, ema20, ema200, bb_*, kc_*)
        symbol: Hisse sembolü (başlık ve dosya adı için)
        output_path: Kaydedilecek dosya yolu. None ise data/charts/{symbol}.png
        bars: Gösterilecek mum sayısı (varsayılan 100)
        signal_entry: Sinyal giriş fiyatı; verilirse son bar üzerinde nokta ile işaretlenir

    Returns:
        Kaydedilen dosyanın Path'i; hata olursa None.
    """
    try:
        import mplfinance as mpf
    except ImportError:
        return None

    if df_4h is None or df_4h.empty or len(df_4h) < 20:
        return None

    df = df_4h.tail(bars).copy()
    df = _ensure_ohlcv_columns(df)

    # mplfinance index'in datetime olmasını ister
    if not isinstance(df.index, pd.DatetimeIndex):
        if "date" in df.columns:
            df = df.set_index("date")
        elif "Date" in df.columns:
            df = df.set_index("Date")

    required = ["Open", "High", "Low", "Close"]
    if not all(c in df.columns for c in required):
        return None

    if output_path is None:
        CHARTS_DIR.mkdir(parents=True, exist_ok=True)
        output_path = CHARTS_DIR / f"{symbol.upper()}.png"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    addplots = []
    colors_bb = "#1f77b4"
    colors_kc = "#ff7f0e"
    color_ema200 = "#2ca02c"
    color_ema20 = "#17becf"

    # Bollinger & Keltner
    if "bb_upper" in df.columns:
        addplots.append(mpf.make_addplot(df["bb_upper"], color=colors_bb, width=0.6))
    if "bb_mid" in df.columns:
        addplots.append(mpf.make_addplot(df["bb_mid"], color=colors_bb, width=0.4, linestyle="--"))
    if "bb_lower" in df.columns:
        addplots.append(mpf.make_addplot(df["bb_lower"], color=colors_bb, width=0.6))
    if "kc_upper" in df.columns:
        addplots.append(mpf.make_addplot(df["kc_upper"], color=colors_kc, width=0.6))
    if "kc_lower" in df.columns:
        addplots.append(mpf.make_addplot(df["kc_lower"], color=colors_kc, width=0.6))
    # EMA seviyeleri
    if "ema20" in df.columns:
        addplots.append(mpf.make_addplot(df["ema20"], color=color_ema20, width=0.6))
    if "ema200" in df.columns:
        addplots.append(mpf.make_addplot(df["ema200"], color=color_ema200, width=0.8))

    # Sinyal noktası: son bar üzerinde giriş fiyatında işaret
    if signal_entry is not None and not np.isnan(signal_entry) and len(df) > 0:
        signal_series = pd.Series(index=df.index, dtype=float)
        signal_series.iloc[-1] = float(signal_entry)
        addplots.append(
            mpf.make_addplot(
                signal_series,
                type="scatter",
                marker="^",
                markersize=120,
                color="lime",
            )
        )

    volume = "Volume" in df.columns
    try:
        mpf.plot(
            df,
            type="candle",
            style="charles",
            title=f"{symbol.upper()} — Grafik Snapshot (4H)",
            ylabel="Fiyat",
            volume=volume,
            addplot=addplots if addplots else None,
            savefig=str(output_path),
            figsize=(12, 7),
        )
        return output_path
    except Exception:
        return None
