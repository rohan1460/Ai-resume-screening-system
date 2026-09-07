#!/usr/bin/env python
"""Generate sample seed data for manual testing (build order item 7).

    python scripts/seed.py            # write seed_data/ files
    python scripts/seed.py --submit   # ...and POST them to a running API

The resumes are deliberately varied: a strong backend match, a partial match, a
frontend engineer who should rank last on a backend JD, and a deliberately corrupt
file to exercise per-resume failure isolation.
"""

import argparse
import sys
import time
from pathlib import Path

import pymupdf
from docx import Document

ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = ROOT / "seed_data"
RESUME_DIR = SEED_DIR / "resumes"

JOB_DESCRIPTION = """Senior Backend Engineer

We are looking for a Senior Backend Engineer to design and build the services behind
our hiring platform.

Requirements:
- Strong Python experience, ideally with FastAPI or Django
- Solid SQL and PostgreSQL knowledge
- Experience with Redis and Celery for async task processing
- Docker and Kubernetes in production
- Familiarity with AWS
- Good testing discipline with pytest
- Comfortable with System Design and Distributed Systems

Nice to have:
- Machine Learning or NLP exposure
- Experience with Elasticsearch
"""

RESUMES: dict[str, str] = {
    "priya_sharma.pdf": """Priya Sharma
Senior Backend Engineer
priya.sharma@example.com | +91 98765 43210

SUMMARY
Backend engineer with 7 years building distributed systems and data platforms.

SKILLS
Python, FastAPI, Django, PostgreSQL, Redis, Celery, Docker, Kubernetes, AWS,
pytest, System Design, Distributed Systems, SQL, Git

EXPERIENCE
Senior Backend Engineer, Acme Corp
Jan 2021 - Present
Led the payments platform handling 2M requests per day. Introduced Celery based
async processing and cut p99 latency by 40 percent.

Backend Engineer, Globex
Jun 2018 - Dec 2020
Built internal REST APIs in Django and migrated batch jobs onto Kubernetes.

EDUCATION
B.Tech in Computer Science, IIT Bombay, 2018
""",
    "arjun_mehta.docx": """Arjun Mehta
Backend Developer
arjun.mehta@example.com

SUMMARY
Backend developer with 4 years of experience in Python web services.

SKILLS
Python, Flask, PostgreSQL, Docker, SQL, Git, Unit Testing

EXPERIENCE
Backend Developer, Initech
Mar 2021 - Present
Maintained Flask services and PostgreSQL schemas for a logistics product.

Junior Developer, Hooli
Jul 2019 - Feb 2021
Wrote internal tooling and reporting scripts.

EDUCATION
B.E in Information Technology, Pune University, 2019
""",
    "sara_khan.pdf": """Sara Khan
Machine Learning Engineer
sara.khan@example.com

SUMMARY
ML engineer focused on NLP systems in production.

SKILLS
Python, PyTorch, spaCy, Hugging Face, Machine Learning, NLP, Docker, AWS,
PostgreSQL, Airflow, pytest

EXPERIENCE
Machine Learning Engineer, Umbrella AI
Aug 2020 - Present
Shipped document classification and entity extraction models serving 500k docs
per month.

Data Scientist, Stark Industries
Jul 2018 - Jul 2020
Built forecasting models and data pipelines.

EDUCATION
M.Tech in Data Science, IISc Bangalore, 2018
""",
    "deepak_nair.pdf": """Deepak Nair
Platform / DevOps Engineer
deepak.nair@example.com

SUMMARY
Platform engineer running Kubernetes clusters and CI/CD for product teams.

SKILLS
Docker, Kubernetes, Terraform, AWS, Linux, Redis, Prometheus, Grafana, Bash,
GitHub Actions, CI/CD, Git

EXPERIENCE
Platform Engineer, Cyberdyne
Apr 2020 - Present
Ran multi-tenant Kubernetes clusters on AWS and cut deploy times by half.

Systems Administrator, Soylent Corp
Sep 2017 - Mar 2020
Managed Linux fleets and monitoring.

EDUCATION
B.Tech in Electronics, NIT Trichy, 2017
""",
    "ravi_kumar.docx": """Ravi Kumar
Frontend Engineer
ravi.kumar@example.com

SUMMARY
Frontend engineer specialising in design systems.

SKILLS
JavaScript, TypeScript, React, Next.js, Redux, CSS, HTML, Tailwind CSS, Jest, Git

EXPERIENCE
Frontend Engineer, Widgets Inc
Feb 2020 - Present
Built and maintained a component library used by 12 product teams.

UI Developer, Pied Piper
Jan 2018 - Jan 2020
Implemented responsive interfaces and improved accessibility.

EDUCATION
B.Sc in Design, Pune University, 2017
""",
}


