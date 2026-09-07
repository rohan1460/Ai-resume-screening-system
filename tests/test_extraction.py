import pytest

from app.services.extraction import (
    CorruptFileError,
    EmptyDocumentError,
    TextLine,
    UnsupportedFileTypeError,
    detect_columns,
    extract_text,
    normalize_text,
    supported_extensions,
)
from tests.factories import (
    SAMPLE_RESUME,
    make_docx_bytes,
    make_pdf_bytes,
    make_right_aligned_pdf_bytes,
    make_table_pdf_bytes,
    make_two_column_pdf_bytes,
)


def line_index(text: str, needle: str) -> int:
    """Index of the line containing ``needle``; -1 when absent."""
    for index, line in enumerate(text.split("\n")):
        if needle in line:
            return index
    return -1


class TestNormalizeText:
    def test_collapses_repeated_spaces(self):
        assert normalize_text("a     b") == "a b"

    def test_strips_per_line_and_caps_blank_runs(self):
        assert normalize_text("  a  \n\n\n\n  b  ") == "a\n\nb"

    def test_normalizes_windows_line_endings(self):
        assert normalize_text("a\r\nb") == "a\nb"

    def test_normalizes_lone_carriage_returns(self):
        assert normalize_text("a\rb") == "a\nb"

    def test_collapses_non_breaking_spaces(self):
        assert normalize_text("a   b") == "a b"


class TestExtractPdf:
    def test_extracts_text(self):
        text = extract_text("resume.pdf", make_pdf_bytes(SAMPLE_RESUME))

        assert "Priya Sharma" in text
        assert "FastAPI" in text

    def test_preserves_reading_order_in_single_column(self):
        text = extract_text("resume.pdf", make_pdf_bytes(SAMPLE_RESUME))

        assert line_index(text, "Priya Sharma") < line_index(text, "SKILLS")
        assert line_index(text, "SKILLS") < line_index(text, "EDUCATION")

    def test_corrupt_bytes_raise(self):
        with pytest.raises(CorruptFileError):
            extract_text("resume.pdf", b"this is definitely not a pdf" * 10)

    def test_truncated_pdf_header_raises(self):
        with pytest.raises(CorruptFileError):
            extract_text("resume.pdf", b"%PDF-1.4 truncated and invalid")


class TestMultiColumnPdf:
    """PyMuPDF returns column-flowed text interleaved; we must restore reading order."""

    @pytest.fixture(scope="class")
    def two_column_text(self) -> str:
        data = make_two_column_pdf_bytes(
            left=[f"LEFT{i} experience item" for i in range(1, 7)],
            right=[f"RIGHT{i} skill item" for i in range(1, 7)],
            header="PRIYA SHARMA - Senior Backend Engineer",
        )
        return extract_text("two_column.pdf", data)

    def test_extracts_all_content_from_both_columns(self, two_column_text):
        for i in range(1, 7):
            assert f"LEFT{i}" in two_column_text
            assert f"RIGHT{i}" in two_column_text

    def test_left_column_is_contiguous_not_interleaved(self, two_column_text):
        positions = [line_index(two_column_text, f"LEFT{i}") for i in range(1, 7)]

        assert positions == sorted(positions)
        # Contiguous: no RIGHT line wedged between the LEFT lines.
        assert positions[-1] - positions[0] == 5

    def test_entire_left_column_precedes_the_right_column(self, two_column_text):
        last_left = line_index(two_column_text, "LEFT6")
        first_right = line_index(two_column_text, "RIGHT1")

        assert last_left < first_right

    def test_full_width_header_comes_first(self, two_column_text):
        assert line_index(two_column_text, "PRIYA SHARMA") == 0

    def test_full_width_footer_separates_column_segments(self):
        data = make_two_column_pdf_bytes(
            left=[f"LEFT{i} experience item" for i in range(1, 6)],
            right=[f"RIGHT{i} skill item" for i in range(1, 6)],
            header="CANDIDATE HEADER LINE",
            footer="REFERENCES AVAILABLE ON REQUEST",
        )

        text = extract_text("two_column.pdf", data)

        assert line_index(text, "CANDIDATE HEADER") < line_index(text, "LEFT1")
        assert line_index(text, "RIGHT5") < line_index(text, "REFERENCES AVAILABLE")

    def test_uneven_columns_still_ordered(self):
        data = make_two_column_pdf_bytes(
            left=[f"LEFT{i} item" for i in range(1, 9)],
            right=[f"RIGHT{i} item" for i in range(1, 6)],
        )

        text = extract_text("uneven.pdf", data)

        assert line_index(text, "LEFT8") < line_index(text, "RIGHT1")


