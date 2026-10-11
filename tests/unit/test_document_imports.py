from io import BytesIO
from uuid import uuid4

import pytest
from docx import Document as DocxDocument
from reportlab.pdfgen import canvas

from app.api.errors import DomainError
from app.application.documents.imports import extract_document_text, validate_document_upload


def _docx_bytes():
    output = BytesIO()
    document = DocxDocument()
    document.add_heading("Career History", level=1)
    document.add_paragraph("Product Manager at Example Ltd.")
    document.save(output)
    return output.getvalue()


def _pdf_bytes():
    output = BytesIO()
    pdf = canvas.Canvas(output)
    pdf.drawString(72, 720, "Product Manager at Example Ltd.")
    pdf.save()
    return output.getvalue()


@pytest.mark.parametrize(
    ("filename", "content_type", "content", "extension", "mime_type"),
    [
        ("career.txt", "text/plain", b"Product Manager", ".txt", "text/plain"),
        ("career.docx", "application/octet-stream", _docx_bytes(), ".docx",
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("career.pdf", "application/pdf", _pdf_bytes(), ".pdf", "application/pdf"),
    ],
    ids=["txt", "docx", "pdf"],
)
def test_document_upload_validation_accepts_supported_formats(
    filename, content_type, content, extension, mime_type
):
    assert validate_document_upload(filename, content, content_type) == (extension, mime_type)


@pytest.mark.parametrize(
    ("filename", "content_type", "content", "code"),
    [
        ("career.exe", "application/octet-stream", b"not a document", "DOCUMENT_FORMAT_UNSUPPORTED"),
        ("career.pdf", "application/pdf", b"not a pdf", "DOCUMENT_SIGNATURE_INVALID"),
        ("career.txt", "text/plain", b"\xff\xfe", "DOCUMENT_ENCODING_UNSUPPORTED"),
        ("career.txt", "application/pdf", b"text", "DOCUMENT_CONTENT_TYPE_MISMATCH"),
        ("career.txt", "text/plain", b"", "DOCUMENT_EMPTY"),
    ],
)
def test_document_upload_validation_rejects_invalid_files(filename, content_type, content, code):
    with pytest.raises(DomainError) as error:
        validate_document_upload(filename, content, content_type)
    assert error.value.code == code


def test_document_text_extraction_supports_txt_docx_and_pdf():
    assert extract_document_text(".txt", b"Plain text\r\nSecond line") == "Plain text\nSecond line"
    assert "Product Manager" in extract_document_text(".docx", _docx_bytes())
    assert "Example Ltd" in extract_document_text(".pdf", _pdf_bytes())


def test_document_text_extraction_rejects_scanned_or_empty_pdf():
    output = BytesIO()
    pdf = canvas.Canvas(output)
    pdf.showPage()
    pdf.save()
    with pytest.raises(DomainError) as error:
        extract_document_text(".pdf", output.getvalue())
    assert error.value.code == "DOCUMENT_TEXT_NOT_EXTRACTABLE"


def test_document_upload_limit_accepts_exactly_four_mib():
    content = b"a" * (4 * 1024 * 1024)
    assert validate_document_upload("career.txt", content, "text/plain") == (".txt", "text/plain")


def test_document_upload_limit_rejects_one_byte_over_four_mib():
    content = b"a" * (4 * 1024 * 1024 + 1)
    with pytest.raises(DomainError) as error:
        validate_document_upload("career.txt", content, "text/plain")
    assert error.value.code == "DOCUMENT_TOO_LARGE"
    assert error.value.status_code == 413
