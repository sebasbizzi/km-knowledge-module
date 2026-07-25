"""
Abstracción de embeddings.

Dev:  EMBEDDING_PROVIDER=local  → sentence-transformers, sin API key, corre en CPU
Prod: EMBEDDING_PROVIDER=bgem3  → BGE-m3 self-hosted, servicio HTTP

Para migrar de local a bgem3: cambiar las vars en .env y regenerar
los vectores existentes con el script de reingesta.
"""

import os
from typing import Protocol

import numpy as np

# El paquete NO lee ningún `.env` propio: la instancia que lo consume inyecta estas variables
# en su entorno antes de importar (12-factor). Ver `knowledge_module.db` para el mismo criterio.
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "local")
EMBEDDING_MODEL    = os.getenv("EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
EMBEDDING_DIM      = int(os.getenv("EMBEDDING_DIM", "384"))


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]: ...
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
    @property
    def dim(self) -> int: ...


class LocalEmbedder:
    """sentence-transformers local — sin API key, para desarrollo."""

    def __init__(self, model_name: str = EMBEDDING_MODEL):
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_name)
        self._dim = self._model.get_sentence_embedding_dimension()

    def embed(self, text: str) -> list[float]:
        vec = self._model.encode(text, normalize_embeddings=True)
        return vec.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return vecs.tolist()

    @property
    def dim(self) -> int:
        return self._dim


class BGEm3Embedder:
    """
    BGE-m3 self-hosted.
    Espera un servicio HTTP en BGEM3_URL que acepta POST /embed
    con body {"text": "..."} o {"texts": [...]}.
    """

    def __init__(self):
        import httpx
        self._url = os.getenv("BGEM3_URL", "http://localhost:8000")
        self._client = httpx.Client(timeout=120.0)
        self._dim = 1024

    def embed(self, text: str) -> list[float]:
        r = self._client.post(f"{self._url}/embed", json={"text": text})
        r.raise_for_status()
        return r.json()["embedding"]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        r = self._client.post(f"{self._url}/embed/batch", json={"texts": texts})
        r.raise_for_status()
        return r.json()["embeddings"]

    @property
    def dim(self) -> int:
        return self._dim


# ── Singleton ──────────────────────────────────────────────────────────────

_embedder: EmbeddingProvider | None = None


def get_embedder() -> EmbeddingProvider:
    global _embedder
    if _embedder is None:
        if EMBEDDING_PROVIDER == "bgem3":
            _embedder = BGEm3Embedder()
        else:
            _embedder = LocalEmbedder()
    return _embedder
