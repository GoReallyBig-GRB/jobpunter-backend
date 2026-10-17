from io import BytesIO
from types import SimpleNamespace

import pytest
from reportlab.pdfgen import canvas

from app.infrastructure.ocr import google_vision


def _blank_pdf() -> bytes:
    output = BytesIO()
    pdf = canvas.Canvas(output)
    pdf.showPage()
    pdf.save()
    return output.getvalue()


def test_google_vision_ocr_batches_pages_and_returns_page_indexes(monkeypatch):
    calls = []

    class FakeClient:
        def batch_annotate_images(self, *, requests, timeout):
            calls.append((requests, timeout))
            return SimpleNamespace(responses=[
                SimpleNamespace(
                    error=SimpleNamespace(message=""),
                    full_text_annotation=SimpleNamespace(text="Recognized candidate text"),
                )
                for _ in requests
            ])

    monkeypatch.setattr(google_vision.vision, "ImageAnnotatorClient", lambda: FakeClient())
    result = google_vision.ocr_pdf_pages(
        _blank_pdf(), [0], timeout_seconds=19, batch_size=1,
    )
    assert result == {0: "Recognized candidate text"}
    assert len(calls) == 1
    assert calls[0][1] == 19
    assert len(calls[0][0]) == 1


def test_google_vision_ocr_sanitizes_provider_errors(monkeypatch):
    class FakeClient:
        def batch_annotate_images(self, *, requests, timeout):
            return SimpleNamespace(responses=[
                SimpleNamespace(
                    error=SimpleNamespace(message="sensitive upstream response"),
                    full_text_annotation=None,
                )
            ])

    monkeypatch.setattr(google_vision.vision, "ImageAnnotatorClient", lambda: FakeClient())
    with pytest.raises(google_vision.OCRProviderError) as error:
        google_vision.ocr_pdf_pages(_blank_pdf(), [0])
    assert "sensitive upstream response" not in str(error.value)
