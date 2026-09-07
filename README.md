# AI Resume Screening System

Explainable resume screening: upload a job description and many resumes, get ranked
candidates with the **matched and missing skills** behind every score.

**Status: Phases 1 and 2 complete; Phase 3 in progress.** Screening runs asynchronously
on Celery workers, the React frontend is wired up, and the API is token-protected.
GitHub Actions CI is still to do.

## How scoring works

```
skill_score    = |matched_skills| / |required_skills|              # keyword layer
semantic_score = cosine_similarity(jd_embedding, resume_embedding) # context layer
final_score    = W_SKILL * skill_score + W_SEMANTIC * semantic_score
```

Weights come from `.env` (`W_SKILL=0.6`, `W_SEMANTIC=0.4`) and must sum to 1.0 — this
is validated at startup. All scores are reported on a 0-100 scale. Both layers are
required: keyword matching alone misses paraphrased experience, embeddings alone miss
hard requirements.

## Layout

```
├── pyproject.toml           # deps, ruff, black, pytest config
├── docker-compose.yml       # postgres 16 (pgvector), redis, minio, api, worker
├── docker/app.Dockerfile    # shared image for api + worker
├── alembic.ini / migrations # schema, incl. pgvector HNSW cosine indexes
├── config/skills.txt        # skills dictionary (editable; supports aliases)
├── seed_data/               # generated sample JD + resumes
├── scripts/seed.py          # generate seed data, optionally submit it
├── src/app/
│   ├── main.py              # FastAPI app factory
│   ├── core/                # settings (Pydantic), structlog setup
│   ├── api/routes/          # health.py, jobs.py
│   ├── db/                  # async session (API) + sync session (worker)
│   ├── models/              # jobs, candidates, scoring_runs, scores
│   ├── schemas/             # request/response models
│   ├── services/            # extraction, skills, resume_parser,
│   │                        # embeddings, scoring, storage, screening
│   └── worker/              # celery_app.py, tasks.py (background screening)
├── frontend/                # React + Vite + TypeScript (see frontend/README.md)
└── tests/
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m spacy download en_core_web_sm    # separate download, not a pip dep
cp .env.example .env
```

## Run

### Everything in containers

```bash
docker compose up -d --build    # postgres, redis, minio, api, worker, frontend
docker compose ps               # wait for everything to report healthy
```

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| API docs | http://localhost:8000/docs |
| MinIO console | http://localhost:9001 |

The first build takes several minutes: it installs CPU-only torch and bakes the spaCy
and sentence-transformers models into the image so containers start offline.

### Running the API and worker locally

Both are needed — with no worker running, jobs stay `queued` forever.

```bash
docker compose up -d postgres redis minio   # infrastructure only
alembic upgrade head

# terminal 1 — the worker
celery -A app.worker.celery_app.celery_app worker --loglevel=info --concurrency=2

# terminal 2 — the API
uvicorn app.main:app --reload
```

And the frontend dev server:

```bash
cd frontend && npm install && npm run dev   # http://localhost:5173
```

Useful worker commands:

```bash
celery -A app.worker.celery_app.celery_app inspect ping     # is a worker alive?
celery -A app.worker.celery_app.celery_app inspect active   # what is running now
celery -A app.worker.celery_app.celery_app inspect stats    # pool size, totals
```

Then generate sample data and screen it end to end:

```bash
python scripts/seed.py --submit
```

```
job_id: 405b15a1-...  status: queued
  status=completed  6/6 resumes  (100%)

Rank  Candidate          Final   Skill  Semantic  Matched
1     Priya Sharma       76.88   81.25     70.31  AWS, Celery, Distributed Systems, Django
2     Sara Khan          48.95   43.75     56.75  AWS, Docker, Machine Learning, NLP
3     Arjun Mehta        42.11   25.00     67.78  Docker, PostgreSQL, Python, SQL
4     Deepak Nair        34.26   25.00     48.15  AWS, Docker, Kubernetes, Redis
5     Ravi Kumar         21.42    0.00     53.55  -

FAILED  corrupt_resume.pdf: Could not read PDF: Failed to open stream
```

## Auth

The job endpoints require a static bearer token; `/health` stays public.

```bash
# generate a key
python -c "import secrets; print(secrets.token_urlsafe(32))"

# put it in .env
API_KEY=<the key>

curl -H "Authorization: Bearer $API_KEY" localhost:8000/jobs/<job_id>
```

Leave `API_KEY` unset for local development and auth is off — but the app **refuses to
start** with `APP_ENV=staging` or `production` and no key, so an unprotected deploy is
not something you can do by forgetting.

A static key rather than JWT: there is no user model here, so a signed token would add
expiry, rotation and clock handling without carrying any claim a shared secret does not.
Comparison is constant-time (`secrets.compare_digest`).

