"""
Telegram entegrasyonu - bot sinyalleri belirtilen formatta gönderilir.
Sinyal mesajına opsiyonel olarak grafik (PNG) eklenebilir.
İşlem onayı: Sinyal mesajındaki 'İşlemi Onayla' butonu ile callback dinlenir.
Token ve chat_id src.config.settings üzerinden okunur.
"""
from pathlib import Path
from typing import Any, Optional

from src.config import settings


def send_telegram_message(
    text: str,
    chat_id: Optional[str] = None,
    token: Optional[str] = None,
    parse_mode: Optional[str] = None,
) -> bool:
    """
    Telegram'a düz metin mesajı gönderir (zamanlayıcı bildirimleri, tarama özeti vb.).
    parse_mode "HTML" verilirse <b>, <i> vb. etiketler işlenir.
    """
    token = token or settings.TELEGRAM_BOT_TOKEN
    chat_id = chat_id or settings.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return False
    try:
        import requests
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        r = requests.post(url, json=payload, timeout=10)
        return r.ok
    except Exception:
        return False


def send_urgent_negative_news_alert(
    symbol: str,
    headline: str,
    gemini_comment: str,
    chat_id: Optional[str] = None,
    token: Optional[str] = None,
) -> bool:
    """
    Aktif pozisyondaki hisse için kritik negatif haber uyarısı.
    Mesaj sessiz (silent) GÖNDERİLMEZ — disable_notification=False ile bildirim sesi çalar.
    """
    token = token or settings.TELEGRAM_BOT_TOKEN
    chat_id = chat_id or settings.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return False
    symbol_upper = symbol.upper()
    text = (
        f"🚨 <b>ACİL DURUM: #{symbol_upper} için Kritik Negatif Haber!</b>\n\n"
        f"🗞️ <b>Haber:</b> {_escape_html(headline[:500])}\n\n"
        f"🤖 <b>AI Analizi:</b>\n{_escape_html((gemini_comment or '—')[:600])}\n\n"
        "⚠️ <b>Aksiyon:</b> Bu hisse şu an portföyünüzde aktif! "
        "ELİNDEKİNİ SAT veya Stop seviyeni çok yakına çek!"
    )
    try:
        import requests
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": False,
        }
        r = requests.post(url, json=payload, timeout=10)
        return r.ok
    except Exception:
        return False


def _escape_html(s: str) -> str:
    """Telegram HTML parse_mode için & < > karakterlerini kaçırır."""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") if s else "")