def write_pdf(path: Path, text: str) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 550, 780), text, fontsize=10)
    doc.save(path)
    doc.close()


def write_docx(path: Path, text: str) -> None:
    document = Document()
    for line in text.split("\n"):
        document.add_paragraph(line)
    document.save(path)


def generate() -> list[Path]:
    RESUME_DIR.mkdir(parents=True, exist_ok=True)
    jd_path = SEED_DIR / "job_description.txt"
    jd_path.write_text(JOB_DESCRIPTION, encoding="utf-8")

    written = [jd_path]
    for filename, content in RESUMES.items():
        path = RESUME_DIR / filename
        if filename.endswith(".pdf"):
            write_pdf(path, content)
        else:
            write_docx(path, content)
        written.append(path)

    # Exercises per-resume failure isolation: valid extension, invalid bytes.
    corrupt = RESUME_DIR / "corrupt_resume.pdf"
    corrupt.write_bytes(b"%PDF-1.4 this file is intentionally truncated and invalid")
    written.append(corrupt)

    return written


def poll_until_done(client, api_url: str, job_id: str, timeout: float = 300.0) -> dict | None:
    """Poll GET /jobs/{id} until the job leaves a running state.

    Phase 1 is synchronous, so this returns on the first poll. It is written against
    the polling contract anyway, so the same script keeps working once Phase 2 moves
    processing into Celery and POST starts returning ``queued``.
    """
    deadline = time.monotonic() + timeout
    last_progress = -1.0

    while time.monotonic() < deadline:
        status = client.get(f"{api_url}/jobs/{job_id}").json()
        if status["progress"] != last_progress:
            last_progress = status["progress"]
            print(
                f"  status={status['status']:<10} "
                f"{status['processed_resumes'] + status['failed_resumes']}"
                f"/{status['total_resumes']} resumes"
                f"  ({status['progress'] * 100:.0f}%)"
            )
        if status["status"] in {"completed", "failed"}:
            if status["status"] == "failed":
                print(f"Job failed: {status['error']}", file=sys.stderr)
                return None
            return status
        time.sleep(1.0)

    print(f"Timed out after {timeout}s waiting for job {job_id}", file=sys.stderr)
    return None


def submit(api_url: str) -> int:
    import httpx

    files = [
        ("resumes", (p.name, p.read_bytes(), "application/octet-stream"))
        for p in sorted(RESUME_DIR.iterdir())
    ]
    data = {"jd_text": JOB_DESCRIPTION, "title": "Senior Backend Engineer"}

    with httpx.Client(timeout=300.0) as client:
        created = client.post(f"{api_url}/jobs", data=data, files=files)
        if created.status_code != 201:
            print(f"Job creation failed: {created.status_code} {created.text}", file=sys.stderr)
            return 1

        job_id = created.json()["job_id"]
        print(f"job_id: {job_id}  status: {created.json()['status']}")

        status = poll_until_done(client, api_url, job_id)
        if status is None:
            return 1

        results = client.get(f"{api_url}/jobs/{job_id}/results").json()

    print(f"\nRequired skills from JD: {', '.join(results['required_skills'])}")
    print(f"Processed: {results['processed_resumes']}  Failed: {results['failed_resumes']}\n")
    print(f"{'Rank':<5} {'Candidate':<16} {'Final':>7} {'Skill':>7} {'Semantic':>9}  Matched")
    print("-" * 78)
    for row in results["results"]:
        matched = ", ".join(row["matched_skills"][:4]) or "-"
        print(
            f"{row['rank']:<5} {(row['name'] or row['filename'])[:15]:<16} "
            f"{row['final_score']:>7.2f} {row['skill_score']:>7.2f} "
            f"{row['semantic_score']:>9.2f}  {matched}"
        )
    for failure in results["failures"]:
        print(f"\nFAILED  {failure['filename']}: {failure['error']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submit", action="store_true", help="POST the seed data to the API")
    parser.add_argument("--api-url", default="http://localhost:8000")
    args = parser.parse_args()

    for path in generate():
        print(f"wrote {path.relative_to(ROOT)}")

    if args.submit:
        print(f"\nSubmitting to {args.api_url} ...\n")
        return submit(args.api_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
