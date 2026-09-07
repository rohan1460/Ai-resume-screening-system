#!/usr/bin/env bash
# End-to-end demo with plain curl: create a job, poll status, fetch ranked results.
#
#   python scripts/seed.py     # generate seed_data/ first
#   ./scripts/demo.sh          # then run this against a live API
#
# Override the API with:  API=http://localhost:8000 ./scripts/demo.sh

set -euo pipefail

API="${API:-http://localhost:8000}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESUMES="$ROOT/seed_data/resumes"

if [ ! -d "$RESUMES" ]; then
  echo "seed_data/ is missing. Run:  python scripts/seed.py" >&2
  exit 1
fi

echo "==> 1. Health check"
curl -sS "$API/health"
echo -e "\n"

echo "==> 2. Create the screening job"
CREATE=$(curl -sS -X POST "$API/jobs" \
  -F "title=Senior Backend Engineer" \
  -F "jd_text=$(cat "$ROOT/seed_data/job_description.txt")" \
  -F "resumes=@$RESUMES/priya_sharma.pdf" \
  -F "resumes=@$RESUMES/arjun_mehta.docx" \
  -F "resumes=@$RESUMES/sara_khan.pdf" \
  -F "resumes=@$RESUMES/deepak_nair.pdf" \
  -F "resumes=@$RESUMES/ravi_kumar.docx" \
  -F "resumes=@$RESUMES/corrupt_resume.pdf")

echo "$CREATE"
JOB_ID=$(printf '%s' "$CREATE" | sed -E 's/.*"job_id":"([^"]+)".*/\1/')
echo -e "\njob_id = $JOB_ID\n"

echo "==> 3. Poll status until the job finishes"
for _ in $(seq 1 300); do
  STATUS_JSON=$(curl -sS "$API/jobs/$JOB_ID")
  STATE=$(printf '%s' "$STATUS_JSON" | sed -E 's/.*"status":"([^"]+)".*/\1/')
  echo "    $STATUS_JSON"
  case "$STATE" in
    completed) break ;;
    failed)    echo "job failed" >&2; exit 1 ;;
  esac
  sleep 1
done
echo

echo "==> 4. Ranked results"
curl -sS "$API/jobs/$JOB_ID/results" | python3 -m json.tool
echo

echo "==> 5. Re-rank the same candidates against a different JD (cached embeddings)"
curl -sS -X POST "$API/jobs/$JOB_ID/rerank" \
  -H "Content-Type: application/json" \
  -d '{"jd_text":"Frontend Engineer skilled in React, TypeScript, JavaScript, CSS and HTML."}' \
  | python3 -m json.tool | head -40
