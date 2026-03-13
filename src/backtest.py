"""
Backtest modülü: Geçmiş veride strateji sinyallerini simüle eder.
"Bu sinyalleri verseydi ne kadar kazandırırdı?" sorusuna yanıt üretir.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from src.config import settings
from src.analysis.squeeze_engine import TripleConfirmationEngine, Signal
from src.analysis.market_context import (
    get_market_context_at_date,
    compute_relative_strength,
    INDEX_SYMBOL,
    INDEX_EXCHANGE,
)


# Giriş için gerekli minimum günlük bar (EMA200 için)
MIN_DAILY_BARS = 200


def _get_signal_at_bar(
    df_1d: pd.DataFrame,
    df_4h: pd.DataFrame,
    symbol: str,
    bar_index: int,
    index_df_1d: Optional[pd.DataFrame] = None,
) -> Optional[Signal]:
    """
    Belirli bir 4H bar indeksinde sinyal üretilir mi? (Geçmişe bakan backtest için.)
    Momentum & Trend: Squeeze path veya Momentum bypass (RS>1.2 + Hacim>2x). Sadece RS, EMA, Volume.
    index_df_1d verilirse piyasa riski ve RS hesaplanır.
    """
    if bar_index < 2 or bar_index >= len(df_4h):
        return None
    df_4h_slice = df_4h.iloc[: bar_index + 1]
    bar_ts = df_4h_slice.index[bar_index]
    try:
        cutoff = pd.Timestamp(bar_ts).normalize()
        df_1d_slice = df_1d[df_1d.index <= cutoff]
    except Exception:
        df_1d_slice = df_1d[df_1d.index <= bar_ts]
    if len(df_1d_slice) < max(MIN_DAILY_BARS, settings.EMA_PERIOD):
        return None
    if len(df_4h_slice) < 2:
        return None
    engine = TripleConfirmationEngine(df_1d_slice, df_4h_slice, symbol)
    result = engine.check_signal()
    # Engine bazen eleme nedenini döner: ("divergence", None) veya ("rvol", None)
    if isinstance(result, tuple):
        return None
    if result is None:
        return None
    signal = result
    # Piyasa bağlamı ve RS filtreleri; RS Booster: RS > 1.2 ise tüm RSI/endeks filtreleri kapatılır
    if index_df_1d is not None and not index_df_1d.empty:
        context = get_market_context_at_date(index_df_1d, bar_ts)
        signal.market_risk = context.market_risk
        rs = compute_relative_strength(engine.daily, context.index_df) if context.index_df is not None else None
        signal.relative_strength = rs
        # Momentum bypass sinyali sadece RS > 1.2 ise kabul
        if getattr(signal, "momentum_bypass", False) and (rs is None or rs <= 1.2):
            return None
        leader_stock = rs is not None and rs > 1.2
        if not leader_stock:
            if context.market_risk and (signal.rsi is None or signal.rsi <= settings.MARKET_RISK_RSI_MIN):
                return None
            if rs is not None and rs < 1:
                return None
    return signal


def _get_ohlc(row: pd.Series) -> tuple[float, float, float]:
    """Bar'dan high, low, close değerlerini al (sütun adı farklılıklarına toleranslı)."""
    high = row.get("high") or row.get("High")
    low = row.get("low") or row.get("Low")
    close = row.get("close") or row.get("Close")
    return (float(high), float(low), float(close))


