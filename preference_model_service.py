from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import desc
from sqlalchemy.orm import Session, selectinload

from models import Favorite, Genre, Movie, UserRating, UserReaction, WatchHistory


class PreferenceModelServiceError(RuntimeError):
    pass


@dataclass
class PersonalizationResult:
    probabilities: dict[int, float] = field(default_factory=dict)
    seen_movie_ids: set[int] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    trained_samples: int = 0
    used_pseudo_negatives: bool = False


class PreferenceModelService:
    positive_watch_threshold = 70
    positive_rating_threshold = 8
    mild_positive_rating_threshold = 6
    negative_rating_threshold = 4
    minimum_training_samples = 4

    def _load_torch(self):
        try:
            import torch
            import torch.nn as nn
            import torch.optim as optim
        except ImportError as exc:
            raise PreferenceModelServiceError(
                "PyTorch не установлен. Добавьте torch в окружение, чтобы включить персональную нейросеть.",
            ) from exc
        return torch, nn, optim

    def _movie_query(self, db: Session):
        return db.query(Movie).options(selectinload(Movie.genres))

    def get_seen_movie_ids(self, db: Session, user_id: int | None) -> set[int]:
        if user_id is None:
            return set()
        return {
            movie_id
            for (movie_id,) in db.query(WatchHistory.movie_id)
            .filter(
                WatchHistory.user_id == user_id,
                WatchHistory.progress_percent >= self.positive_watch_threshold,
            )
            .all()
        }

    def _collect_feedback(self, db: Session, user_id: int) -> tuple[dict[int, float], set[int]]:
        feedback: dict[int, float] = defaultdict(float)
        seen_movie_ids = set()

        for (movie_id,) in db.query(Favorite.movie_id).filter(Favorite.user_id == user_id).all():
            feedback[movie_id] += 2.5

        for movie_id, reaction in db.query(UserReaction.movie_id, UserReaction.reaction).filter(
            UserReaction.user_id == user_id,
        ):
            normalized_reaction = (reaction or "").strip().lower()
            if normalized_reaction == "like":
                feedback[movie_id] += 2.0
            elif normalized_reaction in {"dislike", "skip", "not_interested"}:
                feedback[movie_id] -= 2.0

        for movie_id, rating in db.query(UserRating.movie_id, UserRating.rating).filter(
            UserRating.user_id == user_id,
        ):
            if rating >= self.positive_rating_threshold:
                feedback[movie_id] += 2.0
            elif rating >= self.mild_positive_rating_threshold:
                feedback[movie_id] += 0.75
            elif rating <= self.negative_rating_threshold:
                feedback[movie_id] -= 2.0

        for movie_id, progress_percent in db.query(
            WatchHistory.movie_id,
            WatchHistory.progress_percent,
        ).filter(WatchHistory.user_id == user_id):
            seen_movie_ids.add(movie_id)
            if progress_percent >= self.positive_watch_threshold:
                feedback[movie_id] += 1.0

        return dict(feedback), seen_movie_ids

    def _resolve_labels(
        self,
        feedback_scores: dict[int, float],
    ) -> tuple[set[int], set[int]]:
        positive_movie_ids = {
            movie_id
            for movie_id, score in feedback_scores.items()
            if score >= 1.0
        }
        negative_movie_ids = {
            movie_id
            for movie_id, score in feedback_scores.items()
            if score <= -1.0
        }
        return positive_movie_ids, negative_movie_ids

    def _genre_index(self, db: Session) -> dict[str, int]:
        genres = [name for (name,) in db.query(Genre.name).order_by(Genre.name.asc()).all()]
        return {name: idx for idx, name in enumerate(genres)}

    def _normalize_rating(self, movie: Movie) -> float:
        return max(0.0, min(float(movie.rating or 0) / 10.0, 1.0))

    def _normalize_year(self, movie: Movie) -> float:
        release_year = movie.release_date.year if movie.release_date else 2010
        return max(0.0, min((release_year - 1950) / 100.0, 1.0))

    def _normalize_runtime(self, movie: Movie) -> float:
        runtime = float(movie.runtime or 0)
        return max(0.0, min(runtime / 240.0, 1.0))

    def _normalize_popularity(self, movie: Movie, max_popularity: float) -> float:
        popularity = max(0.0, float(movie.popularity or 0))
        if max_popularity <= 0:
            return 0.0
        return min(math.log1p(popularity) / math.log1p(max_popularity), 1.0)

    def _feature_vector(
        self,
        movie: Movie,
        genre_index: dict[str, int],
        max_popularity: float,
    ) -> list[float]:
        vector = [0.0] * (len(genre_index) + 4)
        for genre in movie.genres:
            idx = genre_index.get(genre.name)
            if idx is not None:
                vector[idx] = 1.0

        offset = len(genre_index)
        vector[offset] = self._normalize_rating(movie)
        vector[offset + 1] = self._normalize_year(movie)
        vector[offset + 2] = self._normalize_runtime(movie)
        vector[offset + 3] = self._normalize_popularity(movie, max_popularity)
        return vector

    def _select_pseudo_negatives(
        self,
        db: Session,
        excluded_ids: set[int],
        positive_movies: list[Movie],
        limit: int,
    ) -> list[Movie]:
        if limit <= 0:
            return []

        positive_genres = {
            genre.name
            for movie in positive_movies
            for genre in movie.genres
        }
        candidates = (
            self._movie_query(db)
            .filter(~Movie.id.in_(excluded_ids))
            .order_by(desc(Movie.popularity), desc(Movie.rating), Movie.id.asc())
            .all()
        )
        disjoint_candidates = [
            movie
            for movie in candidates
            if not ({genre.name for genre in movie.genres} & positive_genres)
        ]

        selected = disjoint_candidates[:limit]
        if len(selected) >= limit:
            return selected

        selected_ids = {movie.id for movie in selected}
        for movie in candidates:
            if movie.id in selected_ids:
                continue
            selected.append(movie)
            selected_ids.add(movie.id)
            if len(selected) >= limit:
                break
        return selected

    def _build_model(self, input_dim: int, nn):
        hidden_1 = max(16, min(64, input_dim * 2))
        hidden_2 = max(8, hidden_1 // 2)
        return nn.Sequential(
            nn.Linear(input_dim, hidden_1),
            nn.ReLU(),
            nn.Linear(hidden_1, hidden_2),
            nn.ReLU(),
            nn.Linear(hidden_2, 1),
            nn.Sigmoid(),
        )

    def predict_user_preferences(
        self,
        db: Session,
        user_id: int,
        candidate_movies: list[Movie],
    ) -> PersonalizationResult:
        if not candidate_movies:
            return PersonalizationResult()

        feedback_scores, seen_movie_ids = self._collect_feedback(db, user_id)
        positive_movie_ids, negative_movie_ids = self._resolve_labels(feedback_scores)
        labeled_movie_ids = positive_movie_ids | negative_movie_ids

        if not positive_movie_ids:
            raise PreferenceModelServiceError(
                "Недостаточно положительных пользовательских сигналов для обучения персональной модели.",
            )

        training_movies = []
        label_by_movie_id: dict[int, float] = {}

        if labeled_movie_ids:
            training_movies = (
                self._movie_query(db)
                .filter(Movie.id.in_(labeled_movie_ids))
                .all()
            )
            for movie in training_movies:
                label_by_movie_id[movie.id] = 1.0 if movie.id in positive_movie_ids else 0.0

        warnings: list[str] = []
        used_pseudo_negatives = False
        negative_count = sum(1 for value in label_by_movie_id.values() if value == 0.0)
        positive_movies = [movie for movie in training_movies if label_by_movie_id.get(movie.id) == 1.0]
        target_negative_count = max(len(positive_movies), 2)
        if negative_count < target_negative_count:
            pseudo_negatives = self._select_pseudo_negatives(
                db,
                excluded_ids=labeled_movie_ids,
                positive_movies=positive_movies,
                limit=target_negative_count - negative_count,
            )
            if pseudo_negatives:
                used_pseudo_negatives = True
                warnings.append(
                    "Нейросеть дополнила обучение непросмотренными фильмами как слабыми отрицательными примерами.",
                )
                for movie in pseudo_negatives:
                    training_movies.append(movie)
                    label_by_movie_id[movie.id] = 0.0

        labels = [label_by_movie_id[movie.id] for movie in training_movies]
        if len(training_movies) < self.minimum_training_samples or len(set(labels)) < 2:
            raise PreferenceModelServiceError(
                "Недостаточно разнообразных пользовательских сигналов для обучения персональной модели.",
            )

        torch, nn, optim = self._load_torch()
        torch.manual_seed(42)

        genre_index = self._genre_index(db)
        max_popularity = max(
            [float(movie.popularity or 0) for movie in [*training_movies, *candidate_movies]],
            default=1.0,
        ) or 1.0

        training_vectors = [
            self._feature_vector(movie, genre_index, max_popularity)
            for movie in training_movies
        ]
        X = torch.tensor(training_vectors, dtype=torch.float32)
        y = torch.tensor(labels, dtype=torch.float32).unsqueeze(1)

        model = self._build_model(len(training_vectors[0]), nn)
        criterion = nn.BCELoss()
        optimizer = optim.Adam(model.parameters(), lr=0.01)

        model.train()
        for _ in range(350):
            prediction = model(X)
            loss = criterion(prediction, y)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            if float(loss.item()) <= 0.02:
                break

        candidate_vectors = [
            self._feature_vector(movie, genre_index, max_popularity)
            for movie in candidate_movies
        ]
        candidate_tensor = torch.tensor(candidate_vectors, dtype=torch.float32)
        model.eval()
        with torch.no_grad():
            predictions = model(candidate_tensor).squeeze(1).tolist()

        probabilities = {
            movie.id: max(0.0, min(float(probability), 1.0))
            for movie, probability in zip(candidate_movies, predictions)
        }
        return PersonalizationResult(
            probabilities=probabilities,
            seen_movie_ids=seen_movie_ids,
            warnings=warnings,
            trained_samples=len(training_movies),
            used_pseudo_negatives=used_pseudo_negatives,
        )


preference_model_service = PreferenceModelService()
