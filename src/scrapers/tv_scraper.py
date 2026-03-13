"""
TradingView veri çekici - tvdatafeed kullanarak BIST hisseleri için OHLC verisi.
4 saatlik ve günlük timeframe desteklenir.
Connection timeout'ta 5 sn bekleyip 3 kez tekrarlar; başarısız sembol atlanıp loglanır.
"""
import time
from pathlib import Path
from typing import Optional

import pandas as pd

from src.config import settings

try:
    from tvDatafeed import TvDatafeed
    try:
        from tvDatafeed import Interval
        INTERVAL_4H = Interval.in_4_hour
        INTERVAL_DAILY = Interval.in_daily
    except ImportError:
        INTERVAL_4H = 240
        INTERVAL_DAILY = "D"
except ImportError:
    TvDatafeed = None
    INTERVAL_4H = 240
    INTERVAL_DAILY = "D"


def _create_tv_instance() -> "TvDatafeed":
    """TradingView client: .env'de kullanıcı/şifre varsa giriş dener; başarısızsa girişsiz devam eder."""
    from src.config import settings
    username = (settings.TRADINGVIEW_USERNAME or "").strip()
    password = (settings.TRADINGVIEW_PASSWORD or "").strip()
    if username and password:
        try:
            return TvDatafeed(username=username, password=password)
        except Exception:
            pass
        import warnings
        warnings.warn(
            "TradingView girişi başarısız; girişsiz (misafir) modda devam ediliyor. "
            "Kullanıcı adı/şifre veya 2FA kontrol edin; istemezseniz .env'den TRADINGVIEW_* satırlarını kaldırın.",
            UserWarning,
            stacklevel=2,
        )
    return TvDatafeed()


# Veri çekme: timeout'ta tekrar deneme
FETCH_MAX_RETRIES = 3
FETCH_RETRY_DELAY_SEC = 5.0
FETCH_DELAY_AFTER_SYMBOL_SEC = 1.0

LOG_DIR = getattr(settings, "project_root", Path(__file__).resolve().parent.parent.parent) / "data" / "logs"
DATA_FAILURES_LOG = LOG_DIR / "data_failures.log"


def _log_data_failure(symbol: str, reason: str = "veri çekilemedi") -> None:
    """Sembol veri hatasını konsola ve data/logs/data_failures.log dosyasına yazar."""
    msg = f"⚠️ {symbol} için {reason}, bir sonraki taramada denenecek"
    print(msg)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        with open(DATA_FAILURES_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} | {symbol} | {reason}\n")
    except Exception:
        pass


def _is_timeout_error(exc: BaseException) -> bool:
    """Exception mesajında timeout/connection timeout geçiyor mu?"""
    s = str(exc).lower()
    return "timeout" in s or "connection timed out" in s or "timed out" in s


def _get_hist_with_retry(
    tv: "TvDatafeed",
    symbol: str,
    exchange: str,
    interval,
    n_bars: int,
) -> Optional[pd.DataFrame]:
    """
    get_hist çağrısını yapar; Connection timeout gelirse 5 sn bekleyip en fazla 3 kez dener.
    Başarısızsa None döner.
    """
    last_exc = None
    for attempt in range(1, FETCH_MAX_RETRIES + 1):
        try:
            df = tv.get_hist(symbol=symbol, exchange=exchange, interval=interval, n_bars=n_bars)
            if df is not None and not df.empty:
                return df
            return None
        except Exception as e:
            last_exc = e
            if _is_timeout_error(e) and attempt < FETCH_MAX_RETRIES:
                time.sleep(FETCH_RETRY_DELAY_SEC)
                continue
            return None
    return None


# Varsayılan hisse listesi yolu (proje köküne göre)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SYMBOL_LIST = PROJECT_ROOT / "data" / "BIST100.txt"


def load_bist_symbols(filepath: Optional[Path] = None) -> list[tuple[str, str]]:
    """
    BIST100 hisse listesini dosyadan okur.
    Format: BIST:SYMBOL veya sadece SYMBOL. # ile başlayan satırlar atlanır.
    """
    path = filepath or DEFAULT_SYMBOL_LIST
    symbols = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # BIST:THYAO -> exchange BIST, symbol THYAO
            if ":" in line:
                _, sym = line.split(":", 1)
                symbols.append((sym.strip(), "BIST"))
            else:
                symbols.append((line.strip(), "BIST"))
    return symbols


def get_ohlc_bist(
    symbol: str,
    exchange: str = "BIST",
    interval_4h: bool = True,
    interval_daily: bool = True,
    n_bars: int = 500,
    tv: Optional["TvDatafeed"] = None,
) -> dict[str, pd.DataFrame]:
    """
    Tek bir BIST sembolü için 4 saatlik ve/veya günlük OHLC verisini getirir.

    Args:
        symbol: Hisse sembolü (örn. THYAO)
        exchange: Borsa (varsayılan BIST)
        interval_4h: 4 saatlik veri isteniyor mu
        interval_daily: Günlük veri isteniyor mu
        n_bars: Çekilecek mum sayısı
        tv: Opsiyonel TvDatafeed instance (birden fazla çağrıda tek instance kullanmak için)

    Returns:
        {"4h": DataFrame, "1d": DataFrame} - İstenen timeframe'ler. Boş DataFrame hata durumunda.
    """
    if TvDatafeed is None:
        raise ImportError("tradingview-datafeed yüklü değil. pip install tradingview-datafeed çalıştırın.")

    tv = tv or _create_tv_instance()
    result = {}
    failed = False

    if interval_4h:
        df_4h = _get_hist_with_retry(tv, symbol, exchange, INTERVAL_4H, n_bars)
        if df_4h is not None and not df_4h.empty:
            result["4h"] = df_4h.copy()
        else:
            result["4h"] = pd.DataFrame()
            failed = True

    if interval_daily:
        df_d = _get_hist_with_retry(tv, symbol, exchange, INTERVAL_DAILY, n_bars)
        if df_d is not None and not df_d.empty:
            result["1d"] = df_d.copy()
        else:
            result["1d"] = pd.DataFrame()
            failed = True

    if failed:
        _log_data_failure(symbol)

    time.sleep(FETCH_DELAY_AFTER_SYMBOL_SEC)
    return result


def get_ohlc_all_bist(
    symbol_list_path: Optional[Path] = None,
    n_bars: int = 500,
) -> dict[str, dict[str, pd.DataFrame]]:
    """
    BIST100 listesindeki tüm hisseler için 4 saatlik ve günlük OHLC verisini getirir.

    Returns:
        { "THYAO": {"4h": DataFrame, "1d": DataFrame }, ... }
    """
    symbols = load_bist_symbols(symbol_list_path)
    if not symbols:
        return {}

    tv = _create_tv_instance() if TvDatafeed else None
    all_data = {}

    for sym, exchange in symbols:
        key = sym
        all_data[key] = get_ohlc_bist(
            symbol=sym,
            exchange=exchange,
            interval_4h=True,
            interval_daily=True,
            n_bars=n_bars,
            tv=tv,
        )

    return all_data


if __name__ == "__main__":
    # Test: tek sembol
    symbols = load_bist_symbols()
    print(f"Yüklü sembol sayısı: {len(symbols)}")
    if symbols:
        sym, ex = symbols[0]
        data = get_ohlc_bist(sym, ex, n_bars=100)
        for tf, df in data.items():
            print(f"{tf}: {len(df)} satır" if not df.empty else f"{tf}: (boş)")