def format_signal_message(
    symbol: str,
    timeframe: str,
    strategy: str,
    price: float,
    trend: str,
    kap_status: str,
    rsi: Optional[float] = None,
    volume_ratio: Optional[float] = None,
    entry: Optional[float] = None,
    stop_loss: Optional[float] = None,
    tp1: Optional[float] = None,
    tp2: Optional[float] = None,
    rr_ratio: Optional[float] = None,
    market_risk: bool = False,
    relative_strength: Optional[float] = None,
    fvg_support_below: Optional[tuple[float, float]] = None,
    rvol_ratio: Optional[float] = None,
    signal_bar_high: Optional[float] = None,
    recommended_lot: Optional[int] = None,
    risk_pct: Optional[float] = None,
    atr: Optional[float] = None,
    sector_note: Optional[str] = None,
    index_volume_warning: Optional[str] = None,
) -> str:
    """
    Sinyal mesajını üretir. Trade Plan + piyasa riski, RS, FVG destek notu eklenebilir.
    """
    has_plan = entry is not None and stop_loss is not None and tp1 is not None and tp2 is not None and rr_ratio is not None

    # Sinyal mesajının en üstüne piyasa durumu (Market Regime)
    regime_line = "🌩️ PİYASA RİSKLİ: Endeks zayıf!" if market_risk else "🌤️ Piyasa Pozitif"
    header_lines = [regime_line, ""]

    def _context_lines() -> list[str]:
        extra = []
        if market_risk:
            extra.append("⚠️ Yüksek Piyasa Riski (XU100 < EMA50 veya RSI < 40)")
        if relative_strength is not None:
            if relative_strength > 1:
                extra.append(f"📈 Göreceli Güç (RS): {relative_strength:.2f} — Endeks üstü getiri")
            else:
                extra.append(f"📈 Göreceli Güç (RS): {relative_strength:.2f}")
        if fvg_support_below is not None:
            low, high = fvg_support_below
            extra.append(f"🧲 FVG destek bölgesi: {low:.2f} - {high:.2f} TL (mıknatıs)")
        # RVOL/Divergence kaldırıldı; sadece RS, EMA, Volume
        return extra

    if has_plan:
        pct = int(risk_pct) if risk_pct is not None else 2
        lines = header_lines + [
            "🚀 <b>#" + symbol.upper() + " — ALIM SİNYALİ</b>",
            "",
            "▫️ <b>Fiyat Seviyeleri</b>",
            f"🔹 Giriş Seviyesi: <b>{entry:.2f} TL</b>",
            f"🛑 Stop-Loss: <b>{stop_loss:.2f} TL</b>",
            f"🎯 Hedef 1 (TP1): <b>{tp1:.2f} TL</b> (Risk Sıfırlama)",
            f"🏁 Hedef 2 (TP2): <b>{tp2:.2f} TL</b> (Trend Takibi)",
            "",
        ]
        if atr is not None and atr > 0:
            emergency_stop = entry - 2.5 * atr
            lines.append(f"🚨 Acil Stop: <b>{emergency_stop:.2f} TL</b> (ATR tabanlı kesin çıkış)")
            lines.append("")
        if recommended_lot is not None and recommended_lot > 0:
            lines.append(f"💰 Önerilen Pozisyon: <b>{recommended_lot} Adet</b>")
            lines.append(f"📊 Kasa Riski: <b>%{pct}</b>")
            lines.append("")
        lines.append("ℹ️ <b>Strateji:</b> TP1'de %50 nakde çevir, stop maliyete çek; kalan %50 EMA10 trailing. 15 bar içinde %1.5 kâr yoksa time-out.")
        if signal_bar_high is not None:
            lines.append(f"📌 Giriş onayı: Fiyat <b>{signal_bar_high:.2f} TL</b> üstüne çıkarsa girin (sonraki ~3 bar).")
        if rr_ratio is not None:
            rr = f"1:{int(rr_ratio)}" if rr_ratio >= 1 else f"{rr_ratio}"
            lines.append(f"⚖️ R/R: {rr}")
        if sector_note:
            lines.append("")
            lines.append(f"🔍 {sector_note}")
        if index_volume_warning:
            lines.append("")
            lines.append(f"⚠️ {index_volume_warning}")
        lines.extend(_context_lines())
        if rsi is not None or volume_ratio is not None:
            lines.append("")
            if rsi is not None:
                lines.append(f"📈 RSI: {rsi} | Hacim: {volume_ratio}x" if volume_ratio is not None else f"📈 RSI: {rsi}")
            elif volume_ratio is not None:
                lines.append(f"Hacim Oranı: {volume_ratio}x")
        lines.append(f"🕐 {timeframe} | {strategy} | {trend} | KAP: {kap_status}")
        return "\n".join(lines)

    lines = header_lines + [
        "🚀 YENİ SİNYAL: #" + symbol.upper(),
        f"Periyot: {timeframe}",
        f"Strateji: {strategy}",
        f"Fiyat: {price} TL",
        f"Trend: {trend}",
        f"KAP Durumu: {kap_status}",
    ]
    lines.extend(_context_lines())
    if rsi is not None:
        lines.append(f"RSI: {rsi}")
    if volume_ratio is not None:
        lines.append(f"Hacim Oranı (Vol/MA20): {volume_ratio}x")
    return "\n".join(lines)


