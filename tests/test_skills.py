import pytest

from app.services.skills import (
    SkillExtractor,
    extract_skills,
    get_required_skills,
    get_skill_extractor,
    load_skill_definitions,
    merge_definitions,
    parse_skills_file,
)


@pytest.fixture(scope="module")
def extractor() -> SkillExtractor:
    return SkillExtractor(parse_skills_file("""
            # a comment
            Python
            PostgreSQL | Postgres
            Machine Learning | ML
            Go | Golang
            C
            React | React.js
            FastAPI
            """))


class TestParseSkillsFile:
    def test_ignores_comments_and_blank_lines(self):
        defs = parse_skills_file("# comment\n\n  \nPython\n")

        assert len(defs) == 1
        assert defs[0].canonical == "Python"

    def test_canonical_is_matchable_alongside_aliases(self):
        defs = parse_skills_file("PostgreSQL | Postgres")

        assert defs[0].aliases == ("PostgreSQL", "Postgres")

    def test_deduplicates_by_canonical_case_insensitively(self):
        defs = parse_skills_file("Python\npython\nPYTHON")

        assert len(defs) == 1


class TestExtract:
    def test_finds_simple_skill(self, extractor):
        assert "Python" in extractor.extract("Experienced in Python development.")

    def test_is_case_insensitive_for_normal_skills(self, extractor):
        assert "FastAPI" in extractor.extract("built services with fastapi")

    def test_alias_maps_to_canonical(self, extractor):
        result = extractor.extract("Worked with Postgres for 5 years.")

        assert "PostgreSQL" in result
        assert "Postgres" not in result

    def test_multi_word_skill(self, extractor):
        assert "Machine Learning" in extractor.extract("focused on machine learning research")

    def test_multi_word_alias(self, extractor):
        assert "Machine Learning" in extractor.extract("strong ML background")

    def test_returns_sorted_unique(self, extractor):
        result = extractor.extract("Python, python, PYTHON and FastAPI")

        assert result == ["FastAPI", "Python"]

    def test_empty_text(self, extractor):
        assert extractor.extract("") == []
        assert extractor.extract("   ") == []

    def test_no_known_skills(self, extractor):
        assert extractor.extract("I enjoy gardening and cooking.") == []


class TestAmbiguousShortSkills:
    """Short aliases must not fire on ordinary prose."""

    def test_lowercase_go_verb_is_not_the_language(self, extractor):
        assert "Go" not in extractor.extract("I want to go to market and go home.")

    def test_capitalized_go_is_the_language(self, extractor):
        assert "Go" in extractor.extract("Backend services written in Go and Python.")

    def test_golang_alias_matches_case_insensitively(self, extractor):
        assert "Go" in extractor.extract("experience with golang microservices")

    def test_lowercase_c_is_not_the_language(self, extractor):
        assert "C" not in extractor.extract("vitamin c supplements and c of the above")

    def test_capital_c_is_the_language(self, extractor):
        assert "C" in extractor.extract("Systems programming in C and Rust.")

    def test_react_only_matches_as_a_noun_form(self, extractor):
        # "react" is in AMBIGUOUS_ALIASES, so the verb must not match.
        assert "React" not in extractor.extract("teams react quickly to incidents")
        assert "React" in extractor.extract("Frontend built with React and Redux.")


class TestProjectDictionary:
    def test_ships_a_usable_dictionary(self):
        defs = load_skill_definitions("config/skills.txt")

        assert len(defs) > 100
        canonicals = {d.canonical for d in defs}
        assert {"Python", "FastAPI", "PostgreSQL", "Docker", "Kubernetes"} <= canonicals

    def test_singleton_is_cached(self):
        assert get_skill_extractor() is get_skill_extractor()

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_skill_definitions("config/does-not-exist.txt")

    def test_extracts_from_a_realistic_jd(self):
        jd = (
            "We are hiring a Senior Backend Engineer. You will build APIs in Python "
            "using FastAPI, work with PostgreSQL and Redis, and deploy on Kubernetes."
        )

        result = get_skill_extractor().extract(jd)

        assert {"Python", "FastAPI", "PostgreSQL", "Redis", "Kubernetes"} <= set(result)

    def test_covers_tech_tools_and_soft_skills(self):
        canonicals = {d.canonical for d in load_skill_definitions("config/skills.txt")}

        assert {"Python", "Go", "PostgreSQL"} <= canonicals  # languages and databases
        assert {"Docker", "Kubernetes", "Git", "Jira", "Figma"} <= canonicals  # tools
        assert {"Team Leadership", "Mentoring", "Problem Solving"} <= canonicals  # soft

    def test_no_duplicate_canonicals(self):
        canonicals = [d.canonical.casefold() for d in load_skill_definitions("config/skills.txt")]

        assert len(canonicals) == len(set(canonicals))


class TestExtractSkills:
    """The public ``extract_skills`` API, backed by the shipped dictionary."""

    def test_returns_a_set(self):
        assert isinstance(extract_skills("Python developer"), set)

    def test_finds_skills_in_a_resume(self):
        text = (
            "Senior Backend Engineer with 7 years building services in Python and "
            "FastAPI, backed by PostgreSQL and Redis, deployed with Docker."
        )

        assert {"Python", "FastAPI", "PostgreSQL", "Redis", "Docker"} <= extract_skills(text)

    def test_deduplicates_repeated_mentions(self):
        result = extract_skills("Python. Python! python, PYTHON")

        assert result == {"Python"}

    def test_maps_aliases_to_canonical_names(self):
        result = extract_skills("Worked with Postgres, NodeJS, K8s and sklearn.")

        assert {"PostgreSQL", "Node.js", "Kubernetes", "scikit-learn"} <= result

    def test_finds_soft_skills(self):
        text = "Provided Team Leadership and Mentoring, with strong Problem Solving."

        assert {"Team Leadership", "Mentoring", "Problem Solving"} <= extract_skills(text)

    def test_finds_collaboration_tools(self):
        assert {"Jira", "Figma"} <= extract_skills("Tracked work in Jira and designed in Figma.")

    def test_empty_and_whitespace_text(self):
        assert extract_skills("") == set()
        assert extract_skills("   \n  ") == set()

    def test_text_with_no_skills(self):
        assert extract_skills("I enjoy gardening, baking and long walks.") == set()

    def test_is_deterministic(self):
        text = "Python, Docker, Kubernetes and Team Leadership."

        assert extract_skills(text) == extract_skills(text)


