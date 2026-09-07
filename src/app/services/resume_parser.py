"""Structured field extraction from resume text: name, education, experience.

These are heuristics over free text, not guarantees. Every function degrades to an
empty result rather than raising, so a resume with an unusual layout still gets
screened on its skills and semantics.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import spacy
from spacy.language import Language

from app.core.config import get_settings

# Only the first few lines of a resume plausibly hold the candidate's name.
NAME_SEARCH_LINES = 15

# The name sits in the header block; beyond this we are into job titles and summaries.
NAME_HEADER_LINES = 3

# Words that mean "this is a role or a document label", not a person. Checked per
# token, so "Senior Backend Engineer" and "Curriculum Vitae" are both rejected.
NON_NAME_WORDS = frozenset(
    {
        # role words
        "engineer",
        "engineering",
        "developer",
        "programmer",
        "manager",
        "analyst",
        "designer",
        "architect",
        "scientist",
        "consultant",
        "administrator",
        "specialist",
        "officer",
        "executive",
        "director",
        "president",
        "founder",
        "intern",
        "trainee",
        "lead",
        "head",
        "principal",
        "staff",
        "senior",
        "junior",
        "associate",
        # discipline words that pair with them
        "platform",
        "backend",
        "frontend",
        "fullstack",
        "full-stack",
        "devops",
        "software",
        "hardware",
        "data",
        "product",
        "project",
        "technical",
        "systems",
        "security",
        "cloud",
        "mobile",
        "web",
        "qa",
        "sre",
        # document boilerplate
        "curriculum",
        "vitae",
        "resume",
        "cv",
        "profile",
        "contact",
        "portfolio",
    }
)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"[+()\d][\d\s().-]{7,}\d")
_NON_NAME_CHARS = re.compile(r"[^A-Za-z .'-]")

# Filename noise to strip before falling back to it for a name.
_FILENAME_NOISE = re.compile(
    r"\b(resume|cv|curriculum|vitae|final|updated|latest|copy|new|draft|v\d+|\d{4})\b",
    re.IGNORECASE,
)

_DEGREE_PATTERN = re.compile(
    r"\b("
    r"b\.?\s?tech|b\.?\s?e\b|b\.?\s?sc|b\.?\s?a\b|b\.?\s?com|bca|bachelor(?:'s)?|"
    r"m\.?\s?tech|m\.?\s?e\b|m\.?\s?sc|m\.?\s?a\b|mca|mba|master(?:'s)?|"
    r"ph\.?\s?d|doctorate|associate(?:'s)? degree|diploma"
    r")\b",
    re.IGNORECASE,
)

_YEAR = re.compile(r"\b(19|20)\d{2}\b")

_MONTH = (
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
    r"|(?:january|february|march|april|june|july|august|september|october|november|december)"
)
_DATE_RANGE = re.compile(
    rf"((?:{_MONTH})?\.?\s*(?:19|20)\d{{2}})\s*(?:-|–|—|to|until)\s*"
    rf"((?:{_MONTH})?\.?\s*(?:19|20)\d{{2}}|present|current|now)",
    re.IGNORECASE,
)

_SECTION_HEADINGS = {
    "education": re.compile(r"^\s*(education|academic|qualifications?)\b", re.IGNORECASE),
    "experience": re.compile(
        r"^\s*(experience|employment|work history|professional experience|career)\b",
        re.IGNORECASE,
    ),
}
_ANY_HEADING = re.compile(
    r"^\s*(education|academic|qualifications?|experience|employment|work history|"
    r"professional experience|career|skills?|projects?|certifications?|awards?|"
    r"summary|objective|interests?|languages?|publications?|references?)\b\s*:?\s*$",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def get_nlp() -> Language:
    """Full spaCy pipeline (needs NER). Loaded once per process."""
    settings = get_settings()
    return spacy.load(settings.spacy_model)


def name_from_filename(filename: str) -> str | None:
    """Derive a display name from a filename like ``priya_sharma_resume_v2.pdf``."""
    stem = Path(filename).stem
    cleaned = re.sub(r"[_\-.]+", " ", stem)
    cleaned = _FILENAME_NOISE.sub(" ", cleaned)
    cleaned = _NON_NAME_CHARS.sub(" ", cleaned)
    cleaned = " ".join(cleaned.split())
    if not cleaned or len(cleaned) < 2:
        return None
    return " ".join(word.capitalize() for word in cleaned.split())


def _candidate_name_lines(text: str) -> list[str]:
    lines = []
    for raw in text.split("\n")[:NAME_SEARCH_LINES]:
        line = raw.strip()
        if not line or _EMAIL.search(line) or _ANY_HEADING.match(line):
            continue
        # A name line is short and mostly letters.
        if len(line) > 60 or sum(c.isdigit() for c in line) > 2:
            continue
        lines.append(line)
    return lines


def looks_like_person_name(value: str) -> bool:
    """Guard against spaCy tagging a lone skill or product as a PERSON.

    A single capitalised token ("Docker", "Java") is far more likely to be a
    technology on a skills line than a candidate's name, so require at least a first
    and last name, each looking like a word rather than a code fragment.
    """
    tokens = value.split()
    if not 2 <= len(tokens) <= 5:
        return False
    for token in tokens:
        stripped = token.strip(".'-")
        if not stripped or not stripped.replace("'", "").replace("-", "").isalpha():
            return False
    return tokens[0][:1].isupper() and tokens[-1][:1].isupper()


def looks_like_job_title(value: str) -> bool:
    """True for role titles and resume boilerplate.

    spaCy readily tags "Platform Engineer" as a PERSON, and such a line passes
    :func:`looks_like_person_name` on shape alone. Without this check a recruiter is
    shown the candidate's job title where their name should be.
    """
    return any(token.casefold().strip(".,/") in NON_NAME_WORDS for token in value.split())


def _tidy_name(value: str) -> str:
    name = " ".join(value.split())
    # Resumes often set the name in all caps; render it as a name, not a shout.
    return name.title() if name.isupper() else name


def extract_name(text: str, filename: str | None = None) -> str | None:
    """Best-effort candidate name.

    Position is the primary signal: on essentially every resume the name is the first
    real line. spaCy NER is only the fallback, because ``en_core_web_sm`` misses many
    non-Western names — "Deepak Nair" yields no entity at all — and falling through to
    NER then picks up a job title from further down. Leaning on position first keeps
    extraction working the same way regardless of what a candidate is called.
    """
    lines = _candidate_name_lines(text)

    for line in lines[:NAME_HEADER_LINES]:
        candidate = _tidy_name(line)
        if (
            len(candidate) <= 60
            and looks_like_person_name(candidate)
            and not looks_like_job_title(candidate)
        ):
            return candidate

    for line in lines:
        doc = get_nlp()(line)
        for ent in doc.ents:
            if ent.label_ != "PERSON":
                continue
            candidate = _tidy_name(ent.text)
            if (
                len(candidate) <= 60
                and looks_like_person_name(candidate)
                and not looks_like_job_title(candidate)
            ):
                return candidate

    return name_from_filename(filename) if filename else None


def _section_lines(text: str, section: str) -> list[str]:
    """Lines under a section heading, up to the next heading."""
    heading = _SECTION_HEADINGS[section]
    lines = text.split("\n")
    collected: list[str] = []
    inside = False

    for line in lines:
        if heading.match(line):
            inside = True
            continue
        if inside:
            if _ANY_HEADING.match(line):
                break
            if line.strip():
                collected.append(line.strip())
    return collected


def extract_education(text: str) -> list[dict[str, Any]]:
    """Education entries as ``{degree, detail, year}``.

    Searches the education section when present, otherwise the whole document, so a
    resume without headings still yields something.
    """
    lines = _section_lines(text, "education") or text.split("\n")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        match = _DEGREE_PATTERN.search(line)
        if not match:
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)

        year_match = _YEAR.search(line)
        entries.append(
            {
                "degree": match.group(0).strip(),
                "detail": line,
                "year": int(year_match.group(0)) if year_match else None,
            }
        )
    return entries


def extract_experience(text: str) -> list[dict[str, Any]]:
    """Experience entries as ``{title, start, end, is_current}``.

    Anchored on date ranges, which are the most reliably formatted part of an
    experience section; the nearest preceding non-empty line is taken as the title.
    """
    lines = _section_lines(text, "experience") or text.split("\n")
    entries: list[dict[str, Any]] = []

    for index, raw in enumerate(lines):
        line = raw.strip()
        match = _DATE_RANGE.search(line)
        if not match:
            continue

        start, end = match.group(1).strip(), match.group(2).strip()

        # The title usually sits on the same line before the dates, or just above.
        title = line[: match.start()].strip(" ,|-–—")
        if not title:
            for previous in reversed(lines[:index]):
                if previous.strip():
                    title = previous.strip()
                    break

        entries.append(
            {
                "title": title or None,
                "start": start,
                "end": end,
                "is_current": end.casefold() in {"present", "current", "now"},
            }
        )
    return entries


def parse_resume_fields(text: str, filename: str | None = None) -> dict[str, Any]:
    return {
        "name": extract_name(text, filename),
        "education": extract_education(text),
        "experience": extract_experience(text),
    }
