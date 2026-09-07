"""Resume text extraction: PDF via PyMuPDF, DOCX via python-docx.

Two layout problems get explicit handling, because both silently corrupt the text
that everything downstream depends on:

**Multi-column PDFs.** PyMuPDF returns text in drawing order, which for a two-column
resume interleaves the columns line by line ("LEFT1 / RIGHT1 / LEFT2 / RIGHT2 ...").
Sentences get shredded and the embedding degrades. We reconstruct reading order from
line bounding boxes: detect a gutter, then read each column top to bottom.

**Tables.** Skills and dates are routinely laid out in tables in both formats; a
paragraph-only read misses them entirely.

Every failure mode the spec calls out (unsupported type, corrupt bytes, empty file,
a file with no extractable text) surfaces as an ``ExtractionError`` subclass so the
caller can mark that one candidate failed and keep processing the batch.
"""

import contextlib
import io
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from docx import Document

_WHITESPACE_RUN = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n{3,}")

# Below this, a "successful" parse is really a scanned/image-only document.
MIN_MEANINGFUL_CHARS = 30

# A line at least this fraction of the content width spans the page (a heading or
# banner) rather than sitting inside one column.
FULL_WIDTH_RATIO = 0.6

# A horizontal gap must be at least this fraction of the content width to count as a
# column gutter rather than ordinary word spacing.
MIN_GUTTER_RATIO = 0.04

# Guards against mistaking right-aligned dates for a second column. A real column
# holds many lines, and the columns are roughly balanced; a date rail is short and
# lopsided, so it stays in single-column order where it belongs.
MIN_LINES_PER_COLUMN = 4
MIN_COLUMN_BALANCE = 0.4


class ExtractionError(Exception):
    """Base class for anything that stops us getting text out of a resume."""


class UnsupportedFileTypeError(ExtractionError):
    pass


class CorruptFileError(ExtractionError):
    pass


class EmptyDocumentError(ExtractionError):
    pass


@dataclass(frozen=True)
class TextLine:
    """One laid-out line of PDF text with its bounding box."""

    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2


