"""
The Triple Confirmation motoru:
1) Macro Filter: Fiyat > EMA (günlük)
2) Volatility Squeeze: BB genişliği < KC genişliği = Squeeze On; BB > KC = Squeeze Off (patlama)
3) Yön tayini: Kapanış > KC üst bandı VEYA Momentum Osilatörü > 0
4) Volume & Momentum: Volume > Volume_MA * multiplier ve RSI > threshold (config'den)
Trade Architect: ATR tabanlı giriş, stop-loss, TP1, TP2 ve R/R oranı.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from src.config import settings
from .indicators import add_all_indicators
from .price_actions import detect_fvg_below_entry


@dataclass
class Signal:
    """Üretilen sinyal özeti + Trade Plan + Piyasa bağlamı / filtreler."""
    symbol: str
    timeframe: str  # "4h" veya "1d"
    strategy: str   # örn. "Squeeze Breakout + Volume Spike"
    price: float
    trend: str      # "Pozitif (Günlük EMA50 Üstü)" / "Negatif"
    kap_status: str # "Bekleniyor" / "Analiz Edilmedi" / ileride "Pozitif"/"Negatif"
    rsi: Optional[float] = None
    volume_ratio: Optional[float] = None
    # Trade Plan (ATR tabanlı)
    entry: Optional[float] = None       # Giriş fiyatı (sinyal kapanışı)
    stop_loss: Optional[float] = None  # Zarar kes
    tp1: Optional[float] = None        # Hedef 1 (R:R 1:1)
    tp2: Optional[float] = None        # Hedef 2 (R:R 1:2)
    rr_ratio: Optional[float] = None   # Risk/Ödül oranı (örn. 2.0 = 1:2)
    atr: Optional[float] = None        # Sinyal barı ATR değeri
    # Piyasa bağlamı & gelişmiş filtreleme
    market_risk: bool = False          # XU100 endeksi EMA20 altında
    relative_strength: Optional[float] = None  # Hisse/Endeks getiri oranı (RS > 1 = endeks üstü)
    fvg_support_below: Optional[tuple[float, float]] = None  # FVG destek bölgesi (low, high)
    rvol_ratio: Optional[float] = None  # Göreceli hacim: mevcut 4H hacim / aynı saat dilimi ortalaması (>1 = patlama)
    signal_bar_high: Optional[float] = None  # Giriş onayı: bu seviyenin yukarı kırılması beklenir (sonraki 3 bar)
    recommended_lot: Optional[int] = None   # Önerilen adet: (Kasa * %2) / (Giriş - Stop); kullanıcı rehberi
    momentum_bypass: bool = False           # True: Squeeze yok, RS>1.2 + Hacim>2x ile giriş (main/backtest RS kontrolü gerekir)
    sector_note: Optional[str] = None       # Sektör korelasyonu: benzer hisselerin teknik durumu özeti


def _bb_width(row: pd.Series) -> Optional[float]:
    """Bollinger Band genişliği = bb_upper - bb_lower."""
    u, l = row.get("bb_upper"), row.get("bb_lower")
    if pd.isna(u) or pd.isna(l):
        return None
    return float(u - l)


def _kc_width(row: pd.Series) -> Optional[float]:
    """Keltner Channel genişliği = kc_upper - kc_lower."""
    u, l = row.get("kc_upper"), row.get("kc_lower")
    if pd.isna(u) or pd.isna(l):
        return None
    return float(u - l)


def _squeeze_on(row: pd.Series) -> bool:
    """
    Sıkışma başlangıcı: Bollinger Band genişliği < Keltner Channel genişliği.
    """
    bb_w = _bb_width(row)
    kc_w = _kc_width(row)
    if bb_w is None or kc_w is None or kc_w <= 0:
        return False
    return bb_w < kc_w


def _squeeze_off(row: pd.Series) -> bool:
    """
    Patlama (Squeeze Off): BB genişliği > KC genişliği.
    """
    bb_w = _bb_width(row)
    kc_w = _kc_width(row)
    if bb_w is None or kc_w is None:
        return False
    return bb_w > kc_w


def _bullish_direction(row: pd.Series) -> bool:
    """
    Yön tayini: Kapanış KC üst bandının üzerinde VEYA Momentum Osilatörü 0'ın üzerinde.
    """
    close = row.get("close") or row.get("Close")
    kc_u = row.get("kc_upper")
    momentum = row.get("momentum")
    if pd.isna(close):
        return False
    if not pd.isna(kc_u) and close > kc_u:
        return True
    if not pd.isna(momentum) and momentum > 0:
        return True
    return False


def _squeeze_exit_bullish(row: pd.Series, prev: pd.Series) -> bool:
    """
    Bullish çıkış: önceki bar Squeeze On, bu bar Squeeze Off ve yön bullish.
    (BB genişliği < KC genişliği -> BB genişliği > KC genişliği + close > kc_upper veya momentum > 0)
    """
    if prev is None:
        return False
    was_squeeze = _squeeze_on(prev)
    now_squeeze_off = _squeeze_off(row)
    if not was_squeeze or not now_squeeze_off:
        return False
    return _bullish_direction(row)


# Hacim: config.VOLUME_MULTIPLIER (1.1 = ortalama üstü yeterli); momentum path aynı çarpan
EXHAUSTION_VOLUME_MULT = 4.0  # Bu oranın üstü = işlem açma, uyarı ver


def _volume_spike(row: pd.Series, mult: float | None = None) -> bool:
    """Volume >= Volume_MA * mult (varsayılan: config.VOLUME_MULTIPLIER)."""
    vol = row.get("volume") or row.get("Volume")
    vol_ma = row.get("volume_ma20")
    if pd.isna(vol) or pd.isna(vol_ma) or vol_ma <= 0:
        return False
    m = mult if mult is not None else settings.VOLUME_MULTIPLIER
    return float(vol) >= float(vol_ma) * m


def _rsi_above_threshold(row: pd.Series, min_rsi: float | None = None) -> bool:
    """RSI > min_rsi (varsayılan: config.RSI_THRESHOLD)."""
    rsi = row.get("rsi")
    if pd.isna(rsi):
        return False
    th = min_rsi if min_rsi is not None else settings.RSI_THRESHOLD
    return rsi > th


def _price_above_ema50(row: pd.Series) -> bool:
    """Orta vade trend: Fiyat > EMA50 (Hybrid-News: kritik haber varken yeterli)."""
    price = row.get("close") or row.get("Close")
    ema = row.get("ema50")
    if pd.isna(price) or pd.isna(ema):
        return False
    return price > ema


def _price_above_ema200(row: pd.Series) -> bool:
    """Uzun vade trend: Fiyat > EMA200 (normal sinyal için zorunlu)."""
    price = row.get("close") or row.get("Close")
    ema = row.get("ema200")
    if pd.isna(price) or pd.isna(ema):
        return False
    return price > ema


def build_trade_plan(row: pd.Series) -> tuple[float, float, float, float, float] | None:
    """
    Trade Architect: ATR tabanlı giriş, stop-loss ve hedefler.
    Entry = kapanış, Stop = Entry - (STOP_ATR_MULT * ATR), TP1 = Entry + (TP1_ATR_MULT * ATR), TP2 = Entry + (TP2_ATR_MULT * ATR).
    R/R oranı = TP2 mesafesi / Stop mesafesi (örn. 1:2 -> 2.0).
    """
    close = row.get("close") or row.get("Close")
    atr_val = row.get("atr14")
    if pd.isna(close) or pd.isna(atr_val) or atr_val <= 0:
        return None
    entry = float(close)
    atr = float(atr_val)
    stop_loss = entry - settings.STOP_ATR_MULT * atr
    tp1 = entry + settings.TP1_ATR_MULT * atr
    tp2 = entry + settings.TP2_ATR_MULT * atr
    risk = entry - stop_loss
    reward_tp2 = tp2 - entry
    rr_ratio = round(reward_tp2 / risk, 2) if risk > 0 else 0.0
    return (round(entry, 2), round(stop_loss, 2), round(tp1, 2), round(tp2, 2), rr_ratio)


class TripleConfirmationEngine:
    """
    Günlük veri ile macro filter (normal: EMA200; Hybrid-News: EMA50), 4 saatlik squeeze + volume + RSI.
    """

    def __init__(self, daily_df: pd.DataFrame, df_4h: pd.DataFrame, symbol: str):
        """
        daily_df: Günlük OHLC (trend filtresi için)
        df_4h: 4 saatlik OHLC (sinyal için)
        symbol: Hisse sembolü
        """
        self.symbol = symbol
        self.daily = add_all_indicators(daily_df.copy())
        self.data_4h = add_all_indicators(df_4h.copy())

    def macro_filter_ok(self, use_ema50: bool = False) -> bool:
        """Günlük trend: use_ema50=True → Fiyat > EMA50 (kritik haber); False → Fiyat > EMA200 (normal)."""
        if self.daily.empty:
            return False
        last = self.daily.iloc[-1]
        return _price_above_ema50(last) if use_ema50 else _price_above_ema200(last)

    def squeeze_on_current_bar(self) -> bool:
        """4H son barında sıkışma (BB genişliği < KC genişliği) var mı? Loglama için."""
        if self.data_4h.empty:
            return False
        return _squeeze_on(self.data_4h.iloc[-1])

    def check_signal(self, news_override_score: Optional[int] = None) -> Optional[Signal] | tuple[str, None]:
        """
        Momentum & Trend: (1) Squeeze breakout + volume + RSI VEYA (2) Momentum bypass.
        Hybrid-News (Gemini 9+): Normalde EMA200 → EMA50 yeterli; Hacim 1.2x → 1.0x (ortalama) yeterli.
        """
        if self.data_4h.empty or len(self.data_4h) < 2:
            return None

        news_override = news_override_score is not None and news_override_score >= 9
        # Normal: EMA200, hacim config (örn. 1.1x). Kritik haber: EMA50, hacim 1.0x (ortalama)
        vol_mult = 1.0 if news_override else settings.VOLUME_MULTIPLIER
        rsi_min = max(35, int(settings.RSI_THRESHOLD * 0.8)) if news_override else settings.RSI_THRESHOLD

        # Macro: normalde Fiyat > EMA200; kritik haber varsa Fiyat > EMA50 yeterli
        if not self.macro_filter_ok(use_ema50=news_override):
            return None

        row = self.data_4h.iloc[-1]
        prev = self.data_4h.iloc[-2]
        vol = row.get("volume") or row.get("Volume")
        vol_ma = row.get("volume_ma20")
        # Aşırı hacim (tükeniş) her iki path için de elenir
        if vol is not None and vol_ma is not None and not (pd.isna(vol) or pd.isna(vol_ma) or vol_ma <= 0):
            if float(vol) > float(vol_ma) * EXHAUSTION_VOLUME_MULT:
                return ("exhaustion_volume", None)

        momentum_bypass = False
        # Path 1: Klasik Squeeze breakout
        if _squeeze_exit_bullish(row, prev):
            if not _volume_spike(row, mult=vol_mult):
                return None
            if not _rsi_above_threshold(row, min_rsi=rsi_min):
                return None
        else:
            # Path 2: Momentum bypass — Squeeze yok; Hacim >= vol_mult + bullish; RSI < 70
            if vol_ma is None or pd.isna(vol_ma) or vol_ma <= 0 or vol is None or pd.isna(vol):
                return None
            vol_ratio = float(vol) / float(vol_ma)
            if vol_ratio < vol_mult:
                return None
            if not _bullish_direction(row):
                return None
            rsi_m = row.get("rsi")
            if rsi_m is not None and not pd.isna(rsi_m) and float(rsi_m) >= 70:
                return None  # Momentum Guard: RSI >= 70 ise momentum bypass sinyali verme
            momentum_bypass = True

        price = float(row.get("close") or row.get("Close"))
        rsi_val = float(row["rsi"]) if "rsi" in row and pd.notna(row["rsi"]) else None
        vol = row.get("volume") or row.get("Volume")
        vol_ma = row.get("volume_ma20")
        vol_ratio = float(vol / vol_ma) if vol_ma and vol_ma > 0 else None
        # RVOL/Divergence kaldırıldı; sadece RS, EMA, Volume odaklı

        # Trade Plan (ATR tabanlı giriş, stop, TP1, TP2, R/R)
        plan = build_trade_plan(row)
        entry = stop_loss = tp1 = tp2 = rr_ratio = atr_val = None
        fvg_support: Optional[tuple[float, float]] = None
        if plan:
            entry, stop_loss, tp1, tp2, rr_ratio = plan
            atr_val = float(row.get("atr14")) if pd.notna(row.get("atr14")) else None
            if atr_val is not None:
                atr_val = round(atr_val, 2)
            # FVG: Girişin altında mıknatıs destek bölgesi var mı?
            if entry is not None:
                fvg_support = detect_fvg_below_entry(
                    self.data_4h, entry, atr=atr_val, atr_mult=2.0, lookback=30
                )

        high = row.get("high") or row.get("High")
        signal_bar_high = round(float(high), 2) if high is not None and not pd.isna(high) else None

        # Önerilen lot: (Toplam_Kasa * %risk) / (Giriş - Stop); her işlemde kasanın %2'sini riske et
        recommended_lot = None
        if entry is not None and stop_loss is not None and entry > stop_loss:
            risk_per_unit = entry - stop_loss
            if risk_per_unit > 0:
                capital = getattr(settings, "TRADING_CAPITAL", 100_000.0) or 100_000.0
                risk_pct = getattr(settings, "RISK_PCT", 2.0) or 2.0
                risk_amount = capital * (risk_pct / 100.0)
                lot_float = risk_amount / risk_per_unit
                recommended_lot = max(0, int(round(lot_float)))

        strategy_name = "Momentum Breakout (RS+Hacim)" if momentum_bypass else "Squeeze Breakout + Volume Spike"
        trend_str = "Pozitif (Günlük EMA50 Üstü)" if news_override else "Pozitif (Günlük EMA200 Üstü)"
        return Signal(
            symbol=self.symbol,
            timeframe="4h",
            strategy=strategy_name,
            price=round(price, 2),
            trend=trend_str,
            kap_status="Bekleniyor",
            rsi=round(rsi_val, 1) if rsi_val is not None else None,
            volume_ratio=round(vol_ratio, 2) if vol_ratio is not None else None,
            entry=entry,
            stop_loss=stop_loss,
            tp1=tp1,
            tp2=tp2,
            rr_ratio=rr_ratio,
            atr=atr_val,
            market_risk=False,  # Main'de context ile doldurulur
            relative_strength=None,  # Main'de index ile hesaplanır
            fvg_support_below=fvg_support,
            rvol_ratio=None,  # Filtre temizliği: RVOL kaldırıldı
            signal_bar_high=signal_bar_high,
            recommended_lot=recommended_lot,
            momentum_bypass=momentum_bypass,
        )