def send_signal(
    symbol: str,
    timeframe: str,
    strategy: str,
    price: float,
    trend: str,
    kap_status: str,
    rsi: Optional[float] = None,
    volume_ratio: Optional[float] = None,
    entry: Optional[float] = None,
    stop_loss: Optional[float] = None,
    tp1: Optional[float] = None,
    tp2: Optional[float] = None,
    rr_ratio: Optional[float] = None,
    market_risk: bool = False,
    relative_strength: Optional[float] = None,
    fvg_support_below: Optional[tuple[float, float]] = None,
    rvol_ratio: Optional[float] = None,
    signal_bar_high: Optional[float] = None,
    recommended_lot: Optional[int] = None,
    risk_pct: Optional[float] = None,
    atr: Optional[float] = None,
    sector_note: Optional[str] = None,
    index_volume_warning: Optional[str] = None,
    chat_id: Optional[str] = None,
    token: Optional[str] = None,
    image_path: Optional[Path | str] = None,
) -> bool:
    """
    Sinyali Telegram'a gönderir. image_path verilirse önce fotoğraf (caption ile), yoksa sadece mesaj.

    Returns:
        Gönderim başarılıysa True, aksi halde False.
    """
    token = token or settings.TELEGRAM_BOT_TOKEN
    chat_id = chat_id or settings.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        print("  ⚠️ Telegram atlandı: TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID .env'de boş.")
        return False

    text = format_signal_message(
        symbol=symbol,
        timeframe=timeframe,
        strategy=strategy,
        price=price,
        trend=trend,
        kap_status=kap_status,
        rsi=rsi,
        volume_ratio=volume_ratio,
        entry=entry,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=tp2,
        rr_ratio=rr_ratio,
        market_risk=market_risk,
        relative_strength=relative_strength,
        fvg_support_below=fvg_support_below,
        rvol_ratio=rvol_ratio,
        signal_bar_high=signal_bar_high,
        recommended_lot=recommended_lot,
        risk_pct=risk_pct,
        atr=atr,
        sector_note=sector_note,
        index_volume_warning=index_volume_warning,
    )

    # Trade plan varsa mesajın altına "İşlemi Onayla" butonu ekle (callback_data max 64 byte)
    reply_markup = None
    if entry is not None and stop_loss is not None and tp1 is not None and tp2 is not None:
        callback_data = f"confirm_trade:{symbol.upper()}:{entry:.2f}"
        if len(callback_data) <= 64:
            reply_markup = {"inline_keyboard": [[{"text": "✅ İŞLEMİ ONAYLA", "callback_data": callback_data}]]}

    try:
        import requests
        path = Path(image_path) if image_path else None
        if path and path.is_file():
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            data = {"chat_id": chat_id, "caption": text, "parse_mode": "HTML"}
            if reply_markup:
                import json as _json
                data["reply_markup"] = _json.dumps(reply_markup)
            with open(path, "rb") as f:
                r = requests.post(
                    url,
                    data=data,
                    files={"photo": f},
                    timeout=15,
                )
            if not r.ok:
                print(f"  ⚠️ Telegram API (foto): {r.status_code} — {r.text[:200]}")
                return False
            return True
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if reply_markup:
            import json as _json
            payload["reply_markup"] = _json.dumps(reply_markup)
        r = requests.post(url, json=payload, timeout=10)
        if not r.ok:
            print(f"  ⚠️ Telegram API (mesaj): {r.status_code} — {r.text[:200]}")
            return False
        return True
    except Exception as e:
        print(f"  ⚠️ Telegram gönderim hatası: {e}")
        return False