def normalize_text(text: str) -> str:
    """Collapse the ragged whitespace PDF extraction tends to produce."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE_RUN.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()


def _bbox_overlaps(
    inner: tuple[float, float, float, float], outer: tuple[float, float, float, float]
) -> bool:
    """True when the centre of ``inner`` falls inside the ``outer`` rect."""
    cx = (inner[0] + inner[2]) / 2
    cy = (inner[1] + inner[3]) / 2
    x0, y0, x1, y1 = outer
    return x0 <= cx <= x1 and y0 <= cy <= y1


def _render_table(rows: list[list[str | None]]) -> str:
    rendered = []
    for row in rows:
        cells = [(cell or "").strip() for cell in row]
        if any(cells):
            rendered.append(" | ".join(cells))
    return "\n".join(rendered)


def _page_tables(page: object) -> list[TextLine]:
    """Tables rendered as pseudo-lines, positioned by their bounding box."""
    try:
        # find_tables prints an advisory banner to stdout; keep it out of the logs.
        with contextlib.redirect_stdout(io.StringIO()):
            finder = page.find_tables()  # type: ignore[attr-defined]
    except Exception:  # table detection is best-effort, never fatal
        return []

    tables = []
    for table in getattr(finder, "tables", []):
        try:
            text = _render_table(table.extract())
        except Exception:
            continue
        if not text.strip():
            continue
        bbox = table.bbox
        tables.append(TextLine(x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3], text=text))
    return tables


def _page_lines(page: object) -> list[TextLine]:
    """Every text line on the page with its bounding box, plus detected tables.

    Lines falling inside a detected table are dropped, so table content appears once
    in structured form rather than twice.
    """
    tables = _page_tables(page)
    lines: list[TextLine] = []

    data = page.get_text("dict")  # type: ignore[attr-defined]
    for block in data.get("blocks", []):
        if block.get("type") != 0:  # skip images
            continue
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", []))
            if not text.strip():
                continue
            bbox = line["bbox"]
            if any(_bbox_overlaps(bbox, (t.x0, t.y0, t.x1, t.y1)) for t in tables):
                continue
            lines.append(TextLine(x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3], text=text))

    return lines + tables


def _merge_intervals(
    intervals: list[tuple[float, float]], tolerance: float
) -> list[tuple[float, float]]:
    if not intervals:
        return []
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + tolerance:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def detect_columns(lines: list[TextLine], content_width: float) -> list[tuple[float, float]]:
    """Detect column bands from line bounding boxes.

    Returns one band per column, left to right, or a single band when the page is not
    genuinely multi-column. Full-width lines are excluded from detection so a banner
    heading does not bridge the gutter and hide the columns.
    """
    if content_width <= 0 or len(lines) < MIN_LINES_PER_COLUMN * 2:
        return []

    columnar = [ln for ln in lines if ln.width < FULL_WIDTH_RATIO * content_width]
    if len(columnar) < MIN_LINES_PER_COLUMN * 2:
        return []

    intervals = sorted((ln.x0, ln.x1) for ln in columnar)
    bands = _merge_intervals(intervals, tolerance=MIN_GUTTER_RATIO * content_width)
    if len(bands) < 2:
        return []

    counts = [sum(1 for ln in columnar if start <= ln.center_x <= end) for start, end in bands]
    if min(counts) < MIN_LINES_PER_COLUMN:
        return []
    # Right-aligned date rails are lopsided; genuine columns are not.
    if min(counts) / max(counts) < MIN_COLUMN_BALANCE:
        return []

    return bands


def _order_lines(lines: list[TextLine], content_width: float) -> list[TextLine]:
    """Sort lines into human reading order, column-aware."""
    if not lines:
        return []

    bands = detect_columns(lines, content_width)
    if not bands:
        return sorted(lines, key=lambda ln: (round(ln.y0, 1), ln.x0))

    def band_of(line: TextLine) -> int:
        for index, (start, end) in enumerate(bands):
            if start <= line.center_x <= end:
                return index
        return len(bands)  # spanning lines sort after the columns in their segment

    # Full-width lines (headings, banners) split the page into horizontal segments;
    # columns are read independently within each segment.
    is_spanning = [ln.width >= FULL_WIDTH_RATIO * content_width for ln in lines]
    ordered_by_y = sorted(
        zip(lines, is_spanning, strict=True), key=lambda pair: (round(pair[0].y0, 1), pair[0].x0)
    )

    result: list[TextLine] = []
    segment: list[TextLine] = []

    def flush() -> None:
        if segment:
            result.extend(sorted(segment, key=lambda ln: (band_of(ln), round(ln.y0, 1), ln.x0)))
            segment.clear()

    for line, spanning in ordered_by_y:
        if spanning:
            flush()
            result.append(line)
        else:
            segment.append(line)
    flush()

    return result


def _extract_pdf(data: bytes) -> str:
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass:
                raise CorruptFileError("PDF is password protected")

            pages: list[str] = []
            for page in doc:
                lines = _page_lines(page)
                if not lines:
                    continue
                content_x0 = min(ln.x0 for ln in lines)
                content_x1 = max(ln.x1 for ln in lines)
                ordered = _order_lines(lines, content_x1 - content_x0)
                pages.append("\n".join(ln.text for ln in ordered))
            return "\n".join(pages)
    except CorruptFileError:
        raise
    except Exception as exc:  # PyMuPDF raises a variety of low-level errors
        raise CorruptFileError(f"Could not read PDF: {exc}") from exc


def _extract_docx(data: bytes) -> str:
    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise CorruptFileError(f"Could not read DOCX: {exc}") from exc

    parts = [p.text for p in document.paragraphs]
    # Skills and dates are routinely laid out in tables; paragraphs alone miss them.
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)


_EXTRACTORS = {".pdf": _extract_pdf, ".docx": _extract_docx}


def supported_extensions() -> set[str]:
    return set(_EXTRACTORS)


def extract_text(filename: str, data: bytes) -> str:
    """Extract normalized plain text from a resume file.

    Raises an ``ExtractionError`` subclass rather than returning empty text, so
    failures are never mistaken for a candidate with nothing to say.
    """
    suffix = Path(filename).suffix.lower()
    extractor = _EXTRACTORS.get(suffix)
    if extractor is None:
        supported = ", ".join(sorted(_EXTRACTORS))
        raise UnsupportedFileTypeError(
            f"Unsupported file type '{suffix or filename}'; expected one of: {supported}"
        )

    if not data:
        raise EmptyDocumentError("File is empty")

    text = normalize_text(extractor(data))
    if len(text) < MIN_MEANINGFUL_CHARS:
        raise EmptyDocumentError(
            f"No meaningful text extracted (got {len(text)} chars); "
            "the document may be scanned images rather than text"
        )
    return text
