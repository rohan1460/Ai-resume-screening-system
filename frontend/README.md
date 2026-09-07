# Frontend

React + Vite + TypeScript. Plain CSS, no UI framework — the spec asks for clean and
functional, not fancy.

## Running

```bash
npm install
npm run dev        # http://localhost:5173
```

The API must be running (`uvicorn app.main:app --reload`, or `docker compose up -d api worker`).

In the container it is served by nginx on `http://localhost:3000`:

```bash
docker compose up -d --build frontend
```

## Why there is no API base URL to configure

The app only ever calls `/api/*`. Vite proxies that to `http://localhost:8000` in dev,
and nginx proxies it to the `api` service in the container — both stripping the `/api`
prefix. The browser therefore sees a single origin, so there is no CORS to configure
and no environment-specific URL baked into the bundle.

Point dev at a different backend with `VITE_API_TARGET=http://elsewhere:8000 npm run dev`.

## Layout

```
src/
├── api.ts                  # typed fetch wrapper, FastAPI error unwrapping
├── types.ts                # mirrors src/app/schemas/job.py
├── App.tsx                 # create -> progress -> results
└── components/
    ├── CreateJobForm.tsx   # JD paste/upload + resume drop
    ├── FileDropzone.tsx    # native drag & drop, de-duplicates by name+size
    ├── ProgressView.tsx    # polls GET /jobs/{id}
    ├── ResultsView.tsx     # results + re-rank panel + failures
    ├── ResultsTable.tsx    # sortable table
    └── SkillChips.tsx      # matched/missing chips, collapses long lists
```

## Checks

```bash
npm run build         # tsc -b && vite build
npm run lint          # eslint
npm run format:check  # prettier
```

## Notes

- **Polling uses a chained `setTimeout`, not `setInterval`.** A slow response can never
  stack requests on top of each other.
- **Paste and upload are mutually exclusive.** Filling one disables the other, so the
  request can never carry both and leave the backend to guess.
- **Long skill lists collapse behind "+N more".** A JD with 16 required skills would
  otherwise make every row unreadable.