def send_kap_triggered_opportunity(
    signal,
    news_comment: str,
    image_path: Optional[Path | str] = None,
    news_score: Optional[int] = None,
    news_sentiment: Optional[str] = None,
) -> bool:
    """
    KAP tetiklemeli fırsat: '🔥 KAP TETİKLEMELİ FIRSAT' başlığı ile Gemini haber yorumu +
    teknik analiz (sinyal + grafik) Telegram'a gönderilir.
    """
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return False
    header = "🔥 <b>KAP TETİKLEMELİ FIRSAT</b>\n\n"
    gemini_block = "📰 <b>Gemini Haber Yorumu:</b>\n" + (news_comment or "—")[:800] + "\n\n"
    if news_score is not None or news_sentiment:
        extra = []
        if news_score is not None:
            extra.append(f"Puan: {news_score}/10")
        if news_sentiment:
            extra.append(f"Duyarlılık: {news_sentiment}")
        if extra:
            gemini_block += " | ".join(extra) + "\n\n"
    body = format_signal_message(
        symbol=signal.symbol,
        timeframe=signal.timeframe,
        strategy=signal.strategy,
        price=signal.price,
        trend=signal.trend,
        kap_status=getattr(signal, "kap_status", "KAP tetikleme"),
        rsi=signal.rsi,
        volume_ratio=signal.volume_ratio,
        entry=getattr(signal, "entry", None),
        stop_loss=getattr(signal, "stop_loss", None),
        tp1=getattr(signal, "tp1", None),
        tp2=getattr(signal, "tp2", None),
        rr_ratio=getattr(signal, "rr_ratio", None),
        market_risk=getattr(signal, "market_risk", False),
        relative_strength=getattr(signal, "relative_strength", None),
        fvg_support_below=getattr(signal, "fvg_support_below", None),
        rvol_ratio=getattr(signal, "rvol_ratio", None),
        signal_bar_high=getattr(signal, "signal_bar_high", None),
        recommended_lot=getattr(signal, "recommended_lot", None),
        risk_pct=getattr(settings, "RISK_PCT", 2),
        atr=getattr(signal, "atr", None),
    )
    text = header + gemini_block + body
    try:
        import requests
        path = Path(image_path) if image_path else None
        if path and path.is_file():
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            with open(path, "rb") as f:
                r = requests.post(
                    url,
                    data={"chat_id": chat_id, "caption": text, "parse_mode": "HTML"},
                    files={"photo": f},
                    timeout=15,
                )
            return r.ok
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        r = requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
        return r.ok
    except Exception:
        return False


def send_signal_from_dataclass(
    signal,
    image_path: Optional[Path | str] = None,
    sector_note: Optional[str] = None,
    index_volume_warning: Optional[str] = None,
) -> bool:
    """
    analysis.Signal dataclass'ından Telegram'a gönderir. Trade Plan varsa ALIM PLANI formatında.
    sector_note ve index_volume_warning verilirse sinyal mesajına eklenir.
    """
    return send_signal(
        symbol=signal.symbol,
        timeframe=signal.timeframe,
        strategy=signal.strategy,
        price=signal.price,
        trend=signal.trend,
        kap_status=signal.kap_status,
        rsi=signal.rsi,
        volume_ratio=signal.volume_ratio,
        entry=getattr(signal, "entry", None),
        stop_loss=getattr(signal, "stop_loss", None),
        tp1=getattr(signal, "tp1", None),
        tp2=getattr(signal, "tp2", None),
        rr_ratio=getattr(signal, "rr_ratio", None),
        market_risk=getattr(signal, "market_risk", False),
        relative_strength=getattr(signal, "relative_strength", None),
        fvg_support_below=getattr(signal, "fvg_support_below", None),
        rvol_ratio=getattr(signal, "rvol_ratio", None),
        signal_bar_high=getattr(signal, "signal_bar_high", None),
        recommended_lot=getattr(signal, "recommended_lot", None),
        risk_pct=getattr(settings, "RISK_PCT", 2),
        atr=getattr(signal, "atr", None),
        sector_note=sector_note or getattr(signal, "sector_note", None),
        index_volume_warning=index_volume_warning,
        image_path=image_path,
    )


def _telegram_api(token: str, method: str, **kwargs: Any) -> Optional[dict]:
    """Telegram Bot API isteği; hata durumunda None döner."""
    try:
        import requests
        url = f"https://api.telegram.org/bot{token}/{method}"
        r = requests.post(url, json=kwargs, timeout=15)
        if not r.ok:
            return None
        return r.json()
    except Exception:
        return None


