from io import BytesIO

import pytest
from docx import Document as DocxDocument

from app.application.documents.rendering import render_document


def test_render_docx_produces_a_formatted_document_with_headings_and_bullets():
    content = {
        "title": "Davids Ezimako",
        "sections": [
            {"heading": "Professional Summary", "body": "Technical product leader."},
            {"heading": "Core Skills", "items": ["Roadmapping", "Delivery leadership"]},
        ],
    }

    data, mime_type, extension = render_document(content, "tailored_cv", "docx")
    document = DocxDocument(BytesIO(data))
    rendered_text = "\n".join(paragraph.text for paragraph in document.paragraphs)

    assert data.startswith(b"PK")
    assert mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert extension == "docx"
    assert "Davids Ezimako" in rendered_text
    assert "Professional Summary" in rendered_text
    assert "Roadmapping" in rendered_text


def test_render_pdf_produces_real_pdf_bytes_and_renders_q_and_a():
    content = {
        "answers": [
            {"question": "Why this role?", "answer": "My experience matches the work."},
            {"question": "What is your approach?", "answer": "I validate assumptions."},
        ]
    }

    data, mime_type, extension = render_document(content, "application_q_and_a", "pdf")

    assert data.startswith(b"%PDF-")
    assert len(data) > 500
    assert mime_type == "application/pdf"
    assert extension == "pdf"


@pytest.mark.parametrize("fmt", ["doc", "txt", "", None])
def test_render_rejects_unsupported_output_formats(fmt):
    with pytest.raises(ValueError, match="output_format must be docx or pdf"):
        render_document({"content": "Text"}, "cover_letter", fmt)


def test_render_rejects_content_without_renderable_text():
    with pytest.raises(ValueError, match="no renderable text"):
        render_document({}, "cover_letter", "docx")
