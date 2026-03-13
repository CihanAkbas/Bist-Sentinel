"""
BIST100 Trading Bot - Ana çalışma döngüsü.
BIST100 hisselerini tarar, Triple Confirmation ile sinyal üretir, Telegram'a grafikle gönderir.
Hata yönetimi: Veri çekilemeyen hisse atlanır; rate limiting ile IP/engel riski azaltılır.
Her döngü özeti data/logs klasörüne yazılır.
"""
import sys
import time
from datetime import datetime
from pathlib import Path

# Proje kökünü path'e ekle
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.config import settings
from src.scrapers.tv_scraper import load_bist_symbols, get_ohlc_bist
from src.scrapers.kap_listener import get_sentiment_for_symbol
from src.analysis.squeeze_engine import TripleConfirmationEngine, Signal
from src.analysis.chart import build_squeeze_chart
from src.notifications.telegram_bot import (
    send_signal_from_dataclass,
    send_telegram_message,
    send_kap_triggered_opportunity,
    send_urgent_negative_news_alert,
    run_callback_polling,
)
from src.logging_utils import ScanSummary, write_scan_log
from src.pending_signals import add_pending_signal, check_pending_signal_cancellations
from src.position_manager import check_active_trades, add_pending_trade_approval
from src.backtest import run_backtest_all, print_backtest_report
from src.analysis.market_context import get_market_context, compute_relative_strength
from src.analysis.sector_correlation import get_sector_context
from src.bist_schedule import is_bist_open, next_open_in_seconds
from src.weekly_report import send_weekly_report, should_send_weekly_report_now, mark_weekly_report_sent


def _enrich_signal_with_kap(signal: Signal) -> Signal:
    """Sinyale KAP anahtar kelime sentiment'ı ekler; Trade Plan alanları korunur."""
    try:
        kap = get_sentiment_for_symbol(signal.symbol)
        return Signal(
            symbol=signal.symbol,
            timeframe=signal.timeframe,
            strategy=signal.strategy,
            price=signal.price,
            trend=signal.trend,
            kap_status=kap.capitalize(),
            rsi=signal.rsi,
            volume_ratio=signal.volume_ratio,
            entry=signal.entry,
            stop_loss=signal.stop_loss,
            tp1=signal.tp1,
            tp2=signal.tp2,
            rr_ratio=signal.rr_ratio,
            atr=signal.atr,
            market_risk=signal.market_risk,
            relative_strength=signal.relative_strength,
            fvg_support_below=signal.fvg_support_below,
            rvol_ratio=signal.rvol_ratio,
            signal_bar_high=getattr(signal, "signal_bar_high", None),
            recommended_lot=getattr(signal, "recommended_lot", None),
            momentum_bypass=getattr(signal, "momentum_bypass", False),
        )
    except Exception:
        return signal