def handle_callback_query(
    callback_query: dict,
    token: str,
    allowed_chat_id: str,
) -> None:
    """
    Buton tıklamasını işler: Sadece allowed_chat_id (TELEGRAM_CHAT_ID) kabul edilir.
    confirm_trade:SYMBOL:ENTRY → pozisyonu takibe al, onay mesajı gönder, butonu kaldır.
    """
    msg = callback_query.get("message") or {}
    chat_id = str(msg.get("chat", {}).get("id", ""))
    if chat_id != allowed_chat_id:
        _telegram_api(token, "answerCallbackQuery", callback_query_id=callback_query.get("id"), text="Yetkisiz.")
        return

    data = (callback_query.get("data") or "").strip()
    if not data.startswith("confirm_trade:"):
        _telegram_api(token, "answerCallbackQuery", callback_query_id=callback_query.get("id"))
        return

    parts = data.split(":", 2)
    if len(parts) != 3:
        _telegram_api(token, "answerCallbackQuery", callback_query_id=callback_query.get("id"), text="Geçersiz veri.")
        return

    _, symbol, entry_str = parts
    try:
        entry_price = float(entry_str.replace(",", "."))
    except ValueError:
        _telegram_api(token, "answerCallbackQuery", callback_query_id=callback_query.get("id"), text="Geçersiz fiyat.")
        return

    from src.position_manager import add_trade, get_and_remove_pending_approval

    pending = get_and_remove_pending_approval(symbol, entry_price)
    if not pending:
        _telegram_api(
            token, "answerCallbackQuery",
            callback_query_id=callback_query.get("id"),
            text="Bu sinyal artık onaylanamıyor.",
            show_alert=True,
        )
        return

    add_trade(
        symbol=pending["symbol"],
        exchange=pending.get("exchange", "BIST"),
        entry_price=pending["entry_price"],
        stop_loss=pending["stop_loss"],
        tp1=pending["tp1"],
        tp2=pending["tp2"],
        entry_bar_time=pending["entry_bar_time"],
    )

    # Kullanıcıya onay mesajı
    send_telegram_message(f"🚀 #{symbol} takibe alındı. Hedefler izleniyor...", chat_id=chat_id, token=token)
    # Orijinal mesajdaki butonu kaldır
    _telegram_api(
        token, "editMessageReplyMarkup",
        chat_id=chat_id,
        message_id=msg.get("message_id"),
        reply_markup={"inline_keyboard": []},
    )
    _telegram_api(token, "answerCallbackQuery", callback_query_id=callback_query.get("id"), text="Onaylandı.")


def _send_photo_to_chat(token: str, chat_id: str, photo_path: Path, caption: str) -> bool:
    """Belirtilen chat'e fotoğraf ve caption gönderir (ör. /bak yanıtı)."""
    try:
        import requests
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        with open(photo_path, "rb") as f:
            r = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                files={"photo": f},
                timeout=15,
            )
        return r.ok
    except Exception:
        return False


