"""
Gemini Haber Süzgeci: KAP haber başlığı ve özetini analiz eder.
Sadece fiyatı etkileyebilecek haberler (puan 7+) tarama tetikler; gereksiz haberlerle sistemi yormaz.

Cevap formatı: "Kritik mi? (Evet/Hayır) | Puan (1-10) | Duyarlılık (Pozitif/Negatif)"
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from src.config import settings


@dataclass
class NewsAnalysis:
    """Gemini çıktısı: kritik mi, puan, duyarlılık, ham yorum."""
    critical: bool
    score: int
    sentiment: str  # "Pozitif" / "Negatif" / "Nötr"
    comment: str


def analyze_news(title: str, summary: str = "") -> Optional[NewsAnalysis]:
    """
    KAP haber başlığı ve özetini Gemini'ye gönderir.
    Dönen: Kritik mi? (Evet/Hayır) | Puan (1-10) | Duyarlılık (Pozitif/Negatif).
    Sadece Puan 7 ve üzeri haberler için tarama tetiklenir (çağıran tarafında kontrol).

    Returns:
        NewsAnalysis veya API yok/hatada None.
    """
    api_key = (settings.GEMINI_API_KEY or "").strip()
    if not api_key:
        return None

    text = f"Başlık: {title}\n\nÖzet: {summary[:1500]}" if summary else f"Başlık: {title}"

    prompt = (
        "Aşağıdaki KAP (Kamuyu Açıklama Platformu) haberini Borsa İstanbul hisse senedi fiyatını "
        "etkileyebilirlik açısından değerlendir. Yönetim kurulu toplantı tarihi, rutin idari "
        "açıklamalar gibi düşük etkili haberler düşük puan alsın; sözleşme, kar payı, büyük "
        "iş anlaşmaları, sermaye artışı, ciddi dava/ceza gibi fiyatı hareket ettirebilecek "
        "haberler yüksek puan alsın.\n\n"
        "Yanıtını SADECE şu formatta ver, başka metin ekleme:\n"
        "Kritik mi? (Evet/Hayır) | Puan (1-10) | Duyarlılık (Pozitif/Negatif)\n\n"
        "Örnek: Kritik mi? Evet | Puan (1-10) 8 | Duyarlılık Pozitif\n\n"
        "Haber:\n" + text
    )

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=prompt,
        )
        raw = (response.text or "").strip()
    except Exception as e:
        print(f"  [Gemini analyze_news hatasi] {e}")
        return None

    return _parse_gemini_response(raw)


def _parse_gemini_response(raw: str) -> Optional[NewsAnalysis]:
    """Gemini çıktısından Kritik/Puan/Duyarlılık ayıklar."""
    critical = "evet" in raw.lower().split("kritik")[-1].split("|")[0].strip().lower()
    # Puan (1-10) 7 veya Puan: 7
    score_match = re.search(r"[Pp]uan\s*[\(\s]*(?:1-10)?\s*\)?\s*[:=]?\s*(\d+)", raw)
    score = int(score_match.group(1)) if score_match else 5
    score = max(1, min(10, score))
    # Duyarlılık
    sentiment = "Nötr"
    if "pozitif" in raw.lower():
        sentiment = "Pozitif"
    elif "negatif" in raw.lower():
        sentiment = "Negatif"
    return NewsAnalysis(critical=critical, score=score, sentiment=sentiment, comment=raw[:500])
