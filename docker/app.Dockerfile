# Shared image for the API and the Celery worker — same code, different command.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# CPU-only torch first: the default wheel pulls in several GB of CUDA that a
# MiniLM inference workload never touches.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e .

# Bake the models into the image so containers start offline and never download
# per-process. Model files land in the default HF cache under /root.
RUN python -m spacy download en_core_web_sm \
    && python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

COPY alembic.ini ./
COPY migrations ./migrations
COPY config ./config
COPY scripts ./scripts

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