# Dinamik çıkış: min kâr %10 (endeks > EMA20), trailing stop, RSI aşırı alım çıkışı
MIN_PROFIT_PCT = 10.0           # Endeks > EMA20 iken minimum kâr oranı
TRAILING_ACTIVATION_PCT = 5.0   # Kâr %5'e gelince trailing stop devreye girer
RSI_OVERBOUGHT = 75             # RSI bu seviyenin üstüne çıkıp kırılınca çıkış
RSI_EXIT_MIN_PROFIT_PCT = 7.0   # RSI çıkışı için en az bu kâr gerekli
TIME_EXIT_BARS = 30             # 30 bar (4H) içinde hedef/stop yoksa time_exit ile kapat
TIME_OUT_BARS = 15              # Verimlilik: 15 bar — kağıda süre tanı; %1.5 altında time_out
EFFICIENCY_MIN_PROFIT_PCT = 1.5 # Time-out eşiği: bu kârın altında sermaye serbest bırakılır
ENTRY_HIGH_CONFIRM_BARS = 3     # Sinyal sonrası en fazla 3 bar içinde high kırılımı gerekli
EMERGENCY_STOP_ATR_MULT = 2.5   # Felaket stopu: girişten 2.5*ATR aşağı = anında çıkış (korunur)
TP1_CLOSE_PCT = 0.50            # TP1'de pozisyonun %50'si nakde; kalan %50 EMA10 trailing


def simulate_exit(
    df_4h: pd.DataFrame,
    start_bar: int,
    entry: float,
    stop_loss: float,
    tp1: float,
    tp2: float,
) -> tuple[int, float, str]:
    """
    Eski sabit TP1/TP2 çıkışı (geriye dönük uyumluluk; indikatörsüz df ile çağrılırsa).
    """
    for i in range(start_bar, len(df_4h)):
        row = df_4h.iloc[i]
        high, low, close = _get_ohlc(row)
        if low <= stop_loss:
            return (i, stop_loss, "stop")
        if high >= tp2:
            return (i, tp2, "tp2")
        if high >= tp1:
            return (i, tp1, "tp1")
    last = df_4h.iloc[-1]
    _, _, close = _get_ohlc(last)
    return (len(df_4h) - 1, close, "end_of_data")


