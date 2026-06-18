from __future__ import annotations

import hashlib
import math
import re
import socket
from typing import Iterable

from sqlalchemy.orm import Session

from config import settings
from models import Movie, MovieEmbedding


class EmbeddingServiceError(RuntimeError):
    pass


class EmbeddingService:
    def __init__(self) -> None:
        self._model = None
        self._fallback_mode = False

    def _load_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                self._fallback_mode = True
                self._model = False
                return self._model
            try:
                self._model = SentenceTransformer(
                    settings.embedding_model_name,
                    local_files_only=True,
                )
            except Exception:
                if not self._has_huggingface_connectivity():
                    self._fallback_mode = True
                    self._model = False
                    return self._model
                try:
                    self._model = SentenceTransformer(settings.embedding_model_name)
                except Exception:
                    self._fallback_mode = True
                    self._model = False
        return self._model

    def _has_huggingface_connectivity(self) -> bool:
        try:
            with socket.create_connection(("huggingface.co", 443), timeout=1):
                return True
        except OSError:
            return False

    def _fallback_encode(self, text: str, dimensions: int = 384) -> list[float]:
        vector = [0.0] * dimensions
        tokens = re.findall(r"[a-zA-Zа-яА-Я0-9]+", text.lower())
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            weight = 1.0 + min(len(token), 12) / 12.0
            vector[bucket] += sign * weight

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]

    def build_movie_text(self, movie: Movie) -> str:
        genre_names = ", ".join(genre.name for genre in movie.genres)
        actor_names = ", ".join(actor.name for actor in movie.actors[:8])
        director_names = ", ".join(director.name for director in movie.directors[:3])
        keyword_names = ", ".join(keyword.name for keyword in movie.keywords[:12])
        return "\n".join(
            [
                movie.title or "",
                movie.original_title or "",
                movie.overview or "",
                f"Genres: {genre_names}",
                f"Actors: {actor_names}",
                f"Directors: {director_names}",
                f"Keywords: {keyword_names}",
                f"Status: {movie.status or ''}",
                f"Language: {movie.language or ''}",
            ],
        ).strip()

    def encode_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load_model()
        if model is False:
            return [self._fallback_encode(text) for text in texts]
        vectors = model.encode(texts, normalize_embeddings=True)
        return [[float(value) for value in vector] for vector in vectors]

    def encode_query(self, text: str) -> list[float]:
        vectors = self.encode_texts([text])
        return vectors[0] if vectors else []

    def persist_embedding(self, db: Session, movie: Movie, vector: list[float]) -> None:
        record = movie.embedding
        if record is None:
            record = MovieEmbedding(
                movie_id=movie.id,
                model_name=settings.embedding_model_name,
                embedding=vector,
            )
            db.add(record)
            movie.embedding = record
        else:
            record.model_name = settings.embedding_model_name
            record.embedding = vector

    def ensure_embeddings(self, db: Session, movies: Iterable[Movie]) -> int:
        pending_movies: list[Movie] = []
        texts: list[str] = []

        for movie in movies:
            has_embedding = (
                movie.embedding is not None
                and movie.embedding.model_name == settings.embedding_model_name
                and movie.embedding.embedding
            )
            if has_embedding:
                continue
            pending_movies.append(movie)
            texts.append(self.build_movie_text(movie))

        if not pending_movies:
            return 0

        vectors = self.encode_texts(texts)
        for movie, vector in zip(pending_movies, vectors):
            self.persist_embedding(db, movie, vector)

        db.commit()
        return len(pending_movies)

    def get_movie_vector(self, db: Session, movie: Movie) -> list[float]:
        self.ensure_embeddings(db, [movie])
        if movie.embedding is None:
            raise EmbeddingServiceError(f"Could not build embedding for movie '{movie.title}'.")
        return [float(value) for value in movie.embedding.embedding]


embedding_service = EmbeddingService()
