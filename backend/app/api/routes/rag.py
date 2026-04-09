import json
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import get_settings
from app.models.db import QueryLog, User
from app.rag.chunking import estimate_tokens
from app.schemas.rag import AskRequest, AskResponse, Citation, EvaluationSummary, HistoryItem, UploadResponse
from app.services.evaluation import evaluator
from app.services.llm import llm_service
from app.services.retrieval import retrieval_service


router = APIRouter(tags=["rag"])
settings = get_settings()


@router.post("/upload", response_model=UploadResponse)
async def upload_pdf(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UploadResponse:
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one PDF is required")
    if len(files) > settings.max_files_per_upload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Upload at most {settings.max_files_per_upload} file per request for this demo.",
        )

    processed: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    total_chunks = 0

    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            rejected.append({"file": file.filename, "reason": "Only PDF uploads are supported"})
            continue

        data = await file.read()
        if len(data) > settings.max_file_size_bytes:
            rejected.append({"file": file.filename, "reason": "File exceeds the 10MB size limit"})
            continue

        try:
            result = retrieval_service.index_pdf(
                db=db,
                user_id=user.id,
                filename=file.filename,
                file_bytes=data,
            )
        except ValueError as exc:
            rejected.append({"file": file.filename, "reason": str(exc)})
            continue

        total_chunks += result["chunk_count"]
        processed.append(result)

    if not processed and rejected:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"processed": processed, "rejected": rejected})

    return UploadResponse(
        message="Upload completed",
        processed=processed,
        rejected=rejected,
        total_chunks=total_chunks,
    )


@router.post("/ask", response_model=AskResponse)
def ask_question(
    payload: AskRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AskResponse:
    return _run_answer_pipeline(db=db, user=user, payload=payload)


@router.post("/ask/stream")
def ask_question_stream(
    payload: AskRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    if estimate_tokens(payload.question) > settings.max_input_tokens:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Question exceeds max input token budget of {settings.max_input_tokens}",
        )

    cache_key = retrieval_service.build_cache_key(db=db, user_id=user.id, query_text=payload.question)
    cached = retrieval_service.get_cached_answer(db=db, cache_key=cache_key)
    if cached is not None:
        def cached_stream():
            yield f"data: {json.dumps({'type': 'meta', 'cached': True, 'rewritten_query': payload.question, 'citations': cached['citations']})}\n\n"
            yield f"data: {json.dumps({'type': 'chunk', 'text': cached['answer']})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'evaluation': cached['evaluation']})}\n\n"

        return StreamingResponse(cached_stream(), media_type="text/event-stream")

    rewritten_query = llm_service.rewrite_query(payload.question)
    retrieval = retrieval_service.retrieve(db=db, user_id=user.id, query=rewritten_query)
    if not retrieval["hits"]:
        def refusal_stream():
            yield f"data: {json.dumps({'type': 'chunk', 'text': retrieval_service.refusal_message()})}\n\n"
            yield "data: {\"type\":\"done\"}\n\n"

        return StreamingResponse(refusal_stream(), media_type="text/event-stream")

    citations = [Citation(filename=hit["filename"], page_number=hit["page_number"], label=hit["citation"]) for hit in retrieval["hits"]]

    def generate():
        yield f"data: {json.dumps({'type': 'meta', 'cached': False, 'rewritten_query': rewritten_query, 'citations': [c.model_dump() for c in citations]})}\n\n"
        chunks: list[str] = []
        for piece in llm_service.stream_answer(
            question=payload.question,
            rewritten_query=rewritten_query,
            contexts=retrieval["hits"],
        ):
            chunks.append(piece)
            yield f"data: {json.dumps({'type': 'chunk', 'text': piece})}\n\n"

        answer = "".join(chunks).strip() or retrieval_service.refusal_message()
        evaluation = evaluator.evaluate(payload.question, answer, retrieval["hits"])
        retrieval_service.persist_answer(
            db=db,
            user_id=user.id,
            question=payload.question,
            rewritten_query=rewritten_query,
            answer=answer,
            citations=citations,
            retrieval_hits=retrieval["hits"],
            evaluation=evaluation,
            cache_key=cache_key,
            cached=False,
        )
        yield f"data: {json.dumps({'type': 'done', 'evaluation': evaluation.model_dump()})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/history", response_model=list[HistoryItem])
def get_history(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[HistoryItem]:
    rows = db.scalars(
        select(QueryLog).where(QueryLog.user_id == user.id).order_by(desc(QueryLog.created_at)).limit(50)
    ).all()
    return [
        HistoryItem(
            id=row.id,
            question=row.question,
            answer=row.answer,
            citations=json.loads(row.citations_json or "[]"),
            score=row.llm_score,
            created_at=row.created_at,
            cached=row.cached,
        )
        for row in rows
    ]


def _run_answer_pipeline(db: Session, user: User, payload: AskRequest) -> AskResponse:
    if estimate_tokens(payload.question) > settings.max_input_tokens:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Question exceeds max input token budget of {settings.max_input_tokens}",
        )

    cache_key = retrieval_service.build_cache_key(db=db, user_id=user.id, query_text=payload.question)
    cached = retrieval_service.get_cached_answer(db=db, cache_key=cache_key)
    if cached is not None:
        return AskResponse(
            answer=cached["answer"],
            citations=[Citation(**item) for item in cached["citations"]],
            rewritten_query=payload.question,
            cached=True,
            evaluation=EvaluationSummary(**cached["evaluation"]),
            retrieved_chunks=cached["retrieved_chunks"],
        )

    rewritten_query = llm_service.rewrite_query(payload.question)
    retrieval = retrieval_service.retrieve(db=db, user_id=user.id, query=rewritten_query)
    if not retrieval["hits"]:
        return AskResponse(
            answer=retrieval_service.refusal_message(),
            citations=[],
            rewritten_query=rewritten_query,
            cached=False,
            evaluation=EvaluationSummary(retrieval_score=0.0, hallucination_risk=0.0, answer_score=None),
            retrieved_chunks=[],
        )

    answer = llm_service.answer(
        question=payload.question,
        rewritten_query=rewritten_query,
        contexts=retrieval["hits"],
    )
    citations = [Citation(filename=hit["filename"], page_number=hit["page_number"], label=hit["citation"]) for hit in retrieval["hits"]]
    evaluation = evaluator.evaluate(payload.question, answer, retrieval["hits"])

    retrieval_service.persist_answer(
        db=db,
        user_id=user.id,
        question=payload.question,
        rewritten_query=rewritten_query,
        answer=answer,
        citations=citations,
        retrieval_hits=retrieval["hits"],
        evaluation=evaluation,
        cache_key=cache_key,
        cached=False,
    )

    return AskResponse(
        answer=answer,
        citations=citations,
        rewritten_query=rewritten_query,
        cached=False,
        evaluation=evaluation,
        retrieved_chunks=retrieval["hits"],
    )