class TestRightAlignedIsNotAColumn:
    """A right-aligned date rail must not be mistaken for a second column."""

    @pytest.fixture(scope="class")
    def dated_text(self) -> str:
        data = make_right_aligned_pdf_bytes(
            entries=[
                ("Senior Engineer, Acme", "Jan 2021 - Present"),
                ("Engineer, Globex", "Jun 2018 - Dec 2020"),
                ("Intern, Initech", "Jan 2018 - May 2018"),
            ],
            intro=["Ravi Kumar", "ravi@example.com", "Backend developer with 6 years."],
        )
        return extract_text("dates.pdf", data)

    def test_each_date_stays_next_to_its_entry(self, dated_text):
        assert line_index(dated_text, "Jan 2021") - line_index(dated_text, "Senior Engineer") == 1
        assert line_index(dated_text, "Jun 2018") - line_index(dated_text, "Engineer, Globex") == 1

    def test_dates_are_not_collected_at_the_end(self, dated_text):
        assert line_index(dated_text, "Jan 2021") < line_index(dated_text, "Engineer, Globex")

    def test_intro_still_comes_first(self, dated_text):
        assert line_index(dated_text, "Ravi Kumar") < line_index(dated_text, "Senior Engineer")


class TestDetectColumns:
    def _lines(self, specs: list[tuple[float, float, float]]) -> list[TextLine]:
        return [TextLine(x0=x0, y0=y, x1=x1, y1=y + 10, text="x") for x0, x1, y in specs]

    def test_no_columns_for_too_few_lines(self):
        lines = self._lines([(50, 150, 10), (50, 150, 30)])

        assert detect_columns(lines, content_width=500) == []

    def test_detects_a_balanced_two_column_layout(self):
        left = [(50, 200, 20 * i) for i in range(6)]
        right = [(320, 470, 20 * i) for i in range(6)]

        bands = detect_columns(self._lines(left + right), content_width=420)

        assert len(bands) == 2

    def test_rejects_a_lopsided_date_rail(self):
        body = [(50, 300, 15 * i) for i in range(20)]
        dates = [(430, 540, 15 * i) for i in range(3)]

        assert detect_columns(self._lines(body + dates), content_width=490) == []

    def test_rejects_zero_content_width(self):
        assert detect_columns(self._lines([(0, 0, 0)]), content_width=0) == []

    def test_single_column_with_varied_indentation(self):
        lines = [(50, 300, 15 * i) for i in range(10)] + [(70, 280, 15 * i) for i in range(10)]

        assert detect_columns(self._lines(lines), content_width=490) == []


class TestPdfTables:
    def test_extracts_table_cells(self):
        data = make_table_pdf_bytes([["Skill", "Years"], ["Python", "7"], ["Kubernetes", "4"]])

        text = extract_text("table.pdf", data)

        assert "Python" in text
        assert "Kubernetes" in text
        assert "7" in text

    def test_keeps_row_cells_together(self):
        data = make_table_pdf_bytes([["Skill", "Years"], ["Python", "7"], ["Kubernetes", "4"]])

        text = extract_text("table.pdf", data)

        assert any("Python" in line and "7" in line for line in text.split("\n"))

    def test_does_not_duplicate_table_content(self):
        data = make_table_pdf_bytes([["Skill", "Years"], ["Kubernetes", "4"], ["Terraform", "3"]])

        text = extract_text("table.pdf", data)

        assert text.count("Kubernetes") == 1

    def test_title_outside_the_table_is_kept(self):
        data = make_table_pdf_bytes([["Skill", "Years"], ["Go", "5"]], title="SKILLS MATRIX")

        text = extract_text("table.pdf", data)

        assert "SKILLS MATRIX" in text


