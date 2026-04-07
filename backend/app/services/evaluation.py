from app.rag.chunking import tokenize
from app.schemas.rag import EvaluationSummary
from app.services.llm import llm_service


class Evaluator:
    def evaluate(self, question: str, answer: str, hits: list[dict]) -> EvaluationSummary:
        retrieval_score = round(sum(hit["score"] for hit in hits) / max(len(hits), 1), 4) if hits else 0.0
        hallucination_risk = self._hallucination_risk(answer=answer, hits=hits)
        answer_score = llm_service.score_answer(question=question, answer=answer, contexts=hits)
        return EvaluationSummary(
            retrieval_score=retrieval_score,
            hallucination_risk=hallucination_risk,
            answer_score=answer_score,
        )

    def _hallucination_risk(self, *, answer: str, hits: list[dict]) -> float:
        answer_terms = set(tokenize(answer))
        context_terms = set()
        for hit in hits:
            context_terms.update(tokenize(hit["text"]))
        if not answer_terms:
            return 1.0
        unsupported = answer_terms - context_terms
        return round(min(len(unsupported) / max(len(answer_terms), 1), 1.0), 4)


evaluator = Evaluator()
