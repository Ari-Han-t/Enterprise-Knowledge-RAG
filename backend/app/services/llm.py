import json
import threading
from typing import Generator

from fastapi import HTTPException, status
from groq import Groq

from app.core.config import get_settings
from app.rag.chunking import estimate_tokens
from app.rate_limit.store import rate_limit_store


settings = get_settings()
groq_semaphore = threading.BoundedSemaphore(value=settings.groq_max_concurrent_requests)


class BaseLLMService:
    def rewrite_query(self, question: str) -> str:
        return question

    def answer(self, *, question: str, rewritten_query: str, contexts: list[dict]) -> str:
        raise NotImplementedError

    def stream_answer(self, *, question: str, rewritten_query: str, contexts: list[dict]) -> Generator[str, None, None]:
        yield self.answer(question=question, rewritten_query=rewritten_query, contexts=contexts)

    def score_answer(self, *, question: str, answer: str, contexts: list[dict]) -> float | None:
        return None

    @staticmethod
    def _context_block(contexts: list[dict]) -> str:
        return "\n\n".join(f"[{item['citation']}]\n{item['text']}" for item in contexts)


class FallbackLLMService(BaseLLMService):
    def answer(self, *, question: str, rewritten_query: str, contexts: list[dict]) -> str:
        top = contexts[:3]
        lines = ["Provider key not configured, returning extractive answer from the most relevant passages."]
        for item in top:
            lines.append(f"- {item['citation']}: {item['text'][:420].strip()}")
        return "\n".join(lines)


class GroqLLMService(BaseLLMService):
    def __init__(self, api_key: str, model: str) -> None:
        self.client = Groq(api_key=api_key)
        self.model = model

    def rewrite_query(self, question: str) -> str:
        self._guard_budget(input_tokens=estimate_tokens(question), output_tokens=80)
        try:
            with self._groq_slot():
                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    max_completion_tokens=80,
                    messages=[
                        {"role": "system", "content": "Rewrite the user question into a concise retrieval query for academic paper search. Keep named entities, methods, metrics, and constraints. Return only the rewritten query."},
                        {"role": "user", "content": question},
                    ],
                )
        except Exception as exc:
            self._raise_provider_error(exc)
        return (response.choices[0].message.content or question).strip()

    def answer(self, *, question: str, rewritten_query: str, contexts: list[dict]) -> str:
        prompt_tokens = estimate_tokens(question) + estimate_tokens(rewritten_query) + estimate_tokens(self._context_block(contexts))
        self._guard_budget(input_tokens=prompt_tokens, output_tokens=settings.max_generation_tokens)
        try:
            with self._groq_slot():
                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0.1,
                    max_completion_tokens=settings.max_generation_tokens,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are an AI research paper analyst. Answer only from the supplied passages. Keep the answer concise, mention uncertainty, and cite every key statement like [paper.pdf p.4].",
                        },
                        {
                            "role": "user",
                            "content": f"Original question:\n{question}\n\nRewritten retrieval query:\n{rewritten_query}\n\nContext passages:\n{self._context_block(contexts)}",
                        },
                    ],
                )
        except Exception as exc:
            self._raise_provider_error(exc)
        return (response.choices[0].message.content or "").strip()

    def stream_answer(self, *, question: str, rewritten_query: str, contexts: list[dict]) -> Generator[str, None, None]:
        prompt_tokens = estimate_tokens(question) + estimate_tokens(rewritten_query) + estimate_tokens(self._context_block(contexts))
        self._guard_budget(input_tokens=prompt_tokens, output_tokens=settings.max_generation_tokens)
        try:
            with self._groq_slot():
                stream = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0.1,
                    max_completion_tokens=settings.max_generation_tokens,
                    stream=True,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are an AI research paper analyst. Answer only from the supplied passages. Keep the answer concise, mention uncertainty, and cite every key statement like [paper.pdf p.4].",
                        },
                        {
                            "role": "user",
                            "content": f"Original question:\n{question}\n\nRewritten retrieval query:\n{rewritten_query}\n\nContext passages:\n{self._context_block(contexts)}",
                        },
                    ],
                )
                for item in stream:
                    if item.choices and item.choices[0].delta.content:
                        yield item.choices[0].delta.content
        except Exception as exc:
            self._raise_provider_error(exc)

    def score_answer(self, *, question: str, answer: str, contexts: list[dict]) -> float | None:
        if not settings.groq_answer_scoring_enabled:
            return None
        prompt_tokens = estimate_tokens(question) + estimate_tokens(answer) + estimate_tokens(self._context_block(contexts))
        self._guard_budget(input_tokens=prompt_tokens, output_tokens=60)
        try:
            with self._groq_slot():
                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    max_completion_tokens=60,
                    response_format={"type": "json_object"},
                    messages=[
                        {
                            "role": "system",
                            "content": "Score the answer quality from 0 to 1 as JSON with key score, based only on factual support from the provided context.",
                        },
                        {
                            "role": "user",
                            "content": f"Question: {question}\n\nAnswer: {answer}\n\nContext:\n{self._context_block(contexts)}",
                        },
                    ],
                )
        except Exception as exc:
            self._raise_provider_error(exc)
        content = response.choices[0].message.content or "{\"score\": null}"
        try:
            payload = json.loads(content)
            value = payload.get("score")
            return round(float(value), 4) if value is not None else None
        except Exception:
            return None

    def _guard_budget(self, *, input_tokens: int, output_tokens: int) -> None:
        estimated_total_tokens = max(input_tokens + output_tokens, 1)
        request_minute = rate_limit_store.hit(
            key="groq:requests:minute",
            limit=settings.groq_requests_per_minute,
            window_seconds=60,
        )
        if not request_minute["allowed"]:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Groq request budget exhausted for this minute. Please retry shortly.",
            )

        request_day = rate_limit_store.hit(
            key="groq:requests:day",
            limit=settings.groq_requests_per_day,
            window_seconds=86400,
        )
        if not request_day["allowed"]:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Groq daily request budget exhausted for this demo.",
            )

        token_minute = rate_limit_store.hit(
            key="groq:tokens:minute",
            limit=settings.groq_tokens_per_minute,
            window_seconds=60,
            amount=estimated_total_tokens,
        )
        if not token_minute["allowed"]:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Groq token budget exhausted for this minute. Please retry shortly.",
            )

        token_day = rate_limit_store.hit(
            key="groq:tokens:day",
            limit=settings.groq_tokens_per_day,
            window_seconds=86400,
            amount=estimated_total_tokens,
        )
        if not token_day["allowed"]:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Groq daily token budget exhausted for this demo.",
            )

    def _groq_slot(self):
        service = self

        class _Slot:
            def __enter__(self_inner):
                acquired = groq_semaphore.acquire(timeout=5)
                if not acquired:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="Groq concurrency budget exhausted. Please retry shortly.",
                    )
                return service

            def __exit__(self_inner, exc_type, exc, tb):
                groq_semaphore.release()

        return _Slot()

    @staticmethod
    def _raise_provider_error(exc: Exception) -> None:
        message = str(exc).lower()
        if "429" in message or "rate limit" in message:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Groq is rate limiting this demo right now. Please retry shortly.",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Groq is temporarily unavailable for this demo.",
        ) from exc


def _build_service() -> BaseLLMService:
    if settings.llm_provider == "groq" and settings.groq_api_key:
        return GroqLLMService(api_key=settings.groq_api_key, model=settings.groq_model)
    return FallbackLLMService()


llm_service = _build_service()
