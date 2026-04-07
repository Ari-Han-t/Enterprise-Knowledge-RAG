import hashlib
import io
import json
from datetime import datetime, timedelta
from pathlib import Path

import chromadb
from pypdf import PdfReader
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder, SentenceTransformer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import new_id
from app.models.db import Chunk, Document, QueryCache, QueryLog
from app.rag.chunking import chunk_page_text, tokenize
from app.schemas.rag import Citation, EvaluationSummary


settings = get_settings()


class RetrievalService:
    def __init__(self) -> None:
        self._embedder: SentenceTransformer | None = None
        self._reranker: CrossEncoder | None = None
        self._chroma = chromadb.PersistentClient(path=settings.chroma_dir)
        self._collection = self._chroma.get_or_create_collection(
            name="research_chunks",
            metadata={"hnsw:space": "cosine"},
        )

    def warm(self) -> None:
        Path(settings.chroma_dir).mkdir(parents=True, exist_ok=True)

    @property
    def embedder(self) -> SentenceTransformer:
        if self._embedder is None:
            self._embedder = SentenceTransformer(settings.embedding_model_name)
        return self._embedder

    @property
    def reranker(self) -> CrossEncoder:
        if self._reranker is None:
            self._reranker = CrossEncoder(settings.rerank_model_name)
        return self._reranker

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
        texts: list[str] = []
        ids: list[str] = []
        metadatas: list[dict] = []

        for page_number, page_text in pages:
            for item in chunk_page_text(filename=filename, page_number=page_number, text=page_text):
                chunk = Chunk(
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
                records.append(chunk)
                texts.append(item["text"])
                ids.append(chunk.id)
                metadatas.append(
                    {
                        "user_id": user_id,
                        "document_id": document.id,
                        "filename": filename,
                        "page_number": item["page_number"],
                        "citation": item["citation"],
                    }
                )

        if not texts:
            raise ValueError(f"No chunks generated for {filename}")

        embeddings = self.embedder.encode(texts, normalize_embeddings=True).tolist()
        self._collection.add(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)
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
        chunk_count = db.scalar(select(func.count()).select_from(Chunk).where(Chunk.user_id == user_id)) or 0
        if chunk_count == 0:
            return {"hits": []}

        dense_hits = self._dense_search(user_id=user_id, query=query)
        keyword_hits = self._keyword_search(db=db, user_id=user_id, query=query)
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

    def _dense_search(self, *, user_id: str, query: str) -> list[dict]:
        query_vector = self.embedder.encode([query], normalize_embeddings=True).tolist()[0]
        response = self._collection.query(
            query_embeddings=[query_vector],
            n_results=settings.top_k_dense,
            where={"user_id": user_id},
            include=["documents", "metadatas", "distances"],
        )
        docs = response.get("documents", [[]])[0]
        metas = response.get("metadatas", [[]])[0]
        dists = response.get("distances", [[]])[0]
        hits: list[dict] = []
        for index, (doc, meta, distance) in enumerate(zip(docs, metas, dists), start=1):
            if not doc or not meta:
                continue
            score = round(max(0.0, 1 - float(distance)), 4)
            hits.append(
                {
                    "id": f"dense:{meta['document_id']}:{meta['page_number']}:{index}",
                    "text": doc,
                    "filename": meta["filename"],
                    "page_number": int(meta["page_number"]),
                    "citation": meta["citation"],
                    "dense_score": score,
                    "keyword_score": 0.0,
                    "score": score,
                }
            )
        return hits

    def _keyword_search(self, *, db: Session, user_id: str, query: str) -> list[dict]:
        rows = db.scalars(select(Chunk).where(Chunk.user_id == user_id)).all()
        if not rows:
            return []
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
        merged: dict[tuple[str, int, str], dict] = {}
        for rank, hit in enumerate(dense_hits, start=1):
            key = (hit["filename"], hit["page_number"], hit["text"])
            current = merged.setdefault(key, hit.copy())
            current["score"] = current.get("score", 0.0) + 1 / (60 + rank)
            current["dense_score"] = max(current.get("dense_score", 0.0), hit["dense_score"])

        for rank, hit in enumerate(keyword_hits, start=1):
            key = (hit["filename"], hit["page_number"], hit["text"])
            current = merged.setdefault(key, hit.copy())
            current["score"] = current.get("score", 0.0) + 1 / (60 + rank)
            current["keyword_score"] = max(current.get("keyword_score", 0.0), hit["keyword_score"])

        return list(merged.values())

    def _rerank(self, *, query: str, hits: list[dict]) -> list[dict]:
        if not hits:
            return []
        pairs = [[query, hit["text"]] for hit in hits]
        scores = self.reranker.predict(pairs)
        for hit, value in zip(hits, scores):
            hit["rerank_score"] = round(float(value), 4)
            hit["score"] = round((hit.get("score", 0.0) * 0.35) + (float(value) * 0.65), 4)
        return sorted(hits, key=lambda item: item["score"], reverse=True)


retrieval_service = RetrievalService()
