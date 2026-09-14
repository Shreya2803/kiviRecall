import asyncio
from functools import lru_cache

from sentence_transformers import SentenceTransformer

from kivi.config import settings

EMBEDDING_DIM = 1024


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    return SentenceTransformer(settings.embedding_model)


def embed(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """Synchronous, CPU-bound. Call via embed_async from request handlers so it
    doesn't block the event loop."""
    if not texts:
        return []
    model = _get_model()
    vectors = model.encode(texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


async def embed_async(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, embed, texts, batch_size)
