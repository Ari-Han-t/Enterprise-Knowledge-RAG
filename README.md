# AI Research Paper Analyzer

Production-oriented enterprise RAG SaaS for research-paper analysis. The stack is optimized for low-cost operation, defaults to Groq for generation, keeps the LLM provider swappable, and enforces strict rate limits before expensive work runs.

## What changed

- Refactored the original single-file backend into a modular FastAPI app
- Added JWT auth with isolated per-user document and query history
- Enforced middleware-based rate limiting by user ID and IP
- Restricted uploads to PDFs with a 10MB size cap
- Implemented hybrid retrieval:
  - Lightweight dense retrieval with hashed semantic vectors
  - Keyword search with BM25
  - Lexical reranking tuned for paper QA
- Added query rewriting, cached answers, evaluation logging, and streaming answers
- Added a minimal deployable frontend in `frontend/`
- Updated Docker and compose setup for backend + Redis
- Removed the heavyweight local ML build path from the default deploy image

## Architecture

```text
.
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── models/
│   │   ├── rag/
│   │   ├── rate_limit/
│   │   ├── schemas/
│   │   └── services/
│   ├── .env.example
│   ├── Dockerfile
│   ├── Procfile
│   ├── main.py
│   └── requirements.txt
├── frontend/
│   ├── app.js
│   ├── index.html
│   ├── styles.css
│   └── vercel.json
└── docker-compose.yml
```

## Backend features

- `POST /auth/signup`
- `POST /auth/login`
- `GET /auth/me`
- `POST /upload`
- `POST /ask`
- `POST /ask/stream`
- `GET /history`
- `GET /health`

### Security and cost controls

- JWT-based auth
- User-scoped document isolation
- Middleware-enforced request throttling
- Upload cap: 5 requests per user per day
- Query cap: 10 requests per user per minute
- IP-based throttling in parallel with user throttling
- File size cap: 10MB
- Max question token budget enforced before generation
- Query-result cache to avoid repeated LLM calls
- Redis preferred, in-memory fallback when Redis is unavailable

## Retrieval pipeline

1. PDF text extraction with page-aware citations
2. Token-aware chunking around 500 tokens with overlap
3. Dense retrieval with stable hashed vectors stored in-process
4. BM25 keyword retrieval across the user corpus
5. Reciprocal-rank-style fusion of dense and keyword hits
6. Lightweight lexical reranking with term and phrase overlap
7. Query rewriting before retrieval
8. Citation-grounded answer generation

## Evaluation logging

Each answered query stores:

- original question
- rewritten query
- retrieved chunks
- answer
- citations
- retrieval score
- hallucination-risk heuristic
- optional LLM answer score

## Environment variables

Copy `backend/.env.example` to `backend/.env`.

Important values:

```env
JWT_SECRET=replace-this
DATABASE_URL=sqlite:///./data/app.db
REDIS_URL=redis://redis:6379/0

LLM_PROVIDER=groq
GROQ_API_KEY=your_key_here
GROQ_MODEL=llama-3.1-8b-instant

FRONTEND_ORIGINS=http://localhost:3000,http://localhost:5173
```

No API keys are hardcoded. `.env` is gitignored.

## Local setup

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn main:app --reload
```

### Frontend

The frontend is static. Open `frontend/index.html` directly, or serve it with any static host. Set the backend URL in the UI.

## Docker

```bash
docker compose up --build
```

Backend will start on `http://localhost:8000` and Redis on `localhost:6379`.

## Deploy

### Render or Railway

- Deploy `backend/` as the web service
- Add a persistent disk for `backend/data`
- Set env vars from `backend/.env.example`
- Add Redis and set `REDIS_URL`
- Start command:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

### Vercel

- Deploy the `frontend/` directory as a static site
- In the UI, set the backend URL to the public FastAPI deployment

## Notes

- Groq is the default provider for low-cost inference.
- If `GROQ_API_KEY` is not set, the backend still starts and falls back to extractive, non-LLM answers so the stack can be tested without paid usage.
- SQLite is used by default for low-friction deployment. Move `DATABASE_URL` to Postgres later if you want horizontal scale.
- The default Docker image is intentionally leaner now, so first-time builds should be much faster than the earlier local-transformer stack.
