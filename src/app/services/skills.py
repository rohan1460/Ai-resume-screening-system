"""Skill extraction via spaCy PhraseMatcher over a configurable skills dictionary.

The dictionary supports ``Canonical | alias | alias`` lines so a resume saying
"postgres" and a JD saying "PostgreSQL" resolve to the same canonical skill.

Short aliases are matched **case-sensitively**. Matching "Go", "C" or "R"
case-insensitively would fire on ordinary prose ("go to market", "vitamin c"), which
would silently inflate every candidate's skill score.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import spacy
from spacy.language import Language
from spacy.matcher import PhraseMatcher

from app.core.config import get_settings

# Aliases at or below this length only match with their exact casing.
CASE_SENSITIVE_MAX_LEN = 2

# Longer aliases that are also ordinary English words, or that appear in the boilerplate
# of a job ad. Matching these case-insensitively produces confident nonsense: "we are
# hiring" becomes a required Hiring skill, "slack in the schedule" becomes the chat tool,
# and every candidate is then judged against it.
AMBIGUOUS_ALIASES = frozenset(
    {
        "react",
        "spark",
        "swift",
        "rust",
        "security",
        "communication",
        "slack",
        "notion",
        "sentry",
        "hiring",
        "recruiting",
        "interviewing",
        "flexibility",
        "ownership",
        "collaboration",
        "teamwork",
        "creativity",
        "empathy",
        "negotiation",
        "selenium",
        "cucumber",
    }
)


@dataclass(frozen=True)
class SkillDefinition:
    canonical: str
    aliases: tuple[str, ...]


def parse_skills_file(content: str) -> list[SkillDefinition]:
    """Parse a skills dictionary. Blank lines and ``#`` comments are ignored."""
    definitions: list[SkillDefinition] = []
    seen: set[str] = set()

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [p.strip() for p in line.split("|") if p.strip()]
        if not parts:
            continue

        canonical = parts[0]
        key = canonical.casefold()
        if key in seen:
            continue
        seen.add(key)

        # The canonical form is always matchable, not just its aliases.
        aliases = tuple(dict.fromkeys(parts))
        definitions.append(SkillDefinition(canonical=canonical, aliases=aliases))

    return definitions


def merge_definitions(
    base: list[SkillDefinition], overlay: list[SkillDefinition]
) -> list[SkillDefinition]:
    """Fold an overlay dictionary into a base one.

    An overlay entry whose canonical name already exists *adds* its aliases rather
    than replacing the entry, so a team can teach the matcher their in-house spelling
    of an existing skill without forking the curated list.
    """
    by_key: dict[str, SkillDefinition] = {d.canonical.casefold(): d for d in base}
    order: list[str] = [d.canonical.casefold() for d in base]

    for definition in overlay:
        key = definition.canonical.casefold()
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = definition
            order.append(key)
        else:
            merged = tuple(dict.fromkeys(existing.aliases + definition.aliases))
            by_key[key] = SkillDefinition(canonical=existing.canonical, aliases=merged)

    return [by_key[key] for key in order]


def _resolve_path(path: str | Path) -> Path:
    file_path = Path(path)
    if not file_path.is_absolute():
        # Resolve relative to the project root (three parents up from this file).
        file_path = Path(__file__).resolve().parents[3] / file_path
    return file_path


def load_skill_definitions(
    path: str | Path, extra_path: str | Path | None = None
) -> list[SkillDefinition]:
    """Load the curated dictionary, optionally extended by an overlay file."""
    file_path = _resolve_path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Skills dictionary not found at {file_path}")
    definitions = parse_skills_file(file_path.read_text(encoding="utf-8"))

    if extra_path:
        overlay_path = _resolve_path(extra_path)
        if not overlay_path.exists():
            raise FileNotFoundError(f"Extra skills dictionary not found at {overlay_path}")
        definitions = merge_definitions(
            definitions, parse_skills_file(overlay_path.read_text(encoding="utf-8"))
        )

    return definitions


class SkillExtractor:
    """Finds canonical skills in free text.

    Build this once and reuse it — compiling the matcher over the full dictionary is
    far more expensive than running it.
    """

    def __init__(self, definitions: list[SkillDefinition], nlp: Language | None = None) -> None:
        self._definitions = definitions
        # Only the tokenizer is needed; a blank pipeline keeps matching fast.
        self._nlp = nlp or spacy.blank("en")
        self._canonical_by_key: dict[str, str] = {}

        self._lower_matcher = PhraseMatcher(self._nlp.vocab, attr="LOWER")
        self._exact_matcher = PhraseMatcher(self._nlp.vocab, attr="ORTH")

        for definition in definitions:
            key = definition.canonical.replace(" ", "_")
            self._canonical_by_key[key] = definition.canonical
            lower_patterns = []
            exact_patterns = []
            for alias in definition.aliases:
                target = (
                    exact_patterns
                    if len(alias) <= CASE_SENSITIVE_MAX_LEN or alias.casefold() in AMBIGUOUS_ALIASES
                    else lower_patterns
                )
                target.append(self._nlp.make_doc(alias))
            if lower_patterns:
                self._lower_matcher.add(key, lower_patterns)
            if exact_patterns:
                self._exact_matcher.add(key, exact_patterns)

    @property
    def skill_count(self) -> int:
        return len(self._definitions)

    def extract_set(self, text: str) -> set[str]:
        """Canonical skills present in ``text``."""
        if not text or not text.strip():
            return set()

        doc = self._nlp.make_doc(text)
        found: set[str] = set()
        for matcher in (self._lower_matcher, self._exact_matcher):
            for match_id, _start, _end in matcher(doc):
                key = self._nlp.vocab.strings[match_id]
                found.add(self._canonical_by_key[key])
        return found

    def extract(self, text: str) -> list[str]:
        """Same as :meth:`extract_set`, alphabetically sorted.

        Persistence and API responses use this: JSON has no set type, and a stable
        order keeps stored rows and test assertions deterministic.
        """
        return sorted(self.extract_set(text))


@lru_cache(maxsize=1)
def get_skill_extractor() -> SkillExtractor:
    """Process-wide singleton, built from the configured dictionary path."""
    settings = get_settings()
    return SkillExtractor(
        load_skill_definitions(settings.skills_dictionary_path, settings.extra_skills_path)
    )


def extract_skills(text: str) -> set[str]:
    """Canonical skills found anywhere in ``text``.

    >>> "Python" in extract_skills("Built services in Python and FastAPI.")
    True
    """
    return get_skill_extractor().extract_set(text)


def get_required_skills(jd_text: str) -> set[str]:
    """The skills a job description asks for.

    Same matching as :func:`extract_skills` — a JD is scored against resumes on the
    same vocabulary, so the two must never diverge. Kept as a separate name because
    the caller's intent differs and the JD side may later gain requirement-specific
    handling (for example weighting "must have" above "nice to have").
    """
    return extract_skills(jd_text)
