# BIST Sentinel — Detaylı Kullanım Rehberi

Borsa İstanbul (BIST100) hisselerini **Triple Confirmation** stratejisiyle tarayan, KAP haberleriyle tetiklenen anlık fırsat taraması ve **aktif pozisyonlar için kritik negatif haber uyarısı** sunan, Telegram entegreli profesyonel sinyal ve zarar kes asistanı.

---

## İçindekiler

1. [Özellikler](#özellikler)
2. [Proje Yapısı](#proje-yapısı)
3. [Strateji: The Triple Confirmation](#strateji-the-triple-confirmation)
4. [KAP ve Haber Sistemi](#kap-ve-haber-sistemi)
5. [Pozisyon Yönetimi](#pozisyon-yönetimi)
6. [Kurulum](#kurulum)
7. [Yapılandırma (.env)](#yapılandırma-env)
8. [Çalıştırma](#çalıştırma)
9. [BIST Mesai ve Zamanlayıcı](#bist-mesai-ve-zamanlayıcı)
10. [Loglama ve Görsel Kanıt](#loglama-ve-görsel-kanıt)
11. [Backtest](#backtest)
12. [Telegram Mesaj Formatları](#telegram-mesaj-formatları)
13. [TradingView ve Veri Kaynağı](#tradingview-ve-veri-kaynağı)
14. [Bağımlılıklar](#bağımlılıklar)

---

## Özellikler

| Özellik | Açıklama |
|--------|----------|
| **Triple Confirmation taraması** | BIST100 hisseleri 4H + günlük veriyle taranır; Squeeze Breakout veya Momentum bypass ile sinyal üretilir. |
| **KAP Watchdog** | 7/24 KAP bildirimleri dinlenir; hisse kodu BIST100 içindeyse Gemini ile haber süzgecinden geçirilir. |
| **Haber tetiklemeli flash scan** | Puan 7+ kritik haber geldiğinde ilgili hisse için anlık 4H/1D tarama yapılır; teknik uygunsa "KAP TETİKLEMELİ FIRSAT" Telegram’a gönderilir. |
| **Negatif haber acil uyarı** | Haber gelen hisse **aktif pozisyondaysa** ve Gemini haberi **Negatif + 7+** puan verirse sesli "ACİL DURUM" mesajı atılır (zarar kes / stop yaklaştır önerisi). |
| **Pozisyon takibi** | Sinyal Telegram’a gidince "İşlemi Onayla" ile `active_trades.json`’a eklenir; Multi-TP: TP1'de stop breakeven, TP2'ye kadar takip; Stop / Hantallık bildirimleri otomatik. |
| **Haftalık rapor** | Her Pazartesi 09:00’da (veya bot ilk açıldığında) haftalık performans karnesi Telegram’a gönderilir. |
| **Sektör korelasyonu** | `data/sector_peers.json` ile sektör arkadaşları kontrol; sinyal mesajında "Sektör Dağılımı" notu. |
| **XU100 hacim uyarısı** | Endeks yükselirken hacim ortalamanın altındaysa "sahte kırılım" uyarısı sinyal mesajına eklenir. |
| **Telegram /bak** | `/bak THYAO` ile 4H grafik, son 3 KAP ve teknik puan (0–10) anında gönderilir. |
| **Backtest** | Geçmiş veriyle aynı sinyal motoru kullanılarak performans simülasyonu. |

---

## Proje Yapısı

```
Bist/
├── data/
│   ├── BIST100.txt              # BIST100 hisse listesi (BIST:SYMBOL veya SYMBOL)
│   ├── sector_peers.json        # Sektör korelasyonu: sembol → { sector, peers }
│   ├── active_trades.json       # Açık pozisyonlar (tp1_hit ile Multi-TP)
│   ├── pending_trade_approvals.json
│   ├── trade_history.json       # Kapanan işlemler (haftalık rapor)
│   ├── charts/                  # Sinyal grafikleri (PNG) — Telegram’a eklenir
│   └── logs/                    # Tarama özetleri (scan_YYYYMMDD_HHMMSS.json / .txt)
├── src/
│   ├── config.py                # Merkezi .env yapılandırması
│   ├── scrapers/
│   │   ├── tv_scraper.py        # TradingView OHLC (4H, günlük)
│   │   └── kap_listener.py      # KAP başlıkları (anahtar kelime sentiment)
│   ├── analysis/
│   │   ├── indicators.py        # BB, KC, RSI, ATR, EMA vb.
│   │   ├── squeeze_engine.py   # Triple Confirmation + Signal
│   │   ├── market_context.py   # XU100 trend, market_risk, RS, XU100 hacim uyarısı
│   │   ├── sector_correlation.py # Sektör arkadaşı hisselerin teknik durumu (Sektör Dağılımı)
│   │   ├── price_actions.py    # FVG, divergence
│   │   ├── chart.py            # mplfinance mum + BB/KC grafiği
│   │   └── news_analyzer.py    # Gemini haber süzgeci (kritik mi, puan, duyarlılık)
│   ├── notifications/
│   │   ├── telegram_bot.py     # Sinyal, KAP fırsat, acil negatif uyarı, İşlemi Onayla
│   │   └── kap_watchdog.py     # KAP API dinleyici, BIST100 + Gemini, flash scan / acil uyarı tetikleme
│   ├── position_manager.py     # active_trades, TP1/Stop/Hantallık kontrolü, get_active_symbols
│   ├── pending_signals.py      # Momentum onayı iptal mesajı
│   ├── bist_schedule.py        # BIST açılış 09:30–18:00, hafta sonu kapalı
│   ├── weekly_report.py       # Haftalık performans raporu
│   ├── logging_utils.py       # ScanSummary, write_scan_log
│   └── backtest.py            # Geçmiş sinyal simülasyonu
├── main.py                     # Ana giriş: run_cycle, run_scheduler_loop, run_flash_scan, KAP Watchdog
├── requirements.txt
├── .env.example
└── README.md
```

---

## Strateji: The Triple Confirmation

Sinyal üretimi iki yoldan biriyle yapılır:

### 1. Klasik Squeeze Breakout

1. **Macro filter:** Günlük fiyat > EMA200.
2. **Volatility Squeeze:** Bollinger Bands (20, 2) genişliği < Keltner Channel (20, 1.5) genişliği → sıkışma; bir sonraki barda BB > KC (squeeze off) ve yön bullish (kapanış > KC üst veya Momentum > 0).
3. **Volume & RSI:** Hacim ≥ Volume_MA(20) × `VOLUME_MULTIPLIER` (örn. 1.2), RSI > `RSI_THRESHOLD` (50).

### 2. Momentum Bypass (Squeeze yok)

Squeeze olmadan, **hacim ≥ 2× ortalama** + **bullish yön** + **RSI < 70** (aşırı alım guard). Bu sinyaller sadece **RS > 1.2** (göreceli güç) ise kabul edilir (main/backtest’te filtrelenir).

### Ek filtreler

- **Piyasa riski:** XU100 endeksi EMA50 altında veya RSI < 40 ise piyasa riskli; sadece RSI > `MARKET_RISK_RSI_MIN` (örn. 65) sinyaller gönderilir; önerilen lot yarıya iner.
- **Göreceli güç (RS):** Hisse/endeks getiri oranı (son 10 gün). RS < 1 sinyaller elenir; RS > 1.2 “lider” hisse olarak ek filtreler yumuşar.
- **Aşırı hacim:** Hacim > 4× ortalama ise tükeniş riski; sinyal üretilmez.
- **Trade Plan:** ATR tabanlı giriş, stop-loss, TP1, TP2 ve R/R oranı; isteğe bağlı FVG destek bölgesi notu.

Backtest ve canlı tarama **aynı motoru** (`squeeze_engine.check_signal`) kullanır; fark sadece backtest’te giriş/çıkışın simüle edilmesidir.

---

## KAP ve Haber Sistemi

### KAP Watchdog (`kap_watchdog.py`)

- **Kaynak:** `https://www.kap.org.tr/tr/api/disclosures` (veya API yanıt vermezse `kap_listener` HTML fallback).
- **Aralık:** Varsayılan 60 saniyede bir son bildirimler çekilir.
- **Ticker:** API’de `stockCodes` / `stockCode` varsa kullanılır; yoksa başlık/özetten BIST100 sembolleriyle eşleştirilir.

### Gemini Haber Süzgeci (`news_analyzer.py`)

- KAP başlığı + özet Gemini’ye gönderilir.
- Cevap formatı: **Kritik mi? (Evet/Hayır) | Puan (1-10) | Duyarlılık (Pozitif/Negatif)**.
- **Puan ≥ 7** olan haberler hem fırsat hem (negatifse) acil uyarı için kullanılır.

### İki tetikleyici

1. **Fırsat taraması (flash scan):** Haber BIST100’de bir hisse için ve puan ≥ 7 → `run_single_symbol_scan(ticker)` → en güncel 4H/1D veriyle sinyal kontrolü; RS > 1, RSI < 70. Uygunsa Telegram’a **"KAP TETİKLEMELİ FIRSAT"** (Gemini yorumu + teknik analiz + grafik).
2. **Acil negatif uyarı:** Aynı haber için hisse **aktif pozisyonda** (`active_trades.json`) ve Gemini **Negatif + puan ≥ 7** → Telegram’a **sesli** "ACİL DURUM" mesajı (bildirim sesi açık): haber başlığı, AI analizi, "ELİNDEKİNİ SAT veya Stop seviyeni çok yakına çek!" aksiyonu.

`GEMINI_API_KEY` .env’de yoksa KAP Watchdog başlamaz; diğer özellikler (periyodik tarama, pozisyon takibi) çalışır.

---

## Pozisyon Yönetimi

- **Kayıt:** Sinyal Telegram’a gidince "İşlemi Onayla" ile kayıt `active_trades.json`’a eklenir (giriş, stop, TP1, TP2, bar zamanı).
- **Kontrol:** Her tarama turunda `check_active_trades` çalışır:
  - **Multi-TP:** TP1'e ulaşınca stop breakeven'e çekilir, TP2'ye kadar takip edilir. **Stop** (veya breakeven) / **TP2** tetiklenirse kapanış bildirimi, kayıt `trade_history`’e taşınır.
  - **15 bar geçti ve kâr %1.5 altında** ise “Hantallık” mesajı, pozisyon listeden çıkarılır.
- **Negatif haber:** Watchdog, `get_active_symbols()` ile açık pozisyonları bilir; ilgili hisse için Negatif + 7+ haberde acil uyarı gönderir.

---

## Kurulum

```bash
cd Bist
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate    # Linux/macOS
pip install -r requirements.txt
```

mplfinance sadece beta sürümde olabilir; "No matching distribution" alırsan:

```bash
pip install --pre mplfinance
```

`.env.example` dosyasını `.env` olarak kopyalayıp `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` ve (KAP/haber için) `GEMINI_API_KEY` değerlerini doldurun.

---

## Yapılandırma (.env)

Tüm parametreler **proje kökündeki `.env`** dosyasından okunur. Kodda `from src.config import settings` ile erişilir.

| Grup | Değişken | Açıklama | Örnek / Varsayılan |
|------|----------|----------|---------------------|
| **API** | `TELEGRAM_BOT_TOKEN` | Telegram bot token | (zorunlu) |
| | `TELEGRAM_CHAT_ID` | Bildirim gönderilecek chat id | (zorunlu) |
| | `GEMINI_API_KEY` | KAP haber süzgeci + acil uyarı | Boş = KAP Watchdog kapalı |
| **TradingView** | `TRADINGVIEW_USERNAME` | Opsiyonel; girişle veri daha stabil | - |
| | `TRADINGVIEW_PASSWORD` | Opsiyonel | - |
| **Strateji** | `EMA_PERIOD`, `BB_PERIOD`, `BB_STD`, `KC_PERIOD`, `KC_MULT` | İndikatör periyotları | 200, 20, 2.0, 20, 1.5 |
| | `RSI_PERIOD`, `RSI_THRESHOLD` | RSI ve minimum eşik | 14, 50 |
| **Hacim** | `VOLUME_MULTIPLIER`, `VOLUME_MA_PERIOD`, `MOMENTUM_PERIOD` | Hacim spike / momentum | 1.2, 20, 12 |
| **Trade plan** | `ATR_PERIOD`, `STOP_ATR_MULT`, `TP1_ATR_MULT`, `TP2_ATR_MULT` | ATR ve hedef çarpanları | 14, 2.0, 2.0, 8.0 |
| **Piyasa riski** | `MARKET_RISK_RSI_MIN` | Endeks zayıfken min RSI | 55–65 |
| **Risk** | `TRADING_CAPITAL`, `RISK_PCT` | Önerilen lot hesabı | 100000, 5 |
| **Bot** | `SCAN_INTERVAL_MINUTES` | Periyodik tarama aralığı (dakika) | 240 (4 saat) |
| | `REQUEST_DELAY` | Hisse başına bekleme (saniye) | 0.5 |

---

## Çalıştırma

### Sürekli mod (zamanlayıcı + KAP Watchdog)

Bot sürekli döngüde çalışır: BIST açıkken periyodik tarama, ayrıca KAP Watchdog (GEMINI_API_KEY varsa) 60 sn’de bir bildirim kontrolü.

```bash
python main.py
```

- Tarama başlamadan önce: "🔍 BIST Sentinel taraması başlıyor..."
- Tarama bitince: özet (taranan/sinyal sayısı, sinyal veren semboller, eleme detayları).
- KAP’ta puan 7+ haber → ilgili hisse için flash scan; aktif pozisyonda negatif haber → sesli acil uyarı.

### Tek seferlik tarama

```bash
python main.py --once
```

Döngüye girmez; bir tur tarama yapıp çıkar.

### Diğer seçenekler

| Argüman | Açıklama |
|--------|----------|
| `--no-telegram` | Telegram bildirimi gönderme |
| `--no-chart` | Sinyal mesajına grafik ekleme |
| `--no-log` | data/logs özet dosyası yazma |
| `--list <path>` | Hisse listesi (varsayılan: data/BIST100.txt) |
| `--delay <saniye>` | Hisse başına bekleme |
| `--no-kap-sentiment` | KAP anahtar kelime sentiment kullanma |
| `--backtest` | Backtest modu (aşağıda) |
| `--backtest-bars 1000` | Backtest bar sayısı |
| `--backtest-top 30` | Raporda en iyi N sembol |

---

## BIST Mesai ve Zamanlayıcı

- **Açılış:** Pazartesi–Cuma **09:30–18:00** (İstanbul).
- Zamanlayıcı döngüsünde bot **sadece BIST açıkken** tarama yapar; hafta sonu ve mesai dışında duraklar, açılışa kadar bekler (bekleme sırasında uzun süre kapalıysa Telegram’a bilgi mesajı gidebilir).
- KAP Watchdog ise 7/24 çalışır (haber geldiği anda flash scan ve acil uyarı tetiklenebilir).

---

## Loglama ve Görsel Kanıt

- **data/logs/** altında her tarama için:
  - `scan_YYYYMMDD_HHMMSS.json` — taranan hisse, veri yok, hata, EMA altı, sıkışma, sinyal sayısı, semboller, eleme nedenleri.
  - `scan_YYYYMMDD_HHMMSS.txt` — aynı özetin okunabilir hali.
- Sinyal üretildiğinde **data/charts/** altında mum + BB/KC grafiği PNG kaydedilir ve Telegram mesajına fotoğraf olarak eklenir (KAP tetiklemeli fırsatta da grafik gider).

---

## Backtest

Geçmiş 4H/günlük veriyle aynı sinyal motoru kullanılır; giriş/çıkış (stop, TP2, TP1) simüle edilir.

```bash
python main.py --backtest
python main.py --backtest --backtest-bars 1000 --backtest-top 30
```

Rapor konsola yazdırılır; sembol bazında işlem sayısı, kazanma oranı, toplam P/L (%), ortalama işlem P/L.

---

## Telegram Mesaj Formatları

### Normal sinyal (Trade Plan ile)

- Başlık: ALIM SİNYALİ, giriş/stop/TP1/TP2, önerilen lot, R/R, FVG destek (varsa), RSI/hacim, KAP durumu.
- "İşlemi Onayla" butonu ile pozisyon takibe alınır.

### KAP tetiklemeli fırsat

- "🔥 KAP TETİKLEMELİ FIRSAT"
- Gemini haber yorumu + puan/duyarlılık.
- Aynı sinyal formatı (giriş, stop, TP1, TP2) + grafik.

### Acil negatif haber (aktif pozisyon)

- "🚨 ACİL DURUM: #SYMBOL için Kritik Negatif Haber!"
- Haber başlığı, AI analizi, "Bu hisse şu an portföyünüzde aktif! ELİNDEKİNİ SAT veya Stop seviyeni çok yakına çek!"
- **Sessiz değil:** `disable_notification: False` ile bildirim sesi çalar.

### Telegram komutları

- **`/bak THYAO`** — İstediğin hisse için anında özet: 4H grafik (PNG), son 3 KAP bildirimi ve **teknik puan (0–10)**. Sadece BIST100 listesindeki semboller kabul edilir; aynı chat (TELEGRAM_CHAT_ID) üzerinden kullanılır.

---

## TradingView ve Veri Kaynağı

- OHLC verisi **tradingview-datafeed** (TradingView) üzerinden; 4H ve günlük.
- **Connection timed out / no data:** `.env`’e `TRADINGVIEW_USERNAME` ve `TRADINGVIEW_PASSWORD` ekleyerek giriş yapmak isteğe bağlıdır; girişle istekler genelde daha kabul görür.
- **error while signin / reCAPTCHA:** TradingView giriş sayfasında reCAPTCHA istediği için bot tarafından giriş bazen başarısız olur. Bu durumda bu iki değişkeni boş bırakın; bot misafir modda çalışır (bazen timeout alabilirsiniz).

---

## Bağımlılıklar

- **tradingview-datafeed** — TradingView BIST OHLC (4H, günlük).
- **pandas**, **pandas-ta**, **numpy** — Veri ve indikatör hesapları.
- **mplfinance**, **matplotlib** — Sinyal grafiği PNG.
- **requests** — Telegram API, KAP istekleri.
- **python-dotenv** — .env yükleme.
- **google-generativeai** — KAP haber süzgeci (Gemini).
- **feedparser** — (kap_listener fallback / RSS ihtiyacı için.)

Tüm liste `requirements.txt` içindedir.
