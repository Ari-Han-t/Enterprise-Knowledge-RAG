# TrialSight Intelligence

Clinical Trial Evidence Assistant built as a multi-user RAG system for reviewing trial PDFs, retrieving supporting evidence, and generating citation-grounded answers under strict cost and abuse controls.

This is not a generic "chat with PDF" demo. It is a scoped product-style system designed around a real workflow:

- upload trial documents
- isolate data per user
- retrieve relevant evidence
- answer with citations
- enforce hard rate limits before LLM spend happens

## Why It Stands Out

- Niche use case: clinical trial evidence review
- Multi-user architecture with JWT auth and per-user data isolation
- Hybrid retrieval instead of plain keyword search
- Cost-aware by design with Groq as the default LLM provider
- Strict demo-safe rate limiting to protect against abuse and accidental spend
- Lightweight deployment path without heavy local model infrastructure

## Core Capabilities

- PDF upload and ingestion
- Chunked document processing with source-aware citations
- Hybrid retrieval:
  - hashed dense retrieval
  - BM25 keyword search
  - reranking
- Query rewriting before retrieval
- Citation-grounded answer generation
- Streaming responses
- Query history and evaluation logging
- Cache-aware repeated question handling

## Safety and Demo Controls

- JWT authentication
- Per-user document isolation
- Upload size limits
- Upload/day limits
- Query/minute and query/day limits
- Global demo query caps
- Groq request, token, and concurrency guards
- Redis-backed rate limiting with in-memory fallback

## Tech Stack

- FastAPI
- SQLite
- Redis
- Groq
- BM25 + lightweight dense retrieval
- Static frontend
- Docker

## Project Structure

```text
backend/
  app/
    api/
    core/
    models/
    rag/
    rate_limit/
    schemas/
    services/
frontend/
docker-compose.yml
```

## Local Run

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

```bash
cd frontend
py -3 -m http.server 3000
```

Open:

```text
http://localhost:3000/login.html?v=3
```

## Environment

Copy `backend/.env.example` to `backend/.env`.

Important values:

```env
JWT_SECRET=replace-this
DATABASE_URL=sqlite:///./data/app.db
REDIS_URL=redis://redis:6379/0
GROQ_API_KEY=your_key_here
GROQ_MODEL=llama-3.1-8b-instant
```

No API keys are hardcoded. `.env` is ignored by git.

## Docker

```bash
docker compose up --build
```

## Interview Pitch

TrialSight Intelligence is a domain-focused RAG application for clinical evidence review. Instead of building a generic chatbot, this project wraps an LLM in a retrieval, security, and cost-control layer so users can query their own document corpus with grounded answers, citations, persistence, and abuse protection.

In short: it shows product thinking, backend architecture, RAG design, multi-user isolation, and deployment awareness in one project.

## Deployment

Deployment setup and public URL notes will be added here.