def handle_bak_command(chat_id: str, symbol: str, token: str) -> None:
    """
    /bak SYMBOL: Hisse için 4H grafik, son 3 KAP haber özeti ve teknik puan (0-10) gönderir.
    Sadece BIST100 listesindeki semboller kabul edilir.
    """
    from pathlib import Path
    from src.scrapers.tv_scraper import load_bist_symbols, get_ohlc_bist
    from src.analysis.squeeze_engine import TripleConfirmationEngine
    from src.analysis.chart import build_squeeze_chart
    from src.notifications.kap_watchdog import get_recent_disclosures_for_symbol

    sym_upper = symbol.upper().strip()
    symbols = load_bist_symbols()
    exchange = "BIST"
    for sym, ex in symbols:
        if sym.upper() == sym_upper:
            exchange = ex
            break
    else:
        send_telegram_message(
            f"⚠️ #{sym_upper} BIST100 listesinde bulunamadı. /bak THYAO şeklinde deneyin.",
            chat_id=chat_id,
            token=token,
        )
        return

    try:
        data = get_ohlc_bist(sym_upper, exchange, n_bars=500)
    except Exception:
        send_telegram_message("⚠️ Veri alınamadı (TradingView).", chat_id=chat_id, token=token)
        return
    df_4h = data.get("4h")
    df_1d = data.get("1d")
    if df_4h is None or df_4h.empty or df_1d is None or df_1d.empty:
        send_telegram_message(f"⚠️ #{sym_upper} için OHLC verisi yok.", chat_id=chat_id, token=token)
        return

    engine = TripleConfirmationEngine(df_1d, df_4h, sym_upper)
    result = engine.check_signal()
    if isinstance(result, tuple):
        result = None

    if result:
        score = 9
        summary = f"Aktif sinyal: {result.strategy}. Giriş: {getattr(result, 'entry', '—')} TL."
    else:
        score = 0
        if engine.macro_filter_ok():
            score += 3
        if engine.squeeze_on_current_bar():
            score += 2
        row = engine.data_4h.iloc[-1] if not engine.data_4h.empty else None
        if row is not None:
            rsi = row.get("rsi")
            if rsi is not None and not (hasattr(rsi, "__float__") and (rsi < 30 or rsi > 70)):
                score += 2
            vol = row.get("volume") or row.get("Volume")
            vol_ma = row.get("volume_ma20")
            if vol is not None and vol_ma is not None and vol_ma > 0 and float(vol) >= float(vol_ma) * 1.2:
                score += 2
        score = min(7, score)
        summary = "Aktif sinyal yok. Yukarıdaki puan mevcut 4H/günlük teknik duruma göre."

    disclosures = get_recent_disclosures_for_symbol(sym_upper, limit=3)
    kap_lines = "\n".join(f"• {_escape_html(d.title[:100])}" + ("…" if len(d.title) > 100 else "") for d in disclosures)
    if not kap_lines:
        kap_lines = "Son KAP bildirimi bulunamadı."

    caption = (
        f"📊 <b>#{sym_upper} Özet</b>\n\n"
        f"<b>Teknik Puan:</b> {score}/10\n"
        f"{_escape_html(summary)}\n\n"
        f"<b>Son 3 KAP:</b>\n{kap_lines}"
    )
    chart_path = build_squeeze_chart(engine.data_4h, sym_upper, bars=100)
    if chart_path and chart_path.is_file():
        _send_photo_to_chat(token, chat_id, chart_path, caption)
    else:
        send_telegram_message(caption, chat_id=chat_id, token=token, parse_mode="HTML")


def handle_message(update: dict, token: str, allowed_chat_id: str) -> bool:
    """
    Gelen mesajı işler. /bak SYMBOL komutu ise handle_bak_command çağrılır.
    Sadece allowed_chat_id kabul edilir. İşlendiyse True döner.
    """
    msg = update.get("message") or {}
    chat_id = str(msg.get("chat", {}).get("id", ""))
    if chat_id != allowed_chat_id:
        return False
    text = (msg.get("text") or "").strip()
    if not text.upper().startswith("/BAK"):
        return False
    parts = text.split(maxsplit=1)
    symbol = (parts[1].strip() if len(parts) > 1 else "").upper()
    if not symbol:
        send_telegram_message("Kullanım: /bak THYAO", chat_id=chat_id, token=token)
        return True
    handle_bak_command(chat_id, symbol, token)
    return True


def run_callback_polling(token: str, allowed_chat_id: str, poll_interval: float = 1.0) -> None:
    """
    getUpdates ile callback_query ve /bak gibi mesaj komutlarını dinler;
    sadece allowed_chat_id (TELEGRAM_CHAT_ID) kabul edilir.
    Sonsuz döngü — arka planda thread olarak çalıştırılmalı.
    """
    offset = 0
    while True:
        try:
            resp = _telegram_api(token, "getUpdates", offset=offset, timeout=30)
            if not resp or not resp.get("ok"):
                import time
                time.sleep(poll_interval)
                continue
            for upd in resp.get("result", []):
                offset = upd.get("update_id", offset) + 1
                cq = upd.get("callback_query")
                if cq:
                    handle_callback_query(cq, token, allowed_chat_id)
                    continue
                if upd.get("message"):
                    handle_message(upd, token, allowed_chat_id)
        except Exception:
            import time
            time.sleep(poll_interval)
