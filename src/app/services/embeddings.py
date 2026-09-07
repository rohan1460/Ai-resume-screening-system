"""Sentence-transformers embedding model.

The model is loaded **once per process** and reused (spec section 2). Loading it per
task would dominate the runtime of every job.
"""

from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def get_embedding_model() -> SentenceTransformer:
    settings = get_settings()
    logger.info(
        "embedding_model.loading",
        model=settings.embedding_model,
        device=settings.embedding_device,
    )
    model = SentenceTransformer(settings.embedding_model, device=settings.embedding_device)
    # Renamed in sentence-transformers 5.x; keep working on both.
    dimension = getattr(model, "get_embedding_dimension", None) or (
        model.get_sentence_embedding_dimension
    )
    logger.info("embedding_model.loaded", model=settings.embedding_model, dim=dimension())
    return model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts into unit-length vectors.

    Vectors are L2-normalized, so cosine similarity reduces to a dot product.
    """
    if not texts:
        return []
    model = get_embedding_model()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    return [vector.tolist() for vector in vectors]


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]
