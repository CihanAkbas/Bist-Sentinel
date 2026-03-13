"""
Merkezi yapılandırma: .env dosyasından tüm parametreler okunur.
Kullanım: from src.config import settings
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Union

from dotenv import load_dotenv

# Proje kökünde .env yükle (main.py veya src'den çalıştırılsa da aynı kök)
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(key: str, default: str = "", cast=str) -> Union[str, int, float]:
    val = os.getenv(key, default)
    if cast is int:
        return int(val) if str(val).strip().isdigit() else int(default) if default else 0
    if cast is float:
        try:
            return float(val)
        except (ValueError, TypeError):
            return float(default) if default else 0.0
    return str(val).strip()


class Settings:
    """
    Tüm yapılandırma değerleri. .env'den okunur; yoksa varsayılanlar kullanılır.
    """

    # --- API & ID ---
    TELEGRAM_BOT_TOKEN: str = _env("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = _env("TELEGRAM_CHAT_ID", "")
    GEMINI_API_KEY: str = _env("GEMINI_API_KEY", "")  # KAP haber süzgeci (kritik mi, puan 1-10)
    # TradingView (opsiyonel): Giriş yapınca veri erişimi daha stabil; Connection timed out azalır
    TRADINGVIEW_USERNAME: str = _env("TRADINGVIEW_USERNAME", "")
    TRADINGVIEW_PASSWORD: str = _env("TRADINGVIEW_PASSWORD", "")

    # --- Strateji eşikleri ---
    EMA_PERIOD: int = _env("EMA_PERIOD", "200", int)
    BB_PERIOD: int = _env("BB_PERIOD", "20", int)
    BB_STD: float = _env("BB_STD", "2.0", float)
    KC_PERIOD: int = _env("KC_PERIOD", "20", int)
    KC_MULT: float = _env("KC_MULT", "1.5", float)
    RSI_PERIOD: int = _env("RSI_PERIOD", "14", int)
    RSI_THRESHOLD: int = _env("RSI_THRESHOLD", "50", int)

    # --- Hacim & momentum ---
    VOLUME_MULTIPLIER: float = _env("VOLUME_MULTIPLIER", "1.1", float)  # Ortalama üstü hacim yeterli (squeeze + momentum)
    VOLUME_MA_PERIOD: int = _env("VOLUME_MA_PERIOD", "20", int)  # Ortalama hacim periyodu
    MOMENTUM_PERIOD: int = _env("MOMENTUM_PERIOD", "9", int)  # Daha hızlı tepki (momentum bypass)
    # Göreceli hacim (RVOL): aynı saat dilimi, son N gün ortalaması
    RVOL_SAME_SLOT_DAYS: int = _env("RVOL_SAME_SLOT_DAYS", "10", int)

    # --- Trade plan (ATR tabanlı giriş/çıkış) ---
    ATR_PERIOD: int = _env("ATR_PERIOD", "14", int)
    STOP_ATR_MULT: float = _env("STOP_ATR_MULT", "1.5", float)   # Stop-Loss = Entry - (mult * ATR)
    TP1_ATR_MULT: float = _env("TP1_ATR_MULT", "1.5", float)     # TP1 = Entry + (mult * ATR), R:R 1:1
    TP2_ATR_MULT: float = _env("TP2_ATR_MULT", "8.0", float)     # TP2 = Entry + (mult * ATR); 8x ≈ %15-20 kâr (Trend Canavarı)

    # --- Piyasa riski (endeks EMA20 altında iken min RSI eşiği) ---
    MARKET_RISK_RSI_MIN: int = _env("MARKET_RISK_RSI_MIN", "65", int)  # Sadece bu RSI üstü sinyaller gönderilir

    # --- Risk yönetimi (önerilen lot hesabı) ---
    TRADING_CAPITAL: float = _env("TRADING_CAPITAL", "100000", float)   # Toplam kasa (TL); önerilen pozisyon hesabı
    RISK_PCT: float = _env("RISK_PCT", "2", float)                     # İşlem başına kasa riski (%); Lot = (Kasa * RISK_PCT%) / (Giriş - Stop)

    # --- Bot ayarları ---
    SCAN_INTERVAL_MINUTES: int = _env("SCAN_INTERVAL_MINUTES", "240", int)  # 4 saat
    REQUEST_DELAY: float = _env("REQUEST_DELAY", "0.5", float)  # Hisse başına bekleme (saniye)
    # Haber bayatlaması: Bu dakikadan eski haberlerde Hybrid-News override kapatılır (tepeden giriş riski)
    NEWS_OVERRIDE_MAX_AGE_MINUTES: int = _env("NEWS_OVERRIDE_MAX_AGE_MINUTES", "90", int)

    # --- Proje yolları (isteğe bağlı override) ---
    @property
    def project_root(self) -> Path:
        return ROOT


# Tekil instance; tüm projede "from src.config import settings" ile kullan
settings = Settings()