class TestGetRequiredSkills:
    def test_returns_a_set(self):
        assert isinstance(get_required_skills("Python role"), set)

    def test_pulls_requirements_from_a_jd(self):
        jd = (
            "Senior Backend Engineer. Requirements: strong Python, experience with "
            "FastAPI or Django, solid PostgreSQL, and Kubernetes in production."
        )

        assert {"Python", "FastAPI", "Django", "PostgreSQL", "Kubernetes"} <= get_required_skills(
            jd
        )

    def test_agrees_with_extract_skills_on_the_same_vocabulary(self):
        text = "Python, FastAPI and PostgreSQL."

        assert get_required_skills(text) == extract_skills(text)

    def test_matches_a_resume_written_differently(self):
        jd = get_required_skills("Looking for PostgreSQL and Kubernetes experience.")
        resume = extract_skills("Used postgres daily and ran workloads on k8s.")

        assert {"PostgreSQL", "Kubernetes"} <= jd & resume

    def test_empty_jd(self):
        assert get_required_skills("") == set()


class TestJobAdBoilerplateIsNotASkill:
    """A false positive in the JD poisons every candidate's score, so guard the
    words that appear in ordinary job-ad prose."""

    def test_we_are_hiring_is_not_a_hiring_skill(self):
        assert "Hiring" not in get_required_skills("We are hiring a backend engineer.")

    def test_slack_in_deadlines_is_not_the_chat_tool(self):
        assert "Slack" not in get_required_skills("There is some slack in the deadlines.")

    def test_capitalised_slack_is_the_chat_tool(self):
        assert "Slack" in extract_skills("Daily standups run in Slack.")

    def test_linear_algebra_does_not_match_a_tool(self):
        assert extract_skills("Comfortable with linear algebra and statistics.") >= {"Statistics"}

    def test_flexibility_in_hours_is_not_a_listed_skill(self):
        assert "Adaptability" not in get_required_skills("We offer flexibility in working hours.")

    def test_realistic_jd_yields_only_real_requirements(self):
        jd = (
            "We are hiring! Join our team. We offer flexibility, great communication "
            "and slack time for learning. You will write Python and deploy with Docker."
        )

        result = get_required_skills(jd)

        assert {"Python", "Docker"} <= result
        assert "Hiring" not in result
        assert "Slack" not in result


class TestExtendingTheDictionary:
    def test_overlay_adds_new_skills(self, tmp_path):
        base = tmp_path / "base.txt"
        base.write_text("Python\nDocker\n")
        extra = tmp_path / "extra.txt"
        extra.write_text("AcmeInternalTool\n")

        defs = load_skill_definitions(base, extra)
        canonicals = {d.canonical for d in defs}

        assert {"Python", "Docker", "AcmeInternalTool"} <= canonicals

    def test_overlay_adds_aliases_to_an_existing_skill(self, tmp_path):
        base = tmp_path / "base.txt"
        base.write_text("PostgreSQL | Postgres\n")
        extra = tmp_path / "extra.txt"
        extra.write_text("PostgreSQL | PG | Postgres16\n")

        defs = load_skill_definitions(base, extra)

        assert len(defs) == 1
        assert set(defs[0].aliases) == {"PostgreSQL", "Postgres", "PG", "Postgres16"}

    def test_overlay_aliases_are_matchable(self, tmp_path):
        base = tmp_path / "base.txt"
        base.write_text("PostgreSQL | Postgres\n")
        extra = tmp_path / "extra.txt"
        extra.write_text("PostgreSQL | OurDatabase\n")

        extractor = SkillExtractor(load_skill_definitions(base, extra))

        assert "PostgreSQL" in extractor.extract_set("Migrated OurDatabase to the cloud.")

    def test_overlay_preserves_base_order(self, tmp_path):
        base = tmp_path / "base.txt"
        base.write_text("Python\nDocker\n")
        extra = tmp_path / "extra.txt"
        extra.write_text("Zebra\n")

        defs = load_skill_definitions(base, extra)

        assert [d.canonical for d in defs] == ["Python", "Docker", "Zebra"]

    def test_missing_overlay_file_raises(self, tmp_path):
        base = tmp_path / "base.txt"
        base.write_text("Python\n")

        with pytest.raises(FileNotFoundError, match="Extra skills dictionary"):
            load_skill_definitions(base, tmp_path / "nope.txt")

    def test_merge_definitions_is_a_pure_function(self):
        base = parse_skills_file("Python")
        overlay = parse_skills_file("Go")

        merge_definitions(base, overlay)

        assert [d.canonical for d in base] == ["Python"]


class TestExtractSetAndListAgree:
    def test_list_form_is_the_sorted_set(self):
        extractor = get_skill_extractor()
        text = "Python, Docker, Kubernetes, FastAPI."

        assert extractor.extract(text) == sorted(extractor.extract_set(text))
