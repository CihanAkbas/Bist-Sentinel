"""
Tarama döngüsü özet loglama: data/logs klasörüne her döngünün istatistikleri yazılır.
"""
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from src.config import settings

LOGS_DIR = settings.project_root / "data" / "logs"


@dataclass
class ScanSummary:
    """Tek tarama döngüsünün özeti + Eleme raporu."""
    timestamp: str
    total_symbols: int
    scanned: int          # Veri alınan hisse sayısı
    no_data: int         # Veri alınamayan
    errors: int          # İstisna ile atlanan
    ema_below: int       # EMA200 altında (macro filter eledi)
    squeeze_on: int      # Sıkışma içinde (squeeze on)
    signals: int         # Üretilen sinyal sayısı
    signal_symbols: list[str]  # Sinyal veren semboller
    # Eleme detayları (hangi filtreye kaç hisse takıldı)
    elimination_divergence: int = 0      # RSI uyumsuzluğu
    elimination_rvol: int = 0            # Göreceli hacim (aynı slot ort. altı)
    elimination_rs_weak: int = 0        # RS < 1 (endeksten zayıf)
    elimination_market_risk_rsi: int = 0 # Riskli piyasa + düşük RSI iptali


def write_scan_log(summary: ScanSummary) -> Path:
    """
    Özeti data/logs klasörüne JSON ve okunabilir özet dosyası olarak yazar.

    Returns:
        Yazılan JSON dosyasının path'i.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = LOGS_DIR / f"scan_{ts}.json"
    txt_path = LOGS_DIR / f"scan_{ts}.txt"

    data = asdict(summary)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    lines = [
        f"Tarama Özeti — {summary.timestamp}",
        "=" * 50,
        f"Toplam sembol:     {summary.total_symbols}",
        f"Taranan (veri var): {summary.scanned}",
        f"Veri yok:          {summary.no_data}",
        f"Hata (atlandı):    {summary.errors}",
        f"EMA200 altında:    {summary.ema_below}",
        f"Sıkışmada (squeeze on): {summary.squeeze_on}",
        f"Sinyal sayısı:     {summary.signals}",
        f"Sinyal verenler:   {', '.join(summary.signal_symbols) or '-'}",
        "",
        "Eleme Detayları:",
        f"  RSI Uyumsuzluğu: {summary.elimination_divergence}",
        f"  RVOL (hacim):    {summary.elimination_rvol}",
        f"  Zayıf RS (RS<1): {summary.elimination_rs_weak}",
        f"  Piyasa riski+RSI: {summary.elimination_market_risk_rsi}",
    ]
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return json_path