def simulate_exit_advanced(
    df_4h: pd.DataFrame,
    start_bar: int,
    entry: float,
    stop_loss: float,
    tp1: float,
    tp2: float,
    market_risk: bool,
    atr: Optional[float] = None,
) -> tuple[int, float, str]:
    """
    TP1'de %50 nakde çevir, kalan %50 EMA10 trailing. Felaket stopu 2.5*ATR korunur.
    RS weak exit kaldırıldı. Efficiency: 15 bar, %1.5 min kâr.
    """
    current_stop = stop_loss
    prev_rsi: Optional[float] = None
    partial_closed = False  # TP1'de %50 kapatıldı; çıkışta blend = 0.5*tp1 + 0.5*exit_price
    breakeven_price = entry * 1.005
    emergency_stop = (entry - EMERGENCY_STOP_ATR_MULT * atr) if atr is not None and atr > 0 else None

    def _exit(exit_price: float, reason: str) -> tuple[int, float, str]:
        if partial_closed:
            blended = TP1_CLOSE_PCT * tp1 + (1.0 - TP1_CLOSE_PCT) * exit_price
            return (i, round(blended, 4), reason)
        return (i, round(exit_price, 4), reason)

    for i in range(start_bar, len(df_4h)):
        row = df_4h.iloc[i]
        high, low, close = _get_ohlc(row)
        profit_pct = (close - entry) / entry * 100.0
        bars_held = i - start_bar + 1
        rsi_val = row.get("rsi")
        if rsi_val is None or (hasattr(rsi_val, "__float__") and pd.isna(rsi_val)):
            rsi_val = None
        else:
            rsi_val = float(rsi_val)
        ema10_val = row.get("ema10")
        if ema10_val is not None and not (hasattr(ema10_val, "__float__") and pd.isna(ema10_val)):
            ema10_val = float(ema10_val)
        else:
            ema10_val = None

        # 0) Hard Stop (Felaket): 2.5*ATR aşağı = anında çıkış (korunur)
        if emergency_stop is not None and low <= emergency_stop:
            return _exit(emergency_stop, "emergency_stop")

        # 1) Efficiency Stop: 15 bar geçti, %1.5 kâr yok → time_out
        if bars_held >= TIME_OUT_BARS and profit_pct < EFFICIENCY_MIN_PROFIT_PCT:
            return _exit(close, "time_out")

        # 2) Time stop (30 bar)
        if bars_held >= TIME_EXIT_BARS:
            return _exit(close, "time_exit")

        # 3) Stop / trailing stop
        if low <= current_stop:
            return _exit(current_stop, "trailing_stop" if current_stop > stop_loss else "stop")

        # 4) RSI exit
        if profit_pct >= RSI_EXIT_MIN_PROFIT_PCT and prev_rsi is not None and prev_rsi >= RSI_OVERBOUGHT:
            if rsi_val is not None and rsi_val < RSI_OVERBOUGHT:
                return _exit(close, "rsi_exit")
        prev_rsi = rsi_val

        # 5) TP1 sonrası: EMA10 trailing; TP1 öncesi: %5+ kârda prev_low + EMA10
        if partial_closed and ema10_val is not None:
            current_stop = max(current_stop, ema10_val)
        elif profit_pct >= TRAILING_ACTIVATION_PCT:
            prev_low = None
            if i > 0:
                prev_row = df_4h.iloc[i - 1]
                prev_low = prev_row.get("low") or prev_row.get("Low")
                if prev_low is not None and not (hasattr(prev_low, "__float__") and pd.isna(prev_low)):
                    prev_low = float(prev_low)
            candidate = None
            if prev_low is not None:
                candidate = prev_low
            if ema10_val is not None:
                candidate = max(candidate, ema10_val) if candidate is not None else ema10_val
            if candidate is not None:
                current_stop = max(current_stop, candidate)

        # 6) TP1: %50 nakde çevir, stop maliyete çek; kalan %50 EMA10 trailing ile devam
        if high >= tp1 and not partial_closed:
            partial_closed = True
            current_stop = max(current_stop, breakeven_price)
        if high >= tp2:
            return _exit(tp2, "tp2")
        if not market_risk:
            min_price = entry * (1 + MIN_PROFIT_PCT / 100.0)
            if high >= min_price:
                return _exit(min_price, "min_profit")

    last = df_4h.iloc[-1]
    _, _, close = _get_ohlc(last)
    return _exit(close, "end_of_data") if partial_closed else (len(df_4h) - 1, round(close, 4), "end_of_data")


@dataclass
class Trade:
    """Tek bir backtest işlemi."""
    symbol: str
    entry_bar: int
    exit_bar: int
    entry_price: float
    exit_price: float
    exit_reason: str  # stop, tp1, tp2, end_of_data
    pnl: float
    pnl_pct: float
    entry_time: Optional[pd.Timestamp] = None
    exit_time: Optional[pd.Timestamp] = None


def _max_drawdown_pct(pnl_pcts: list[float]) -> float:
    """Cumulative return üzerinden maksimum drawdown (yüzde puan)."""
    if not pnl_pcts:
        return 0.0
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnl_pcts:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


def _max_consecutive_losses(trades: list[Trade]) -> int:
    """Ardışık zarar sayısının maksimumu."""
    if not trades:
        return 0
    best = 0
    cur = 0
    for t in trades:
        if t.pnl <= 0:
            cur += 1
            if cur > best:
                best = cur
        else:
            cur = 0
    return best


@dataclass
class BacktestResult:
    """Backtest özet sonucu + risk metrikleri."""
    symbol: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    total_pnl: float
    total_pnl_pct: float
    avg_pnl_per_trade: float
    trades: list[Trade] = field(default_factory=list)
    max_drawdown_pct: float = 0.0      # Maksimum kayıp (drawdown, yüzde puan)
    max_consecutive_losses: int = 0    # Ardışık zarar sayısı (en kötü seri)

    @property
    def summary_text(self) -> str:
        if self.total_trades == 0:
            return f"{self.symbol}: Sinyal yok."
        return (
            f"{self.symbol} | İşlem: {self.total_trades} | "
            f"Kazanç: %{self.win_rate_pct:.1f} | "
            f"Toplam P/L: %{self.total_pnl_pct:.2f} | "
            f"Ort. işlem P/L: %{self.avg_pnl_per_trade:.2f} | "
            f"Max DD: %{self.max_drawdown_pct:.1f} | "
            f"Ardışık zarar: {self.max_consecutive_losses}"
        )


