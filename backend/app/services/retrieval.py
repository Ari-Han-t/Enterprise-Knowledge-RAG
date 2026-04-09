import hashlib
import io
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from pypdf import PdfReader
from rank_bm25 import BM25Okapi
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import new_id
from app.models.db import Chunk, Document, QueryCache, QueryLog
from app.rag.chunking import chunk_page_text, tokenize
from app.schemas.rag import Citation, EvaluationSummary


settings = get_settings()


class RetrievalService:
    def warm(self) -> None:
        Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)

    def refusal_message(self) -> str:
        return "I do not have enough paper context to answer that yet. Upload a relevant PDF or ask a more specific question."

    def index_pdf(self, *, db: Session, user_id: str, filename: str, file_bytes: bytes) -> dict:
        pages = self._extract_pdf_pages(file_bytes)
        if not pages:
            raise ValueError(f"No extractable text found in {filename}")

        document = Document(
            user_id=user_id,
            filename=filename,
            sha256=hashlib.sha256(file_bytes).hexdigest(),
            size_bytes=len(file_bytes),
            page_count=len(pages),
            chunk_count=0,
        )
        db.add(document)
        db.flush()

        records: list[Chunk] = []
        for page_number, page_text in pages:
            for item in chunk_page_text(filename=filename, page_number=page_number, text=page_text):
                records.append(
                    Chunk(
                        id=new_id(),
                        user_id=user_id,
                        document_id=document.id,
                        filename=filename,
                        page_number=item["page_number"],
                        chunk_index=item["chunk_index"],
                        token_count=item["token_count"],
                        citation=item["citation"],
                        text=item["text"],
                    )
                )

        if not records:
            raise ValueError(f"No chunks generated for {filename}")

        db.add_all(records)
        document.chunk_count = len(records)
        db.commit()

        upload_path = Path(settings.upload_dir) / user_id
        upload_path.mkdir(parents=True, exist_ok=True)
        (upload_path / filename).write_bytes(file_bytes)

        return {
            "document_id": document.id,
            "filename": filename,
            "page_count": len(pages),
            "chunk_count": len(records),
        }

    def retrieve(self, *, db: Session, user_id: str, query: str) -> dict:
        rows = db.scalars(select(Chunk).where(Chunk.user_id == user_id)).all()
        if not rows:
            return {"hits": []}

        dense_hits = self._dense_search(rows=rows, query=query)
        keyword_hits = self._keyword_search(rows=rows, query=query)
        merged = self._merge_hits(dense_hits=dense_hits, keyword_hits=keyword_hits)
        reranked = self._rerank(query=query, hits=merged)
        return {"hits": reranked[: settings.top_k_final]}

    def build_cache_key(self, *, db: Session, user_id: str, rewritten_query: str) -> str:
        version = db.scalar(select(func.max(Document.created_at)).where(Document.user_id == user_id))
        version_str = version.isoformat() if version else "empty"
        raw = f"{user_id}:{rewritten_query.strip().lower()}:{version_str}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get_cached_answer(self, *, db: Session, cache_key: str) -> dict | None:
        cache = db.get(QueryCache, cache_key)
        if cache is None or cache.expires_at <= datetime.utcnow():
            return None
        return {
            "answer": cache.answer,
            "citations": json.loads(cache.citations_json),
            "retrieved_chunks": json.loads(cache.retrieved_chunks_json),
            "evaluation": json.loads(cache.evaluation_json),
        }

    def persist_answer(
        self,
        *,
        db: Session,
        user_id: str,
        question: str,
        rewritten_query: str,
        answer: str,
        citations: list[Citation],
        retrieval_hits: list[dict],
        evaluation: EvaluationSummary,
        cache_key: str,
        cached: bool,
    ) -> None:
        citations_json = json.dumps([item.model_dump() for item in citations])
        hits_json = json.dumps(retrieval_hits)
        db.add(
            QueryLog(
                user_id=user_id,
                question=question,
                rewritten_query=rewritten_query,
                answer=answer,
                citations_json=citations_json,
                retrieved_chunks_json=hits_json,
                retrieval_score=evaluation.retrieval_score,
                hallucination_score=evaluation.hallucination_risk,
                llm_score=evaluation.answer_score,
                cached=cached,
            )
        )
        db.merge(
            QueryCache(
                cache_key=cache_key,
                user_id=user_id,
                answer=answer,
                citations_json=citations_json,
                retrieved_chunks_json=hits_json,
                evaluation_json=evaluation.model_dump_json(),
                expires_at=datetime.utcnow() + timedelta(seconds=settings.query_cache_ttl_seconds),
            )
        )
        db.commit()

    def _extract_pdf_pages(self, file_bytes: bytes) -> list[tuple[int, str]]:
        reader = PdfReader(io.BytesIO(file_bytes))
        pages: list[tuple[int, str]] = []
        for index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append((index, text))
        return pages

    def _dense_search(self, *, rows: list[Chunk], query: str) -> list[dict]:
        query_vector = self._dense_vector(tokenize(query))
        hits: list[dict] = []
        for row in rows:
            row_vector = self._dense_vector(tokenize(row.text))
            score = self._cosine_similarity(query_vector, row_vector)
            hits.append(
                {
                    "id": row.id,
                    "text": row.text,
                    "filename": row.filename,
                    "page_number": row.page_number,
                    "citation": row.citation,
                    "dense_score": round(score, 4),
                    "keyword_score": 0.0,
                    "score": round(score, 4),
                }
            )
        hits.sort(key=lambda item: item["dense_score"], reverse=True)
        return hits[: settings.top_k_dense]

    def _keyword_search(self, *, rows: list[Chunk], query: str) -> list[dict]:
        corpus = [tokenize(row.text) for row in rows]
        bm25 = BM25Okapi(corpus)
        scores = bm25.get_scores(tokenize(query))
        paired = sorted(zip(rows, scores), key=lambda item: item[1], reverse=True)[: settings.top_k_keyword]
        max_score = max((score for _, score in paired), default=1.0) or 1.0
        hits: list[dict] = []
        for row, score in paired:
            normalized = round(float(score) / max_score, 4) if max_score else 0.0
            hits.append(
                {
                    "id": row.id,
                    "text": row.text,
                    "filename": row.filename,
                    "page_number": row.page_number,
                    "citation": row.citation,
                    "dense_score": 0.0,
                    "keyword_score": normalized,
                    "score": normalized,
                }
            )
        return hits

    def _merge_hits(self, *, dense_hits: list[dict], keyword_hits: list[dict]) -> list[dict]:
        merged: dict[str, dict] = {}
        for rank, hit in enumerate(dense_hits, start=1):
            current = merged.setdefault(hit["id"], hit.copy())
            current["score"] = current.get("score", 0.0) + 1 / (60 + rank)
            current["dense_score"] = max(current.get("dense_score", 0.0), hit["dense_score"])

        for rank, hit in enumerate(keyword_hits, start=1):
            current = merged.setdefault(hit["id"], hit.copy())
            current["score"] = current.get("score", 0.0) + 1 / (60 + rank)
            current["keyword_score"] = max(current.get("keyword_score", 0.0), hit["keyword_score"])

        return list(merged.values())

    def _rerank(self, *, query: str, hits: list[dict]) -> list[dict]:
        if not hits:
            return []

        query_tokens = tokenize(query)
        query_terms = set(query_tokens)
        query_phrases = set(self._ngrams(query_tokens, 2))

        for hit in hits:
            doc_tokens = tokenize(hit["text"])
            doc_terms = set(doc_tokens)
            shared_terms = len(query_terms & doc_terms) / max(len(query_terms), 1)
            phrase_overlap = len(query_phrases & set(self._ngrams(doc_tokens, 2))) / max(len(query_phrases), 1) if query_phrases else 0.0
            exact_bonus = 0.15 if query.lower() in hit["text"].lower() else 0.0
            rerank_score = min((shared_terms * 0.65) + (phrase_overlap * 0.2) + exact_bonus, 1.0)
            hit["rerank_score"] = round(rerank_score, 4)
            hit["score"] = round(
                (hit.get("dense_score", 0.0) * 0.3)
                + (hit.get("keyword_score", 0.0) * 0.25)
                + (hit["score"] * 0.1)
                + (rerank_score * 0.35),
                4,
            )

        return sorted(hits, key=lambda item: item["score"], reverse=True)

    def _dense_vector(self, tokens: list[str]) -> np.ndarray:
        vector = np.zeros(settings.dense_vector_size, dtype=np.float32)
        if not tokens:
            return vector
        counts = Counter(tokens)
        for token, count in counts.items():
            index = self._stable_hash(token) % settings.dense_vector_size
            vector[index] += float(count)
        norm = np.linalg.norm(vector)
        return vector if norm == 0 else vector / norm

    @staticmethod
    def _cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
        if not left.any() or not right.any():
            return 0.0
        return float(np.clip(np.dot(left, right), 0.0, 1.0))

    @staticmethod
    def _stable_hash(value: str) -> int:
        return int(hashlib.sha256(value.encode("utf-8")).hexdigest(), 16)

    @staticmethod
    def _ngrams(tokens: list[str], n: int) -> list[str]:
        if len(tokens) < n:
            return []
        return [" ".join(tokens[index:index + n]) for index in range(len(tokens) - n + 1)]


retrieval_service = RetrievalService()
