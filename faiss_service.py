from __future__ import annotations

import numpy as np
from sqlalchemy.orm import Session

from models import MovieEmbedding


class FAISSServiceError(RuntimeError):
    pass


class FAISSService:
    def __init__(self) -> None:
        self.index = None
        self.movie_ids: list[int] = []

    def _load_faiss(self):
        try:
            import faiss
        except ImportError as exc:
            raise FAISSServiceError(
                "faiss-cpu is not installed. Run pip install -r requirements.txt.",
            ) from exc
        return faiss

    def rebuild_index(self, db: Session) -> int:
        embeddings = db.query(MovieEmbedding).order_by(MovieEmbedding.movie_id).all()
        if not embeddings:
            self.index = None
            self.movie_ids = []
            return 0

        matrix = np.asarray(
            [[float(value) for value in embedding.embedding] for embedding in embeddings],
            dtype="float32",
        )
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)

        faiss = self._load_faiss()
        faiss.normalize_L2(matrix)
        self.index = faiss.IndexFlatIP(matrix.shape[1])
        self.index.add(matrix)
        self.movie_ids = [embedding.movie_id for embedding in embeddings]
        return len(self.movie_ids)

    def ensure_index(self, db: Session) -> int:
        embedding_count = db.query(MovieEmbedding).count()
        if embedding_count == 0:
            self.index = None
            self.movie_ids = []
            return 0
        if self.index is None or len(self.movie_ids) != embedding_count:
            return self.rebuild_index(db)
        return embedding_count

    def search_by_vector(
        self,
        db: Session,
        vector: list[float],
        top_k: int = 10,
        exclude_movie_id: int | None = None,
    ) -> list[tuple[int, float]]:
        if not vector:
            return []

        self.ensure_index(db)
        if self.index is None:
            return []

        faiss = self._load_faiss()
        query = np.asarray([vector], dtype="float32")
        faiss.normalize_L2(query)

        limit = min(max(top_k, 1), len(self.movie_ids))
        scores, indices = self.index.search(query, limit)

        results: list[tuple[int, float]] = []
        for score, idx in zip(scores[0].tolist(), indices[0].tolist()):
            if idx < 0:
                continue
            movie_id = self.movie_ids[idx]
            if exclude_movie_id is not None and movie_id == exclude_movie_id:
                continue
            normalized_score = max(0.0, min(1.0, (float(score) + 1.0) / 2.0))
            results.append((movie_id, normalized_score))
        return results


faiss_service = FAISSService()
