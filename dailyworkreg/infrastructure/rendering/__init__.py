"""Producing files for the operator: spreadsheets and printed documents.

The rendering package holds only the work that needs an engine. Everything about what a
document *says* belongs to ``application/export.py``; this layer turns that projection
into a file format and decides nothing about the content.
"""

from .pdf import (
    C_PDF_RENDERER_UNAVAILABLE,
    C_PDF_RENDERING_FAILED,
    RenderError,
    count_pages,
    identification_page_count,
    join_pdfs,
    render_document,
    render_documents,
    without_identification_page,
)

__all__ = [
    "C_PDF_RENDERER_UNAVAILABLE",
    "C_PDF_RENDERING_FAILED",
    "RenderError",
    "count_pages",
    "identification_page_count",
    "join_pdfs",
    "render_document",
    "render_documents",
    "without_identification_page",
]
