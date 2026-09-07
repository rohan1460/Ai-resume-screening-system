import math

import pytest

from app.services.scoring import (
    ScoreBreakdown,
    compute_skill_score,
    cosine_similarity,
    match_skills,
    normalize_semantic_score,
    rank_breakdowns,
    score_candidate,
)
from app.services.screening import score_resumes
from app.services.skills import extract_skills, get_required_skills


class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_is_magnitude_invariant(self):
        assert cosine_similarity([1.0, 1.0], [5.0, 5.0]) == pytest.approx(1.0)

    def test_known_value(self):
        assert cosine_similarity([1.0, 1.0], [1.0, 0.0]) == pytest.approx(1 / math.sqrt(2))

    def test_zero_vector_is_zero_not_nan(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0

    def test_empty_vectors(self):
        assert cosine_similarity([], []) == 0.0
        assert cosine_similarity([1.0], []) == 0.0

    def test_dimension_mismatch_raises(self):
        with pytest.raises(ValueError, match="dimension mismatch"):
            cosine_similarity([1.0, 2.0], [1.0])


class TestMatchSkills:
    def test_splits_matched_and_missing(self):
        matched, missing = match_skills(["Python", "Go", "Rust"], ["Python", "Java"])

        assert matched == ["Python"]
        assert missing == ["Go", "Rust"]

    def test_is_case_insensitive(self):
        matched, _ = match_skills(["Python"], ["python"])

        assert matched == ["Python"]

    def test_reports_the_jd_spelling(self):
        matched, _ = match_skills(["PostgreSQL"], ["postgresql"])

        assert matched == ["PostgreSQL"]

    def test_extra_candidate_skills_are_ignored(self):
        matched, missing = match_skills(["Python"], ["Python", "Cobol", "Fortran"])

        assert matched == ["Python"]
        assert missing == []

    def test_no_required_skills(self):
        assert match_skills([], ["Python"]) == ([], [])

    def test_preserves_jd_order(self):
        matched, _ = match_skills(["Zsh", "Ansible", "Bash"], ["bash", "zsh", "ansible"])

        assert matched == ["Zsh", "Ansible", "Bash"]


class TestComputeSkillScore:
    def test_all_matched(self):
        assert compute_skill_score(4, 4) == 1.0

    def test_none_matched(self):
        assert compute_skill_score(0, 4) == 0.0

    def test_partial(self):
        assert compute_skill_score(1, 4) == 0.25

    def test_no_required_skills_is_zero_not_division_error(self):
        assert compute_skill_score(0, 0) == 0.0


class TestNormalizeSemanticScore:
    def test_passes_through_in_range(self):
        assert normalize_semantic_score(0.42) == 0.42

    def test_clamps_negative_to_zero(self):
        assert normalize_semantic_score(-0.8) == 0.0

    def test_clamps_above_one(self):
        assert normalize_semantic_score(1.2) == 1.0


class TestScoreCandidate:
    def test_perfect_match_scores_100(self):
        result = score_candidate(
            required_skills=["Python", "Docker"],
            candidate_skills=["Python", "Docker"],
            jd_embedding=[1.0, 0.0],
            candidate_embedding=[1.0, 0.0],
            w_skill=0.6,
            w_semantic=0.4,
        )

        assert result.final_score == pytest.approx(100.0)
        assert result.skill_score == pytest.approx(100.0)
        assert result.semantic_score == pytest.approx(100.0)

    def test_applies_the_configured_weights(self):
        # skill = 1.0, semantic = 0.0  ->  final = w_skill
        result = score_candidate(
            required_skills=["Python"],
            candidate_skills=["Python"],
            jd_embedding=[1.0, 0.0],
            candidate_embedding=[0.0, 1.0],
            w_skill=0.6,
            w_semantic=0.4,
        )

        assert result.final_score == pytest.approx(60.0)

    def test_alternate_weights_change_the_result(self):
        kwargs = {
            "required_skills": ["Python"],
            "candidate_skills": ["Python"],
            "jd_embedding": [1.0, 0.0],
            "candidate_embedding": [0.0, 1.0],
        }

        assert score_candidate(**kwargs, w_skill=0.9, w_semantic=0.1).final_score == pytest.approx(
            90.0
        )
        assert score_candidate(**kwargs, w_skill=0.2, w_semantic=0.8).final_score == pytest.approx(
            20.0
        )

    def test_half_the_skills_half_the_similarity(self):
        result = score_candidate(
            required_skills=["Python", "Go"],
            candidate_skills=["Python"],
            jd_embedding=[1.0, 1.0],
            candidate_embedding=[1.0, 0.0],
            w_skill=0.6,
            w_semantic=0.4,
        )

        expected_semantic = 1 / math.sqrt(2)
        assert result.skill_score == pytest.approx(50.0)
        assert result.semantic_score == pytest.approx(expected_semantic * 100, abs=0.01)
        assert result.final_score == pytest.approx(
            (0.6 * 0.5 + 0.4 * expected_semantic) * 100, abs=0.01
        )

    def test_explains_matched_and_missing(self):
        result = score_candidate(
            required_skills=["Python", "Kubernetes"],
            candidate_skills=["Python"],
            jd_embedding=[1.0],
            candidate_embedding=[1.0],
        )

        assert result.matched_skills == ["Python"]
        assert result.missing_skills == ["Kubernetes"]

    def test_missing_embedding_yields_zero_semantic(self):
        result = score_candidate(
            required_skills=["Python"],
            candidate_skills=["Python"],
            jd_embedding=None,
            candidate_embedding=None,
            w_skill=0.6,
            w_semantic=0.4,
        )

        assert result.semantic_score == 0.0
        assert result.final_score == pytest.approx(60.0)

    def test_negative_similarity_does_not_drag_score_below_skill_component(self):
        result = score_candidate(
            required_skills=["Python"],
            candidate_skills=["Python"],
            jd_embedding=[1.0, 0.0],
            candidate_embedding=[-1.0, 0.0],
            w_skill=0.6,
            w_semantic=0.4,
        )

        assert result.semantic_score == 0.0
        assert result.final_score == pytest.approx(60.0)

    def test_jd_with_no_skills_falls_back_to_semantic_only(self):
        result = score_candidate(
            required_skills=[],
            candidate_skills=["Python"],
            jd_embedding=[1.0, 0.0],
            candidate_embedding=[1.0, 0.0],
            w_skill=0.6,
            w_semantic=0.4,
        )

        assert result.skill_score == 0.0
        assert result.final_score == pytest.approx(40.0)

    def test_uses_settings_weights_by_default(self):
        from app.core.config import get_settings

        settings = get_settings()
        result = score_candidate(
            required_skills=["Python"],
            candidate_skills=["Python"],
            jd_embedding=[1.0, 0.0],
            candidate_embedding=[0.0, 1.0],
        )

        assert result.final_score == pytest.approx(settings.w_skill * 100)

    def test_scores_stay_within_0_to_100(self):
        result = score_candidate(
            required_skills=["Python", "Go", "Rust"],
            candidate_skills=["Python"],
            jd_embedding=[0.3, 0.9],
            candidate_embedding=[0.5, 0.2],
        )

        for value in (result.skill_score, result.semantic_score, result.final_score):
            assert 0.0 <= value <= 100.0


class TestRankBreakdowns:
    def _bd(self, final: float) -> ScoreBreakdown:
        return ScoreBreakdown(skill_score=0.0, semantic_score=0.0, final_score=final)

    def test_ranks_by_descending_score(self):
        ranked = rank_breakdowns({"a": self._bd(50.0), "b": self._bd(90.0), "c": self._bd(70.0)})

        assert ranked["b"].rank == 1
        assert ranked["c"].rank == 2
        assert ranked["a"].rank == 3

    def test_ties_are_deterministic(self):
        first = rank_breakdowns({"b": self._bd(80.0), "a": self._bd(80.0)})
        second = rank_breakdowns({"a": self._bd(80.0), "b": self._bd(80.0)})

        assert first["a"].rank == second["a"].rank == 1
        assert first["b"].rank == second["b"].rank == 2

    def test_preserves_the_breakdown_fields(self):
        original = ScoreBreakdown(
            skill_score=10.0,
            semantic_score=20.0,
            final_score=30.0,
            matched_skills=["Python"],
            missing_skills=["Go"],
        )

        ranked = rank_breakdowns({"a": original})["a"]

        assert ranked.matched_skills == ["Python"]
        assert ranked.missing_skills == ["Go"]
        assert ranked.skill_score == 10.0

    def test_empty_input(self):
        assert rank_breakdowns({}) == {}

    def test_ranks_are_contiguous(self):
        ranked = rank_breakdowns({str(i): self._bd(float(i)) for i in range(5)})

        assert sorted(b.rank for b in ranked.values()) == [1, 2, 3, 4, 5]


class TestScoreResumes:
    """The orchestration layer: score a whole batch and rank it."""

    JD_VECTOR = [1.0, 0.0]

    def _candidates(self) -> dict[str, tuple[list[str], list[float]]]:
        return {
            "strong": (["Python", "Docker"], [1.0, 0.0]),
            "partial": (["Python"], [0.7, 0.7]),
            "weak": (["Excel"], [0.0, 1.0]),
        }

    def test_scores_every_candidate(self):
        result = score_resumes(self.JD_VECTOR, ["Python", "Docker"], self._candidates())

        assert set(result) == {"strong", "partial", "weak"}

    def test_ranks_best_first(self):
        result = score_resumes(self.JD_VECTOR, ["Python", "Docker"], self._candidates())

        assert result["strong"].rank == 1
        assert result["weak"].rank == 3

    def test_preserves_caller_keys(self):
        result = score_resumes(
            self.JD_VECTOR, ["Python"], {"candidate-uuid-123": (["Python"], [1.0, 0.0])}
        )

        assert list(result) == ["candidate-uuid-123"]

    def test_explains_each_candidate(self):
        result = score_resumes(self.JD_VECTOR, ["Python", "Docker"], self._candidates())

        assert result["partial"].matched_skills == ["Python"]
        assert result["partial"].missing_skills == ["Docker"]

    def test_weights_are_threaded_through(self):
        skill_heavy = score_resumes(
            self.JD_VECTOR, ["Python"], self._candidates(), w_skill=1.0, w_semantic=0.0
        )
        semantic_heavy = score_resumes(
            self.JD_VECTOR, ["Python"], self._candidates(), w_skill=0.0, w_semantic=1.0
        )

        assert skill_heavy["strong"].final_score == pytest.approx(100.0)
        assert semantic_heavy["weak"].final_score == pytest.approx(0.0)

    def test_weights_can_flip_the_winner(self):
        candidates = {
            # Has every required skill, but semantically unrelated text.
            "keyword_match": (["Python", "Docker"], [0.0, 1.0]),
            # No listed skills, but semantically very close.
            "semantic_match": ([], [1.0, 0.0]),
        }

        skill_heavy = score_resumes(
            self.JD_VECTOR, ["Python", "Docker"], candidates, w_skill=0.9, w_semantic=0.1
        )
        semantic_heavy = score_resumes(
            self.JD_VECTOR, ["Python", "Docker"], candidates, w_skill=0.1, w_semantic=0.9
        )

        assert skill_heavy["keyword_match"].rank == 1
        assert semantic_heavy["semantic_match"].rank == 1

    def test_candidate_without_an_embedding(self):
        result = score_resumes(self.JD_VECTOR, ["Python"], {"a": (["Python"], None)})

        assert result["a"].semantic_score == 0.0
        assert result["a"].matched_skills == ["Python"]

    def test_missing_jd_embedding_falls_back_to_skills(self):
        result = score_resumes(None, ["Python"], {"a": (["Python"], [1.0, 0.0])})

        assert result["a"].semantic_score == 0.0
        assert result["a"].skill_score == 100.0

    def test_empty_batch(self):
        assert score_resumes(self.JD_VECTOR, ["Python"], {}) == {}

    def test_is_deterministic_across_runs(self):
        first = score_resumes(self.JD_VECTOR, ["Python", "Docker"], self._candidates())
        second = score_resumes(self.JD_VECTOR, ["Python", "Docker"], self._candidates())

        assert {k: v.rank for k, v in first.items()} == {k: v.rank for k, v in second.items()}


class TestExplainabilityEndToEnd:
    """Real skill extraction wired to the scoring maths, with fixed vectors.

    No model download: the embeddings are supplied directly, so the whole
    JD-to-ranking path stays deterministic.
    """

    JD = (
        "Senior Backend Engineer. Requirements: Python, FastAPI, PostgreSQL, "
        "Docker and Kubernetes."
    )
    BACKEND_RESUME = (
        "Backend engineer. Built services in Python with FastAPI, backed by "
        "PostgreSQL, shipped with Docker and Kubernetes."
    )
    FRONTEND_RESUME = "Frontend engineer working in React, TypeScript, CSS and HTML."

    def _score(self, resume: str, resume_vector: list[float]) -> object:
        required = sorted(get_required_skills(self.JD))
        return score_candidate(
            required_skills=required,
            candidate_skills=sorted(extract_skills(resume)),
            jd_embedding=[1.0, 0.0],
            candidate_embedding=resume_vector,
            w_skill=0.6,
            w_semantic=0.4,
        )

    def test_matching_resume_scores_full_marks_on_skills(self):
        result = self._score(self.BACKEND_RESUME, [1.0, 0.0])

        assert result.skill_score == pytest.approx(100.0)
        assert result.missing_skills == []

    def test_unrelated_resume_matches_no_required_skills(self):
        result = self._score(self.FRONTEND_RESUME, [0.0, 1.0])

        assert result.skill_score == 0.0
        assert result.final_score == 0.0

    def test_matched_and_missing_partition_the_requirements(self):
        required = set(get_required_skills(self.JD))
        result = self._score("Python and Docker only.", [1.0, 0.0])

        assert set(result.matched_skills) | set(result.missing_skills) == required
        assert not set(result.matched_skills) & set(result.missing_skills)

    def test_recruiter_can_see_exactly_what_is_missing(self):
        result = self._score("Python, FastAPI and PostgreSQL.", [1.0, 0.0])

        assert set(result.missing_skills) == {"Docker", "Kubernetes"}

    def test_better_candidate_outranks_worse_one(self):
        strong = self._score(self.BACKEND_RESUME, [1.0, 0.0])
        weak = self._score(self.FRONTEND_RESUME, [0.0, 1.0])

        assert strong.final_score > weak.final_score
