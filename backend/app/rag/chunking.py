import re


TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def estimate_tokens(text: str) -> int:
    return len(TOKEN_PATTERN.findall(text or ""))


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_PATTERN.findall(text or "")]


def chunk_page_text(
    *,
    filename: str,
    page_number: int,
    text: str,
    target_tokens: int = 500,
    overlap_tokens: int = 80,
) -> list[dict]:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return []

    sentences = [part.strip() for part in re.split(r"(?<=[.?!])\s+", clean) if part.strip()]
    chunks: list[dict] = []
    current_parts: list[str] = []
    current_tokens = 0
    chunk_index = 0

    def flush() -> None:
        nonlocal current_parts, current_tokens, chunk_index
        if not current_parts:
            return
        chunk_text = " ".join(current_parts).strip()
        chunks.append(
            {
                "chunk_index": chunk_index,
                "text": chunk_text,
                "token_count": estimate_tokens(chunk_text),
                "page_number": page_number,
                "citation": f"{filename} p.{page_number}",
            }
        )
        chunk_index += 1
        if overlap_tokens <= 0:
            current_parts = []
            current_tokens = 0
            return

        overlap_words = chunk_text.split()[-overlap_tokens:]
        current_parts = [" ".join(overlap_words)] if overlap_words else []
        current_tokens = estimate_tokens(current_parts[0]) if current_parts else 0

    for sentence in sentences:
        sentence_tokens = estimate_tokens(sentence)
        if current_parts and current_tokens + sentence_tokens > target_tokens:
            flush()
        if sentence_tokens > target_tokens:
            words = sentence.split()
            start = 0
            while start < len(words):
                piece = " ".join(words[start:start + target_tokens])
                if piece:
                    chunks.append(
                        {
                            "chunk_index": chunk_index,
                            "text": piece,
                            "token_count": estimate_tokens(piece),
                            "page_number": page_number,
                            "citation": f"{filename} p.{page_number}",
                        }
                    )
                    chunk_index += 1
                start += max(target_tokens - overlap_tokens, 1)
            current_parts = []
            current_tokens = 0
            continue
        current_parts.append(sentence)
        current_tokens += sentence_tokens

    flush()
    return chunks

