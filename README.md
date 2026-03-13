# Enterprise Knowledge RAG

Enterprise Knowledge RAG is a FastAPI backend for building a private, user-scoped knowledge assistant over enterprise documents and media. It ingests files, stores embeddings in ChromaDB, retrieves relevant context per user, and answers questions with Groq while forcing responses to stay grounded in uploaded material.

## What it does

- User authentication with JWT-based signup and login
- Per-user document collections and chat history
- Retrieval-augmented question answering over uploaded company knowledge
- Streaming answers over Server-Sent Events
- Multimodal ingestion for text, PDFs, slides, images, and audio
- OCR and transcription support for scanned/image and audio inputs

## Good use cases

- Internal policy and handbook Q&A
- Sales enablement over decks, PDFs, and notes
- Knowledge search for operations or support teams
- Contract, SOP, or documentation lookup across uploaded files
- Team-specific assistants where each user keeps their own indexed knowledge base

## Supported file types

The upload pipeline currently supports:

- Text: `txt`
- PDF: `pdf`
- PowerPoint: `pptx`
- Images with OCR: `png`, `jpg`, `jpeg`
- Audio transcription: `mp3`, `wav`, `m4a`, `flac`, `ogg`

## Tech stack

- FastAPI + Uvicorn
- Groq for LLM inference
- Sentence Transformers (`all-MiniLM-L6-v2` by default) for embeddings
- ChromaDB for persistent vector storage
- Tesseract + Poppler for OCR workflows
- Whisper for audio transcription

## Project structure

```text
.
├── docker-compose.yml
└── Backend/
    ├── main.py
    ├── requirements.txt
    ├── Dockerfile
    └── .env   # create this locally
```

## Environment variables

The backend loads environment variables from `Backend/.env`.

Minimum required variable:

```env
GROQ_API_KEY=your_groq_api_key_here
```

Recommended full example:

```env
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile
JWT_SECRET=replace-this-with-a-long-random-secret
CHROMA_DIR=data/chroma
EMBED_MODEL_NAME=all-MiniLM-L6-v2
MIN_SIMILARITY=0.30
MAX_K=6
WHISPER_MODEL=base

# Needed mainly for local Windows setups if OCR/PDF extraction is used
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
POPPLER_PATH=C:\path\to\poppler\Library\bin
```

## Add your Groq API key

1. Create a Groq API key from your Groq account.
2. Create the file `Backend/.env`.
3. Add at least:

```env
GROQ_API_KEY=your_real_key_here
```

4. Set `JWT_SECRET` to a strong random value before using this outside local testing.

The application will fail to start if `GROQ_API_KEY` is missing.

## Run with Docker

This is the easiest path because the container already installs system packages required for OCR and audio processing.

### Prerequisites

- Docker
- Docker Compose

### Start

1. Create `Backend/.env` using the example above.
2. From the repository root, run:

```bash
docker compose up --build
```

3. The API will be available at:

```text
http://localhost:8000
```

4. Health check:

```text
http://localhost:8000/health
```

## Run locally

### Prerequisites

- Python 3.11
- Tesseract OCR
- Poppler
- FFmpeg

### Install Python dependencies

From the `Backend` folder:

```bash
pip install "numpy<2.0"
pip install -r requirements.txt
```

### Start the server

From the `Backend` folder:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Windows note

If you want image OCR or scanned PDF extraction on Windows, set these in `Backend/.env` to your local installs:

- `TESSERACT_CMD`
- `POPPLER_PATH`

If you do not set them correctly, OCR-related uploads may fail even though plain text and standard PDFs still work.

## API workflow

### 1. Sign up

`POST /auth/signup`

Example body:

```json
{
  "username": "alice",
  "password": "strong-password"
}
```

### 2. Log in

`POST /auth/login`

Example body:

```json
{
  "username": "alice",
  "password": "strong-password"
}
```

This returns an `access_token`. Use it as a Bearer token for protected routes.

### 3. Upload files

`POST /upload`

Send a multipart form request with the file and the Bearer token.

### 4. Ask questions

`POST /ask`

Example body:

```json
{
  "question": "What does the onboarding policy say about probation?"
}
```

The backend retrieves the most relevant chunks from the current user's collection and asks Groq to answer only from that context.

### 5. Stream answers

`POST /ask/stream`

Returns an SSE stream for incremental responses.

### 6. View saved chat history

`GET /history`

Returns the authenticated user's saved Q&A history.

## Important behavior

- Data is isolated per user.
- Answers are designed to stay grounded in uploaded content.
- If no relevant context is found, the app refuses rather than fabricating an answer.
- Chat history is persisted per user.
- Vector data is stored on disk through ChromaDB persistence.

## Operational notes

- First startup can be slower because embedding and transcription models may need to download.
- Large local caches, user data, and runtime artifacts are intentionally ignored by git.
- This repository currently contains the backend service only.

## Default runtime settings

Current defaults in the backend:

- `GROQ_MODEL=llama-3.3-70b-versatile`
- `CHROMA_DIR=data/chroma`
- `EMBED_MODEL_NAME=all-MiniLM-L6-v2`
- `MIN_SIMILARITY=0.30`
- `MAX_K=6`
- `WHISPER_MODEL=base`

## Security notes

- Change `JWT_SECRET` before any shared or production deployment.
- Do not commit `Backend/.env`.
- Uploaded enterprise data is stored locally on disk unless you change the storage setup.

## Quick start

```bash
cd Backend
```

Create `Backend/.env`, then from the repository root:

```bash
docker compose up --build
```

Open `http://localhost:8000/docs` for the FastAPI Swagger UI once the server is running.