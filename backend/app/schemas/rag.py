from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=5, max_length=4000)


class Citation(BaseModel):
    filename: str
    page_number: int
    label: str


class EvaluationSummary(BaseModel):
    retrieval_score: float
    hallucination_risk: float
    answer_score: float | None = None


class AskResponse(BaseModel):
    answer: str
    citations: list[Citation]
    rewritten_query: str
    cached: bool
    evaluation: EvaluationSummary
    retrieved_chunks: list[dict[str, Any]]


class UploadResponse(BaseModel):
    message: str
    processed: list[dict[str, Any]]
    rejected: list[dict[str, str]]
    total_chunks: int


class HistoryItem(BaseModel):
    id: int
    question: str
    answer: str
    citations: list[dict[str, Any]]
    score: float | None = None
    created_at: datetime
    cached: bool