def _entry_high_confirmation(
    df_4h: pd.DataFrame,
    signal_bar: int,
    signal_bar_high: float,
) -> tuple[Optional[int], Optional[float]]:
    """
    Sinyal barının high seviyesinin sonraki 3 bar içinde yukarı kırılıp kırılmadığını kontrol eder.
    Returns: (enter_at_bar, entry_price) — kırılım varsa giriş barı ve o barın kapanışı; yoksa (None, None).
    """
    for j in range(1, ENTRY_HIGH_CONFIRM_BARS + 1):
        idx = signal_bar + j
        if idx >= len(df_4h):
            break
        row = df_4h.iloc[idx]
        high = row.get("high") or row.get("High")
        close = row.get("close") or row.get("Close")
        if high is None or pd.isna(high):
            continue
        if float(high) >= signal_bar_high:
            entry_price = float(close) if close is not None and not pd.isna(close) else signal_bar_high
            return (idx, entry_price)
    return (None, None)


def run_backtest_symbol(
    symbol: str,
    df_1d: pd.DataFrame,
    df_4h: pd.DataFrame,
    index_df_1d: Optional[pd.DataFrame] = None,
) -> BacktestResult:
    """
    Tek sembol için backtest: sinyal + giriş onayı (sinyal bar high kırılımı, 3 bar içinde),
    trailing stop / RSI exit / min %10 kâr / time_exit (30 bar) uygular. Hacim devamlılığı kaldırıldı.
    """
    from src.analysis.indicators import normalize_column_names, add_all_indicators

    empty_result = BacktestResult(
        symbol=symbol, total_trades=0, winning_trades=0, losing_trades=0,
        win_rate_pct=0.0, total_pnl=0.0, total_pnl_pct=0.0, avg_pnl_per_trade=0.0,
    )
    if df_4h is None or df_4h.empty or df_1d is None or df_1d.empty:
        return empty_result

    df_4h = normalize_column_names(df_4h.copy())
    df_1d = normalize_column_names(df_1d.copy())
    if "close" not in df_4h.columns and "Close" not in df_4h.columns:
        return empty_result

    df_4h = add_all_indicators(df_4h)
    df_1d = add_all_indicators(df_1d)

    trades: list[Trade] = []
    i = 2
    while i < len(df_4h):
        signal = _get_signal_at_bar(df_1d, df_4h, symbol, i, index_df_1d=index_df_1d)
        if signal is None or signal.entry is None or signal.stop_loss is None or signal.tp1 is None or signal.tp2 is None:
            i += 1
            continue

        # Giriş onayı: sinyal barının high'ı sonraki 3 bar içinde yukarı kırılmalı
        signal_high = getattr(signal, "signal_bar_high", None)
        if signal_high is None:
            row_i = df_4h.iloc[i]
            signal_high = row_i.get("high") or row_i.get("High")
            if signal_high is not None and not pd.isna(signal_high):
                signal_high = float(signal_high)
        if signal_high is None:
            i += 1
            continue
        enter_at_bar, entry_price = _entry_high_confirmation(df_4h, i, float(signal_high))
        if enter_at_bar is None or entry_price is None:
            i += 1
            continue

        market_risk = getattr(signal, "market_risk", True)
        atr_val = getattr(signal, "atr", None)
        exit_bar, exit_price, reason = simulate_exit_advanced(
            df_4h, enter_at_bar + 1, entry_price, signal.stop_loss, signal.tp1, signal.tp2, market_risk,
            atr=atr_val,
        )
        pnl = exit_price - entry_price
        pnl_pct = (pnl / entry_price) * 100.0
        entry_ts = df_4h.index[enter_at_bar] if enter_at_bar < len(df_4h) else None
        exit_ts = df_4h.index[exit_bar] if exit_bar < len(df_4h) else None
        trades.append(
            Trade(
                symbol=symbol,
                entry_bar=enter_at_bar,
                exit_bar=exit_bar,
                entry_price=entry_price,
                exit_price=exit_price,
                exit_reason=reason,
                pnl=round(pnl, 4),
                pnl_pct=round(pnl_pct, 2),
                entry_time=entry_ts,
                exit_time=exit_ts,
            )
        )
        i = exit_bar + 1

    if not trades:
        return empty_result

    total = len(trades)
    winning = sum(1 for t in trades if t.pnl > 0)
    losing = sum(1 for t in trades if t.pnl <= 0)
    total_pnl = sum(t.pnl for t in trades)
    total_pnl_pct = sum(t.pnl_pct for t in trades)
    avg_pnl = total_pnl_pct / total
    win_rate = (winning / total * 100.0) if total else 0.0
    pnl_pcts = [t.pnl_pct for t in trades]
    max_dd = _max_drawdown_pct(pnl_pcts)
    max_consec = _max_consecutive_losses(trades)

    return BacktestResult(
        symbol=symbol,
        total_trades=total,
        winning_trades=winning,
        losing_trades=losing,
        win_rate_pct=round(win_rate, 1),
        total_pnl=round(total_pnl, 4),
        total_pnl_pct=round(total_pnl_pct, 2),
        avg_pnl_per_trade=round(avg_pnl, 2),
        trades=trades,
        max_drawdown_pct=max_dd,
        max_consecutive_losses=max_consec,
    )


