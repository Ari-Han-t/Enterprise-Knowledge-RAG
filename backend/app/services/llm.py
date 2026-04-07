import json
from typing import Generator

from groq import Groq

from app.core.config import get_settings


settings = get_settings()


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
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=0,
            max_completion_tokens=80,
            messages=[
                {"role": "system", "content": "Rewrite the user question into a concise retrieval query for academic paper search. Keep named entities, methods, metrics, and constraints. Return only the rewritten query."},
                {"role": "user", "content": question},
            ],
        )
        return (response.choices[0].message.content or question).strip()

    def answer(self, *, question: str, rewritten_query: str, contexts: list[dict]) -> str:
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
        return (response.choices[0].message.content or "").strip()

    def stream_answer(self, *, question: str, rewritten_query: str, contexts: list[dict]) -> Generator[str, None, None]:
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

    def score_answer(self, *, question: str, answer: str, contexts: list[dict]) -> float | None:
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
        content = response.choices[0].message.content or "{\"score\": null}"
        try:
            payload = json.loads(content)
            value = payload.get("score")
            return round(float(value), 4) if value is not None else None
        except Exception:
            return None


def _build_service() -> BaseLLMService:
    if settings.llm_provider == "groq" and settings.groq_api_key:
        return GroqLLMService(api_key=settings.groq_api_key, model=settings.groq_model)
    return FallbackLLMService()


llm_service = _build_service()

