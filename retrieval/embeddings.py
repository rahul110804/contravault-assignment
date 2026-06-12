"""Embedding generation using sentence-transformers."""
import logging
from functools import lru_cache

import numpy as np

from config import EMBEDDING_MODEL

logger = logging.getLogger(__name__)

_model = None


def _get_model():
    """Lazy-load the embedding model (singleton)."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
        _model = SentenceTransformer(EMBEDDING_MODEL)
        logger.info("Embedding model loaded.")
    return _model


def embed_texts(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """Generate embeddings for a list of texts.
    
    Args:
        texts: List of text strings to embed
        batch_size: Batch size for encoding
    
    Returns:
        List of embedding vectors as lists of floats
    """
    if not texts:
        return []
    
    model = _get_model()
    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=False,
        normalize_embeddings=True
    )
    return embeddings.tolist()


def embed_single(text: str) -> list[float]:
    """Generate embedding for a single text."""
    return embed_texts([text])[0]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    a_arr = np.array(a)
    b_arr = np.array(b)
    return float(np.dot(a_arr, b_arr) / (np.linalg.norm(a_arr) * np.linalg.norm(b_arr) + 1e-9))