MAX_FETCH_RETRIES = 3
RETRY_DELAY_SEC = 2.0


def _fetch_ohlc_with_retry(
    symbol: str,
    exchange: str,
    n_bars: int,
    interval_4h: bool = True,
    interval_daily: bool = True,
):
    """get_ohlc_bist'i en fazla MAX_FETCH_RETRIES (3) kez dener. İstenen timeframe'lerin hepsi doluysa döner."""
    import time
    from src.scrapers.tv_scraper import get_ohlc_bist

    last_data = None
    for attempt in range(1, MAX_FETCH_RETRIES + 1):
        try:
            data = get_ohlc_bist(
                symbol, exchange,
                interval_4h=interval_4h,
                interval_daily=interval_daily,
                n_bars=n_bars,
            )
            last_data = data
            df_4h = data.get("4h") if data else None
            df_1d = data.get("1d") if data else None
            ok_4h = df_4h is not None and not df_4h.empty
            ok_1d = df_1d is not None and not df_1d.empty
            # İstenen her iki seri de doluysa başarı
            if interval_4h and interval_daily:
                if ok_4h and ok_1d:
                    return data
            elif interval_4h:
                if ok_4h:
                    return data
            elif interval_daily:
                if ok_1d:
                    return data
        except Exception:
            pass
        if attempt < MAX_FETCH_RETRIES:
            time.sleep(RETRY_DELAY_SEC)
    return last_data