class TestExtractDocx:
    def test_extracts_paragraphs(self):
        text = extract_text("resume.docx", make_docx_bytes(SAMPLE_RESUME))

        assert "Priya Sharma" in text
        assert "PostgreSQL" in text

    def test_extracts_table_cells(self):
        data = make_docx_bytes(
            "Ravi Kumar\nSoftware Engineer with several years of experience.",
            table_rows=[["Skills", "Go, Kubernetes"]],
        )

        text = extract_text("resume.docx", data)

        assert "Kubernetes" in text

    def test_keeps_docx_table_rows_together(self):
        data = make_docx_bytes(
            "Ravi Kumar\nSoftware engineer with plenty of relevant experience.",
            table_rows=[["Python", "7 years"], ["Docker", "4 years"]],
        )

        text = extract_text("resume.docx", data)

        assert any("Python" in line and "7 years" in line for line in text.split("\n"))

    def test_corrupt_bytes_raise(self):
        with pytest.raises(CorruptFileError):
            extract_text("resume.docx", b"not a docx at all")


class TestFailureModes:
    def test_unsupported_extension(self):
        with pytest.raises(UnsupportedFileTypeError, match="Unsupported file type"):
            extract_text("resume.txt", b"plain text resume")

    def test_unsupported_error_names_the_allowed_types(self):
        with pytest.raises(UnsupportedFileTypeError, match=r"\.docx"):
            extract_text("resume.rtf", b"data")

    def test_no_extension(self):
        with pytest.raises(UnsupportedFileTypeError):
            extract_text("resume", b"data")

    def test_empty_file(self):
        with pytest.raises(EmptyDocumentError, match="empty"):
            extract_text("resume.pdf", b"")

    def test_pdf_with_no_text_is_empty_not_success(self):
        """A blank page parses fine but yields nothing — that must not look like success."""
        with pytest.raises(EmptyDocumentError):
            extract_text("resume.pdf", make_pdf_bytes(""))

    def test_docx_with_only_whitespace_is_empty(self):
        with pytest.raises(EmptyDocumentError):
            extract_text("resume.docx", make_docx_bytes("   \n  \n   "))

    def test_text_below_the_meaningful_threshold_is_rejected(self):
        with pytest.raises(EmptyDocumentError, match="scanned images"):
            extract_text("resume.pdf", make_pdf_bytes("Hi"))

    def test_extension_matching_is_case_insensitive(self):
        text = extract_text("RESUME.PDF", make_pdf_bytes(SAMPLE_RESUME))

        assert "Priya Sharma" in text

    def test_errors_share_a_common_base(self):
        from app.services.extraction import ExtractionError

        for error in (UnsupportedFileTypeError, CorruptFileError, EmptyDocumentError):
            assert issubclass(error, ExtractionError)


class TestSupportedExtensions:
    def test_reports_pdf_and_docx(self):
        assert supported_extensions() == {".pdf", ".docx"}


class TestMultiPagePdf:
    def test_reads_every_page_in_order(self):
        import pymupdf

        doc = pymupdf.open()
        for index in range(3):
            page = doc.new_page()
            page.insert_text((50, 100), f"PAGE{index} content for the candidate", fontsize=11)
        data = doc.tobytes()
        doc.close()

        text = extract_text("multi.pdf", data)

        assert line_index(text, "PAGE0") < line_index(text, "PAGE1") < line_index(text, "PAGE2")
