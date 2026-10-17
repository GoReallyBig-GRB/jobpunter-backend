"""Google Cloud Vision OCR for scanned PDF pages.

OCR is called only for pages with no selectable text, and only when the operator
explicitly enables it. Provider messages are never surfaced to candidates.
"""
from __future__ import annotations

from typing import Any

import fitz
from google.api_core.exceptions import GoogleAPIError
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import vision

MAX_OCR_IMAGE_BYTES = 8 * 1024 * 1024
DEFAULT_RENDER_SCALE = 1.5


class OCRUnavailableError(RuntimeError):
    """OCR credentials or the OCR API are unavailable."""


class OCRProviderError(RuntimeError):
    """The OCR provider could not process one or more pages."""


def _render_page(page: fitz.Page) -> bytes:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(DEFAULT_RENDER_SCALE, DEFAULT_RENDER_SCALE), alpha=False)
    content = pixmap.tobytes("png")
    if len(content) > MAX_OCR_IMAGE_BYTES:
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.0, 1.0), alpha=False)
        content = pixmap.tobytes("png")
    if len(content) > MAX_OCR_IMAGE_BYTES:
        raise OCRProviderError("ocr_page_image_exceeds_provider_limit")
    return content


def ocr_pdf_pages(
    content: bytes,
    page_indices: list[int],
    *,
    timeout_seconds: int = 30,
    batch_size: int = 8,
) -> dict[int, str]:
    """Return OCR text keyed by zero-based PDF page index."""
    if not page_indices:
        return {}
    if not 1 <= batch_size <= 16:
        raise ValueError("batch_size must be between 1 and 16")

    try:
        client = vision.ImageAnnotatorClient()
    except DefaultCredentialsError as exc:
        raise OCRUnavailableError("ocr_credentials_unavailable") from exc
    except Exception as exc:
        raise OCRUnavailableError("ocr_client_unavailable") from exc

    results: dict[int, str] = {}
    try:
        with fitz.open(stream=content, filetype="pdf") as pdf:
            if any(index < 0 or index >= pdf.page_count for index in page_indices):
                raise OCRProviderError("ocr_page_index_out_of_range")
            for start in range(0, len(page_indices), batch_size):
                indices = page_indices[start:start + batch_size]
                requests: list[Any] = []
                for index in indices:
                    image_bytes = _render_page(pdf.load_page(index))
                    requests.append(vision.AnnotateImageRequest(
                        image=vision.Image(content=image_bytes),
                        features=[vision.Feature(type_=vision.Feature.Type.DOCUMENT_TEXT_DETECTION)],
                    ))
                response = client.batch_annotate_images(requests=requests, timeout=timeout_seconds)
                if len(response.responses) != len(indices):
                    raise OCRProviderError("ocr_provider_response_count_mismatch")
                for index, item in zip(indices, response.responses):
                    if item.error.message:
                        raise OCRProviderError("ocr_provider_rejected_page")
                    annotation = getattr(item, "full_text_annotation", None)
                    results[index] = annotation.text if annotation and annotation.text else ""
    except (OCRUnavailableError, OCRProviderError):
        raise
    except DefaultCredentialsError as exc:
        raise OCRUnavailableError("ocr_credentials_unavailable") from exc
    except GoogleAPIError as exc:
        raise OCRProviderError("ocr_provider_request_failed") from exc
    except Exception as exc:
        raise OCRProviderError("ocr_document_processing_failed") from exc
    finally:
        transport = getattr(client, "transport", None)
        close = getattr(transport, "close", None)
        if callable(close):
            close()
    return results