In the browser the key is entered once and kept in that browser's `localStorage`. It is
deliberately not a build-time variable — anything baked into the bundle is readable by
everyone who loads the page.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/jobs` | multipart: `jd_text` or `jd_file`, plus `resumes` files. Returns `job_id`. |
| `GET` | `/jobs/{job_id}` | Status and `processed / total` progress. |
| `GET` | `/jobs/{job_id}/results` | Ranked candidates with score breakdown and skills. |
| `POST` | `/jobs/{job_id}/rerank` | Re-score against a new JD using cached embeddings. |
| `GET` | `/health` | Liveness check. **Public.** |

`POST /jobs` stores the uploads, writes `queued` rows, dispatches a Celery task and
returns — it never waits for processing. Poll `GET /jobs/{id}` for progress; the worker
commits after each resume, so `processed_resumes` climbs while the batch runs.

`POST /jobs/{id}/rerank` re-scores the existing candidates against a new JD using their
cached pgvector embeddings. It never re-parses, never re-reads object storage and never
re-embeds a resume — the only new work is embedding the JD once, so the cost is flat in
the number of candidates. On the sample batch that is ~0.2s against ~1.5s for the
initial screening. Each rerank writes a new `scoring_run`, so earlier rankings stay
queryable.

```bash
curl -X POST localhost:8000/jobs \
  -F "jd_text=Backend engineer with Python, FastAPI, PostgreSQL, Docker" \
  -F "resumes=@seed_data/resumes/priya_sharma.pdf" \
  -F "resumes=@seed_data/resumes/ravi_kumar.docx"

curl localhost:8000/jobs/<job_id>/results

curl -X POST localhost:8000/jobs/<job_id>/rerank \
  -H "Content-Type: application/json" \
  -d '{"jd_text":"Frontend engineer with React, TypeScript, CSS"}'
```

## Tests

```bash
pytest              # 382 tests (backend)
ruff check .
black --check .

cd frontend && npm run build && npm run lint && npm run format:check
```

Integration tests need Postgres running; they create and manage their own
`resume_screening_test` database. Embeddings and object storage are stubbed there for
speed and determinism — the real model is exercised by `scripts/seed.py --submit`.

## Logging

`structlog` throughout; set `LOG_JSON=true` for machine-readable output (the containers
do). Every task log carries `job_id` and `task_id`, so one job's whole lifecycle is a
single filter:

```
job.queued        job_id total_resumes
job.started       job_id total_resumes
resume.processed  job_id filename candidate_id position of skills_found chars_extracted duration_ms
resume.failed     job_id filename candidate_id position of error duration_ms
job.completed     job_id processed failed total duration_ms
job.retrying      job_id error retry_in
job.failed        job_id (with traceback)
```

## Retries

Transient infrastructure faults — `OperationalError`, `StorageError`, botocore errors —
are retried with exponential backoff (`CELERY_RETRY_BACKOFF * 2^attempt`, default 5s →
10s → 20s), up to `CELERY_MAX_RETRIES`. When they run out the job is marked `failed`
with the reason.

A bad *resume* is never retried: it would fail identically every time and burn the
backoff budget. Those are isolated per candidate instead.

## Error handling

A resume that is corrupt, empty, an unsupported type, or has no extractable text is
recorded as a failed candidate with a reason and reported under `failures` in the
results. The rest of the batch still processes and ranks.

## Notes and deviations

- **Object storage vars are `S3_*`, not `MINIO_*`**, so the same config works against
  real AWS S3 by leaving `S3_ENDPOINT` empty. The MinIO container still uses
  `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`, which the image requires.
- **`candidates.name` was added** to the spec's table shape: section 8 shows a
  candidate name in the results table but section 4 defines no such column. It is
  filled from the resume's header line, with spaCy NER and then the filename as
  fallbacks. Position leads because `en_core_web_sm` misses many non-Western names.
- **`scoring_runs` was added** so `/rerank` preserves history instead of overwriting
  the previous ranking.
- **`EMBEDDING_DEVICE` is pinned to `cpu`.** sentence-transformers otherwise selects
  MPS on macOS, and a torch model initialised on MPS crashes as soon as Celery's
  prefork pool forks a child — the worker then respawns children endlessly. Only set
  `mps`/`cuda` with a non-forking pool (`--pool=solo` or `--pool=threads`).
- **The screening task is bound with `@celery_app.task`, not `@shared_task`.** A shared
  task resolves against whatever "current app" exists in the calling process, which in
  the API is Celery's default app pointing at `amqp://` — dispatch then fails trying to
  reach RabbitMQ.
- **Object storage is now load-bearing.** With processing in the worker, uploaded bytes
  live only in MinIO/S3, so an outage at upload time returns `503` rather than accepting
  a job that is certain to fail. A single failed object still fails only that candidate.
- **Rerank runs inline, not through Celery.** It is one embedding call plus a scoring
  pass, so queuing it would add more latency than it saves and force the client to poll
  for something that answers in milliseconds.
- **Deployed environments refuse to start on the dev credentials.** `POSTGRES_PASSWORD`,
  `S3_ACCESS_KEY` and `S3_SECRET_KEY` have local defaults so `docker compose up` needs no
  setup; with `APP_ENV=staging` or `production` those exact values are rejected at
  startup, so a forgotten secret fails loudly instead of silently shipping a known password.
- **The API warms the embedding model at startup** (`WARM_MODELS_ON_STARTUP`). Rerank
  embeds the JD in-process, and loading the model lazily made the first rerank after a
  deploy take ~9s. The tradeoff is that an API replica holds the model in memory
  alongside the workers.