def run_backtest_all(
    symbol_list_path: Optional[Path] = None,
    n_bars: int = 1000,
) -> list[BacktestResult]:
    """
    Hisse listesindeki tüm semboller için backtest çalıştırır.
    XU100 endeks verisi bir kez çekilir; piyasa riski ve RS her bar tarihi için o tarihteki endeksle hesaplanır.
    Veri çekme başarısız olursa en fazla 3 kez tekrar denener.
    """
    import time
    from src.scrapers.tv_scraper import load_bist_symbols

    path = symbol_list_path or (Path(__file__).resolve().parent.parent / "data" / "BIST100.txt")
    symbols = load_bist_symbols(path)
    total_syms = len(symbols)
    print(f"Backtest: İstenen bar sayısı = {n_bars} | Sembol sayısı = {total_syms}")

    # Endeks verisi (en fazla 3 deneme _fetch_ohlc_with_retry içinde)
    index_df_1d: Optional[pd.DataFrame] = None
    try:
        print(f"Endeks (XU100) verisi çekiliyor (1d, {n_bars} bar)...", end=" ", flush=True)
        index_data = _fetch_ohlc_with_retry(
            INDEX_SYMBOL, INDEX_EXCHANGE,
            n_bars=n_bars,
            interval_4h=False,
            interval_daily=True,
        )
        index_df_1d = index_data.get("1d") if index_data else None
        if index_df_1d is not None and not index_df_1d.empty:
            from src.analysis.indicators import normalize_column_names
            index_df_1d = normalize_column_names(index_df_1d.copy())
            n_idx = len(index_df_1d)
            print(f"alındı: {n_idx} bar.")
        else:
            print("alınamadı (boş).")
    except Exception as e:
        print(f"hata: {e}")
    if index_df_1d is None or index_df_1d.empty:
        index_df_1d = None

    results = []
    no_data_count = 0
    for i, (sym, exchange) in enumerate(symbols, 1):
        try:
            data = _fetch_ohlc_with_retry(sym, exchange, n_bars=n_bars)
            df_4h = data.get("4h")
            df_1d = data.get("1d")
            n_4h = len(df_4h) if df_4h is not None and not df_4h.empty else 0
            n_1d = len(df_1d) if df_1d is not None and not df_1d.empty else 0
            if df_4h is None or df_4h.empty or df_1d is None or df_1d.empty:
                print(f"  [{i}/{total_syms}] {sym}: veri alınamadı (4h={n_4h}, 1d={n_1d})")
                no_data_count += 1
                time.sleep(0.3)
                continue
            print(f"  [{i}/{total_syms}] {sym}: 4h={n_4h} bar, 1d={n_1d} bar (istek: {n_bars}) — backtest çalıştırılıyor")
            res = run_backtest_symbol(sym, df_1d, df_4h, index_df_1d=index_df_1d)
            results.append(res)
            time.sleep(0.3)
        except Exception as e:
            print(f"  [{i}/{total_syms}] {sym}: hata — {e}")
            no_data_count += 1
            time.sleep(0.3)
            continue

    print(f"\nVeri özeti: {len(results)} sembole veri alındı, {no_data_count} sembole veri alınamadı.")
    return results


def print_backtest_report(results: list[BacktestResult], top_n: int = 20) -> None:
    """Backtest sonuçlarını konsola yazdırır (risk metrikleri: max drawdown, ardışık zarar dahil)."""
    if not results:
        print("Backtest: Hiç sonuç yok.")
        return
    total_trades = sum(r.total_trades for r in results)
    total_pnl_pct = sum(r.total_pnl_pct for r in results)
    with_signals = [r for r in results if r.total_trades > 0]
    max_dd_overall = max((r.max_drawdown_pct for r in with_signals), default=0.0)
    max_consec_overall = max((r.max_consecutive_losses for r in with_signals), default=0)
    print("=" * 70)
    print("BACKTEST RAPORU — Momentum & Trend (Squeeze veya RS+Hacim, EMA, Volume)")
    print("=" * 70)
    print(f"Toplam sembol: {len(results)} | Sinyal veren: {len(with_signals)}")
    print(f"Toplam işlem: {total_trades} | Toplam P/L (basit toplam): %{total_pnl_pct:.2f}")
    print(f"Risk: Maks. drawdown (sembol bazında): %{max_dd_overall:.1f} | En uzun ardışık zarar: {max_consec_overall}")
    print()
    with_signals.sort(key=lambda x: x.total_pnl_pct, reverse=True)
    for r in with_signals[:top_n]:
        print(r.summary_text)
    print("=" * 70)
