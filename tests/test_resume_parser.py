from app.services.resume_parser import (
    extract_education,
    extract_experience,
    extract_name,
    looks_like_job_title,
    looks_like_person_name,
    name_from_filename,
    parse_resume_fields,
)
from tests.factories import SAMPLE_RESUME


class TestNameFromFilename:
    def test_underscores_become_spaces_and_title_case(self):
        assert name_from_filename("priya_sharma.pdf") == "Priya Sharma"

    def test_strips_resume_noise(self):
        assert name_from_filename("ravi_kumar_resume_v2.pdf") == "Ravi Kumar"

    def test_strips_cv_and_year(self):
        assert name_from_filename("anita-desai-CV-2024.docx") == "Anita Desai"

    def test_returns_none_when_nothing_is_left(self):
        assert name_from_filename("resume.pdf") is None
        assert name_from_filename("cv_final_v3.pdf") is None


class TestLooksLikePersonName:
    def test_accepts_a_full_name(self):
        assert looks_like_person_name("Priya Sharma")

    def test_accepts_three_part_and_hyphenated_names(self):
        assert looks_like_person_name("Anita Roy Desai")
        assert looks_like_person_name("Jean-Luc Picard")

    def test_rejects_single_token(self):
        # spaCy tags lone technologies as PERSON; a name needs first and last.
        assert not looks_like_person_name("Docker")
        assert not looks_like_person_name("Java")

    def test_rejects_tokens_with_digits_or_symbols(self):
        assert not looks_like_person_name("Node.js Developer2")
        assert not looks_like_person_name("C++ Engineer")

    def test_rejects_lowercase_start(self):
        assert not looks_like_person_name("python developer")

    def test_rejects_overly_long_phrases(self):
        assert not looks_like_person_name("One Two Three Four Five Six")


class TestLooksLikeJobTitle:
    def test_rejects_common_titles(self):
        for title in (
            "Platform Engineer",
            "Senior Backend Engineer",
            "Data Scientist",
            "Product Manager",
            "Technical Lead",
            "Systems Administrator",
        ):
            assert looks_like_job_title(title), title

    def test_rejects_document_boilerplate(self):
        assert looks_like_job_title("Curriculum Vitae")
        assert looks_like_job_title("Ravi Kumar Resume")

    def test_accepts_real_names(self):
        for name in ("Priya Sharma", "Deepak Nair", "Anita Roy Desai", "Jean-Luc Picard"):
            assert not looks_like_job_title(name), name


class TestExtractName:
    """Regression cover for job titles winning over the real name."""

    def test_prefers_the_header_line_over_a_job_title_below(self):
        text = (
            "Deepak Nair\n"
            "Platform / DevOps Engineer\n"
            "deepak.nair@example.com\n"
            "EXPERIENCE\n"
            "Platform Engineer, Cyberdyne\n"
        )

        assert extract_name(text, "deepak_nair.pdf") == "Deepak Nair"

    def test_does_not_return_a_job_title_as_the_name(self):
        text = "SUMMARY\nExperienced professional.\nEXPERIENCE\nPlatform Engineer, Cyberdyne\n"

        assert extract_name(text, "resume.pdf") != "Platform Engineer"

    def test_works_for_names_spacy_does_not_recognise(self):
        """en_core_web_sm misses many non-Western names; position must still work."""
        text = "Deepak Nair\nPlatform Engineer\ndeepak@example.com"

        assert extract_name(text, None) == "Deepak Nair"

    def test_all_caps_names_are_tidied(self):
        assert extract_name("DEEPAK NAIR\nEngineer", None) == "Deepak Nair"

    def test_skips_curriculum_vitae_banner(self):
        assert extract_name("CURRICULUM VITAE\nRavi Kumar\nEngineer", None) == "Ravi Kumar"

    def test_title_above_the_name_is_skipped(self):
        assert extract_name("Senior Backend Engineer\nAnita Desai", None) == "Anita Desai"

    def test_finds_person_at_top_of_resume(self):
        assert extract_name(SAMPLE_RESUME, "resume.pdf") == "Priya Sharma"

    def test_falls_back_to_filename_when_no_person_found(self):
        text = "SUMMARY\nExperienced engineer building distributed systems at scale."

        assert extract_name(text, "ravi_kumar_resume.pdf") == "Ravi Kumar"

    def test_returns_none_when_both_sources_fail(self):
        assert extract_name("SKILLS\nPython, Docker", "resume.pdf") is None

    def test_ignores_email_lines(self):
        text = "priya.sharma@example.com\nPriya Sharma\nEngineer"

        assert extract_name(text, None) == "Priya Sharma"

    def test_empty_text_with_no_filename(self):
        assert extract_name("", None) is None


class TestExtractEducation:
    def test_finds_degree_and_year(self):
        entries = extract_education(SAMPLE_RESUME)

        assert len(entries) == 1
        assert entries[0]["year"] == 2018
        assert "IIT Bombay" in entries[0]["detail"]

    def test_recognizes_multiple_degrees(self):
        text = (
            "EDUCATION\n"
            "M.Tech in Data Science, IISc Bangalore, 2020\n"
            "B.Sc in Mathematics, Delhi University, 2017\n"
        )

        entries = extract_education(text)

        assert len(entries) == 2
        assert {e["year"] for e in entries} == {2020, 2017}

    def test_handles_degree_without_year(self):
        entries = extract_education("EDUCATION\nMBA, Finance")

        assert len(entries) == 1
        assert entries[0]["year"] is None

    def test_works_without_a_heading(self):
        entries = extract_education("Bachelor of Engineering, Pune University, 2015")

        assert len(entries) == 1

    def test_no_education_returns_empty(self):
        assert extract_education("SKILLS\nPython, Docker") == []

    def test_empty_text(self):
        assert extract_education("") == []


class TestExtractExperience:
    def test_parses_entries_from_sample(self):
        entries = extract_experience(SAMPLE_RESUME)

        assert len(entries) == 2
        assert entries[0]["is_current"] is True
        assert entries[1]["is_current"] is False

    def test_captures_title_from_preceding_line(self):
        entries = extract_experience(
            "EXPERIENCE\nSenior Engineer, Acme\nJan 2021 - Present\nDid things."
        )

        assert entries[0]["title"] == "Senior Engineer, Acme"

    def test_captures_title_on_the_same_line(self):
        entries = extract_experience("EXPERIENCE\nSoftware Engineer | 2018 - 2021")

        assert entries[0]["title"] == "Software Engineer"

    def test_handles_en_dash(self):
        entries = extract_experience("EXPERIENCE\nEngineer\nMar 2019 – Dec 2022")

        assert len(entries) == 1
        assert entries[0]["is_current"] is False

    def test_current_role_variants(self):
        for word in ("Present", "current", "NOW"):
            entries = extract_experience(f"EXPERIENCE\nEngineer\n2020 - {word}")
            assert entries[0]["is_current"] is True, word

    def test_no_experience_returns_empty(self):
        assert extract_experience("SKILLS\nPython") == []

    def test_empty_text(self):
        assert extract_experience("") == []


class TestParseResumeFields:
    def test_returns_all_three_fields(self):
        result = parse_resume_fields(SAMPLE_RESUME, "priya_sharma.pdf")

        assert result["name"] == "Priya Sharma"
        assert len(result["education"]) == 1
        assert len(result["experience"]) == 2

    def test_degrades_gracefully_on_junk(self):
        result = parse_resume_fields("...", "unknown.pdf")

        assert result["education"] == []
        assert result["experience"] == []