def run_cycle(
    symbol_list_path: Path | None = None,
    send_telegram: bool = True,
    delay_seconds: float | None = None,
    use_kap_sentiment: bool = True,
    with_chart: bool = True,
    write_log: bool = True,
) -> list[Signal]:
    """
    Tek tarama döngüsü: tüm semboller için veri çek, analiz et, sinyal varsa grafikle bildir.
    Veri çekilemeyen veya hata veren hisse atlanır; döngü kesilmez.
    Her döngü sonunda data/logs'a özet yazılır.
    """
    if delay_seconds is None:
        delay_seconds = settings.REQUEST_DELAY
    symbols = load_bist_symbols(symbol_list_path)
    if not symbols:
        print("BIST100 listesi boş veya bulunamadı.")
        return []

    # Bekleyen sinyalleri kontrol et: 3 bar geçtiyse ve high kırılımı yoksa iptal mesajı
    if send_telegram:
        cancelled = check_pending_signal_cancellations(send_telegram=True, delay_seconds=delay_seconds)
        if cancelled:
            print(f"  {cancelled} sinyal iptal (momentum onayı gelmedi).")

    # Açık pozisyonları kontrol et: Hantallık / TP1 / Stop bildirimleri
    closed = check_active_trades(send_telegram=send_telegram, delay_seconds=delay_seconds)
    if closed:
        print(f"  {closed} pozisyon kapatıldı veya Hantallık/TP1/Stop bildirimi gönderildi.")

    signals: list[Signal] = []
    total = len(symbols)
    no_data = 0
    errors = 0
    scanned = 0
    ema_below = 0
    squeeze_on = 0
    started_at = datetime.now().isoformat(timespec="seconds")
    # Eleme raporu: Hangi filtreye kaç hisse takıldı?
    elimination_divergence = 0
    elimination_rvol = 0
    elimination_rs_weak = 0
    elimination_market_risk_rsi = 0

    # Piyasa bağlamı (Market Regime): XU100 EMA50 altında veya RSI < 40 ise riskli
    context = get_market_context()
    if context.market_risk:
        print("⚠️ Piyasa riskli (XU100 < EMA50 veya RSI < 40); sinyaller yarı lot ile, sadece RSI >", settings.MARKET_RISK_RSI_MIN, "gönderilecek.")

    for i, (sym, exchange) in enumerate(symbols, 1):
        print(f"[{i}/{total}] {sym} ...", end=" ", flush=True)
        try:
            data = get_ohlc_bist(sym, exchange, n_bars=500)
            df_4h = data.get("4h")
            df_1d = data.get("1d")
            if df_4h is None or df_4h.empty or df_1d is None or df_1d.empty:
                print("veri yok")
                no_data += 1
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                continue

            engine = TripleConfirmationEngine(df_1d, df_4h, sym)
            scanned += 1
            if not engine.macro_filter_ok():
                ema_below += 1
            if engine.squeeze_on_current_bar():
                squeeze_on += 1

            result = engine.check_signal()
            # Engine bazen eleme nedenini döner: ("divergence", None), ("rvol", None), ("exhaustion_volume", None)
            if isinstance(result, tuple):
                reason = result[0]
                if reason == "divergence":
                    elimination_divergence += 1
                    print("uyumsuzluk")
                elif reason == "rvol":
                    elimination_rvol += 1
                    print("rvol")
                elif reason == "exhaustion_volume":
                    print("⚠️ Aşırı Hacim (Tükeniş Riski)")
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
                continue
            signal = result
            if signal:
                signal.market_risk = context.market_risk
                # Piyasa riskliyse önerilen pozisyon büyüklüğünü yarıya indir
                if context.market_risk:
                    lot = getattr(signal, "recommended_lot", None)
                    if lot is not None and lot > 0:
                        signal.recommended_lot = max(1, lot // 2)
                signal.relative_strength = compute_relative_strength(engine.daily, context.index_df) if context.index_df is not None else None
                # Momentum bypass: Squeeze olmadan giren sinyal sadece RS > 1.2 ise kabul
                if getattr(signal, "momentum_bypass", False):
                    if signal.relative_strength is None or signal.relative_strength <= 1.2:
                        print("atlandı (momentum bypass, RS<=1.2)")
                        if delay_seconds > 0:
                            time.sleep(delay_seconds)
                        continue
                # RS Booster: RS > 1.2 ise tüm RSI ve endeks filtrelerini kapat
                leader_stock = signal.relative_strength is not None and signal.relative_strength > 1.2
                if not leader_stock:
                    # Market context: Endeks EMA20 altındayken RS > 1.2 ise izin ver; değilse RSI eşiği
                    if context.market_risk:
                        strong_rs = signal.relative_strength is not None and signal.relative_strength > 1.2
                        if not strong_rs and (signal.rsi is None or signal.rsi <= settings.MARKET_RISK_RSI_MIN):
                            elimination_market_risk_rsi += 1
                            print("atlandı (riskli piyasa, RSI<=%d ve RS<=1.2)" % settings.MARKET_RISK_RSI_MIN)
                            if delay_seconds > 0:
                                time.sleep(delay_seconds)
                            continue
                    # RS filtresi: Endeksten zayıf (RS < 1) sinyalleri ele
                    if signal.relative_strength is not None and signal.relative_strength < 1:
                        elimination_rs_weak += 1
                        print("atlandı (RS<1)")
                        if delay_seconds > 0:
                            time.sleep(delay_seconds)
                        continue
                print("SİNYAL")
                if use_kap_sentiment:
                    signal = _enrich_signal_with_kap(signal)
                signals.append(signal)

                if send_telegram:
                    chart_path = None
                    if with_chart:
                        chart_path = build_squeeze_chart(
                            engine.data_4h, sym, bars=100,
                            signal_entry=getattr(signal, "entry", None),
                        )
                    # Sektör korelasyonu: benzer hisselerin teknik durumu
                    sector_note = get_sector_context(
                        signal.symbol, exchange, delay_seconds=delay_seconds,
                        symbol_list_path=symbol_list_path,
                    )
                    if sector_note:
                        signal.sector_note = sector_note
                    index_warning = context.index_volume_warning if getattr(context, "index_volume_below_avg", False) else None
                    ok = send_signal_from_dataclass(
                        signal,
                        image_path=chart_path,
                        sector_note=sector_note or getattr(signal, "sector_note", None),
                        index_volume_warning=index_warning,
                    )
                    print(f"  Telegram: {'gönderildi' if ok else 'gönderilemedi (token/chat_id?)'}")
                    if ok:
                        # High breakout 3 bar içinde gelmezse iptal mesajı için bekleyen listesine ekle
                        if getattr(signal, "signal_bar_high", None) is not None:
                            add_pending_signal(
                                sym, exchange,
                                signal.signal_bar_high,
                                engine.data_4h.index[-1],
                            )
                        # Onay bekleyen listesine ekle; sadece Telegram'dan 'İşlemi Onayla' basılınca active_trades'e alınır
                        add_pending_trade_approval(signal, engine.data_4h.index[-1], exchange)
            else:
                print("ok")
        except Exception as e:
            errors += 1
            print(f"hata: {e}")

        if delay_seconds > 0:
            time.sleep(delay_seconds)

    if errors:
        print(f"\nUyarı: {errors} hissede hata oluştu (atlandı).")

    summary = ScanSummary(
        timestamp=started_at,
        total_symbols=total,
        scanned=scanned,
        no_data=no_data,
        errors=errors,
        ema_below=ema_below,
        squeeze_on=squeeze_on,
        signals=len(signals),
        signal_symbols=[s.symbol for s in signals],
        elimination_divergence=elimination_divergence,
        elimination_rvol=elimination_rvol,
        elimination_rs_weak=elimination_rs_weak,
        elimination_market_risk_rsi=elimination_market_risk_rsi,
    )
    if write_log:
        log_path = write_scan_log(summary)
        print(f"Özet log: {log_path}")

    if send_telegram:
        _send_scan_summary_telegram(summary)

    return signals


def run_flash_scan(
    symbol: str,
    exchange: str,
    news_result=None,
    send_telegram: bool = True,
    delay_seconds: float | None = None,
    news_published_at=None,
) -> Signal | None:
    """
    Haber gelen sembol için anlık özel tarama: en güncel 4H ve günlük veriyi çeker,
    Squeeze/Momentum stratejilerine uygunluk kontrol eder. RS > 1, RSI < 70 gibi teknik
    filtreleri geçerse Telegram'a 'KAP TETİKLEMELİ FIRSAT' mesajı (Gemini yorumu + grafik) gönderir.
    Haber bayatlaması: news_published_at, NEWS_OVERRIDE_MAX_AGE_MINUTES'tan eskiyse Hybrid-News
    override devre dışı bırakılır (tepeden giriş riskini azaltmak için).
    """
    if delay_seconds is None:
        delay_seconds = settings.REQUEST_DELAY
    try:
        data = get_ohlc_bist(symbol, exchange, n_bars=500)
    except Exception:
        return None
    df_4h = data.get("4h")
    df_1d = data.get("1d")
    if df_4h is None or df_4h.empty or df_1d is None or df_1d.empty:
        return None
    engine = TripleConfirmationEngine(df_1d, df_4h, symbol)
    news_score = getattr(news_result, "score", None) if news_result else None
    # Haber bayatlaması: X dakikadan eski haberde override kullanma (normal teknik şartlar geçerli)
    if news_score is not None and news_published_at is not None:
        max_age_sec = settings.NEWS_OVERRIDE_MAX_AGE_MINUTES * 60
        try:
            if (datetime.now() - news_published_at).total_seconds() > max_age_sec:
                news_score = None
        except (TypeError, ValueError):
            pass
    result = engine.check_signal(news_override_score=news_score)
    if isinstance(result, tuple):
        return None
    signal = result
    if not signal:
        return None
    # Teknik filtreler: RS > 1 (veya en az zayıf değil), RSI < 70
    if signal.rsi is not None and signal.rsi >= 70:
        return None
    context = get_market_context()
    signal.market_risk = context.market_risk
    signal.relative_strength = (
        compute_relative_strength(engine.daily, context.index_df) if context.index_df is not None else None
    )
    if signal.relative_strength is not None and signal.relative_strength < 1:
        return None
    if delay_seconds and delay_seconds > 0:
        time.sleep(delay_seconds)
    if send_telegram:
        chart_path = build_squeeze_chart(
            engine.data_4h, symbol, bars=100, signal_entry=getattr(signal, "entry", None)
        )
        news_comment = getattr(news_result, "comment", "") if news_result else ""
        news_score = getattr(news_result, "score", None) if news_result else None
        news_sentiment = getattr(news_result, "sentiment", None) if news_result else None
        send_kap_triggered_opportunity(
            signal, news_comment, image_path=chart_path, news_score=news_score, news_sentiment=news_sentiment
        )
    return signal


def run_single_symbol_scan(
    ticker: str,
    news_result=None,
    symbol_list_path: Path | None = None,
    send_telegram: bool = True,
    news_published_at=None,
) -> Signal | None:
    """
    Tek bir hisse için tarama (KAP tetiklemesi). BIST100 listesinde varsa run_flash_scan çağrılır.
    news_published_at: Haber yayın zamanı; bayat haberde Hybrid-News override kapatılır.
    """
    symbols = load_bist_symbols(symbol_list_path)
    ticker_upper = ticker.upper().strip()
    exchange = "BIST"
    for sym, ex in symbols:
        if sym.upper() == ticker_upper:
            exchange = ex
            break
    else:
        return None
    return run_flash_scan(
        ticker_upper, exchange, news_result=news_result, send_telegram=send_telegram,
        news_published_at=news_published_at,
    )


def _send_scan_summary_telegram(summary: ScanSummary) -> None:
    """Tarama özetini Telegram'a kısa metin olarak gönderir (Eleme detayları dahil)."""
    parts = [
        "✅ BIST Sentinel taraması tamamlandı.",
        f"📊 Taranan: {summary.scanned}/{summary.total_symbols} hisse",
        f"🎯 Sinyal: {summary.signals}",
    ]
    if summary.signal_symbols:
        parts.append(f"📌 Sinyal verenler: {', '.join(summary.signal_symbols)}")
    if summary.errors:
        parts.append(f"⚠️ Atlanan (hata): {summary.errors}")
    parts.append("")
    parts.append("📊 Eleme Detayları:")
    parts.append(f"  📉 Endeks Altı (EMA200): {summary.ema_below}")
    parts.append(f"  ⚠️ Uyumsuzluk (RSI): {summary.elimination_divergence}")
    parts.append(f"  📊 RVOL (hacim): {summary.elimination_rvol}")
    parts.append(f"  🐌 Zayıf RS (RS<1): {summary.elimination_rs_weak}")
    parts.append(f"  🛑 Piyasa riski + düşük RSI: {summary.elimination_market_risk_rsi}")
    send_telegram_message("\n".join(parts))


def run_scheduler_loop(
    list_path: Path,
    send_telegram: bool,
    delay_seconds: float | None,
    use_kap_sentiment: bool,
    with_chart: bool,
    write_log: bool,
) -> None:
    """
    Sürekli döngü: Sadece BIST açıkken (Pzt–Cuma 09:30–18:00 İstanbul) tarama yapar.
    Hafta sonu ve mesai dışında bekler; açılışa kadar uyur.
    KAP Watchdog: 7/24 KAP bildirimlerini dinler, kritik haber (Gemini puan 7+) gelince
    ilgili hisse için run_single_symbol_scan (flash scan) tetiklenir.
    """
    interval_seconds = settings.SCAN_INTERVAL_MINUTES * 60
    print(f"BIST Sentinel zamanlayıcı başlatıldı. Aralık: {settings.SCAN_INTERVAL_MINUTES} dakika.")
    print("Bot sadece BIST açıkken (Pzt–Cuma 09:30–18:00) çalışır.\n")

    # KAP Watchdog: haber tetiklemeli tarama (arka planda 60 sn'de bir KAP API kontrolü)
    if settings.GEMINI_API_KEY:
        import threading
        from src.notifications.kap_watchdog import run_kap_watchdog_loop

        def on_kap_critical(ticker: str, news_result, title: str, summary: str, published_at=None) -> None:
            print(f"  [KAP] Kritik haber → #{ticker} flash tarama tetikleniyor.")
            run_single_symbol_scan(
                ticker, news_result=news_result, symbol_list_path=list_path, send_telegram=send_telegram,
                news_published_at=published_at,
            )

        def on_urgent_negative(ticker: str, news_result, title: str, summary: str) -> None:
            """Aktif pozisyondaki hisse için kritik negatif haber — sesli Telegram uyarısı."""
            if send_telegram:
                comment = getattr(news_result, "comment", "") or ""
                send_urgent_negative_news_alert(ticker, title, comment)
            print(f"  [KAP] 🚨 Acil negatif haber uyarısı gönderildi: #{ticker}")

        kap_thread = threading.Thread(
            target=run_kap_watchdog_loop,
            args=(on_kap_critical, list_path, 60, 7),
            kwargs={"on_urgent_negative_alert": on_urgent_negative},
            daemon=True,
        )
        kap_thread.start()
        print("KAP Watchdog (haber tetiklemeli tarama + negatif haber uyarısı) başlatıldı.")
    else:
        print("KAP Watchdog atlandı: GEMINI_API_KEY .env'de tanımlı değil.")

    # Bot ilk açıldığında haftalık performans karnesi gönder (Pazartesi 09:00 değilse; yoksa döngüde gönderilir)
    now = datetime.now()
    if not (now.weekday() == 0 and now.hour == 9) and send_telegram:
        send_weekly_report(send_telegram=True)

    while True:
        # Her Pazartesi 09:00'da haftalık rapor
        if send_telegram and should_send_weekly_report_now():
            send_weekly_report(send_telegram=True)
            mark_weekly_report_sent()
        if not is_bist_open():
            wait_sec = next_open_in_seconds()
            if wait_sec > 0:
                wait_min = int(wait_sec / 60)
                print(f"⏸ BIST kapalı. {wait_min} dakika sonra açılışa kadar bekleniyor...")
                time.sleep(min(wait_sec, 300))  # En fazla 5 dk uyu, tekrar kontrol et
                continue
        if send_telegram:
            send_telegram_message("🔍 BIST Sentinel taraması başlıyor...")
        print("BIST100 tarama başlıyor...")
        signals = run_cycle(
            symbol_list_path=list_path,
            send_telegram=send_telegram,
            delay_seconds=delay_seconds,
            use_kap_sentiment=use_kap_sentiment,
            with_chart=with_chart,
            write_log=write_log,
        )
        print(f"\nToplam {len(signals)} sinyal bulundu.")
        print(f"Sonraki tarama {settings.SCAN_INTERVAL_MINUTES} dakika sonra...\n")
        time.sleep(interval_seconds)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="BIST100 Triple Confirmation Bot (BIST Sentinel)")
    parser.add_argument("--once", action="store_true", help="Tek tarama yap ve çık (zamanlayıcı döngüsüne girme)")
    parser.add_argument("--no-telegram", action="store_true", help="Telegram bildirimi gönderme")
    parser.add_argument("--no-chart", action="store_true", help="Telegram mesajına grafik ekleme")
    parser.add_argument("--no-log", action="store_true", help="data/logs özet dosyası yazma")
    parser.add_argument("--list", type=Path, default=None, help="Hisse listesi dosyası (varsayılan: data/BIST100.txt)")
    parser.add_argument("--delay", type=float, default=None, help=f"Hisse başına bekleme (saniye). Varsayılan: .env REQUEST_DELAY ({settings.REQUEST_DELAY})")
    parser.add_argument("--no-kap-sentiment", action="store_true", help="KAP anahtar kelime sentiment kullanma")
    parser.add_argument("--backtest", action="store_true", help="Backtest çalıştır (geçmiş sinyallerle performans simülasyonu)")
    parser.add_argument("--backtest-bars", type=int, default=1000, help="Backtest için çekilecek bar sayısı (varsayılan: 1000)")
    parser.add_argument("--backtest-top", type=int, default=20, help="Raporda gösterilecek en iyi N sembol (varsayılan: 20)")
    args = parser.parse_args()

    list_path = args.list or ROOT / "data" / "BIST100.txt"
    delay_seconds = args.delay if args.delay is not None else settings.REQUEST_DELAY

    if args.backtest:
        print("Backtest başlıyor (geçmiş veri ile sinyal simülasyonu)...")
        results = run_backtest_all(symbol_list_path=list_path, n_bars=args.backtest_bars)
        print_backtest_report(results, top_n=args.backtest_top)
        return 0

    # Telegram açıksa buton tıklamalarını dinleyen polling thread'i başlat
    if not args.no_telegram and settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        import threading
        poller = threading.Thread(
            target=run_callback_polling,
            args=(settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID),
            daemon=True,
        )
        poller.start()
        print("Telegram işlem onay dinleyicisi başlatıldı (İşlemi Onayla butonu).")

    if args.once:
        if not args.no_telegram:
            send_telegram_message("🔍 BIST Sentinel taraması başlıyor...")
        print("BIST100 tarama başlıyor (tek seferlik)...")
        signals = run_cycle(
            symbol_list_path=list_path,
            send_telegram=not args.no_telegram,
            delay_seconds=delay_seconds,
            use_kap_sentiment=not args.no_kap_sentiment,
            with_chart=not args.no_chart,
            write_log=not args.no_log,
        )
        print(f"\nToplam {len(signals)} sinyal bulundu.")
        return 0

    run_scheduler_loop(
        list_path=list_path,
        send_telegram=not args.no_telegram,
        delay_seconds=delay_seconds,
        use_kap_sentiment=not args.no_kap_sentiment,
        with_chart=not args.no_chart,
        write_log=not args.no_log,
    )
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main() or 0)
