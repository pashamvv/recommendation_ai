from __future__ import annotations

from collections import defaultdict

from sqlalchemy import desc
from sqlalchemy.orm import Session, selectinload

from embedding_service import EmbeddingServiceError, embedding_service
from faiss_service import FAISSServiceError, faiss_service
from models import Favorite, Movie, RecommendationCache, UserRating, UserReaction, WatchHistory
from nlp_parser import ParsedPreferences


class RecommendationEngine:
    mood_to_genres = {
        "scary": {"ужасы", "триллер", "детектив"},
        "funny": {"комедия", "приключения"},
        "romantic": {"мелодрама", "драма"},
        "melancholic": {"драма", "фэнтези"},
        "dynamic": {"боевик", "приключения", "фантастика"},
        "tense": {"триллер", "криминал", "детектив"},
        "evening": {"комедия", "драма", "приключения"},
        "warm": {"мелодрама", "комедия", "мультфильм"},
        "easy": {"комедия", "приключения", "мультфильм"},
    }

    def _movie_query(self, db: Session):
        return db.query(Movie).options(
            selectinload(Movie.genres),
            selectinload(Movie.actors),
            selectinload(Movie.directors),
            selectinload(Movie.keywords),
            selectinload(Movie.embedding),
            selectinload(Movie.similar_movies),
        )

    def _to_float(self, value) -> float:
        if value is None:
            return 0.0
        return float(value)

    def _serialize_movie(self, movie: Movie) -> dict:
        return {
            "id": movie.id,
            "tmdb_id": movie.tmdb_id,
            "title": movie.title,
            "original_title": movie.original_title,
            "overview": movie.overview,
            "release_date": movie.release_date,
            "rating": self._to_float(movie.rating),
            "poster_url": movie.poster_url,
            "backdrop_url": movie.backdrop_url,
            "popularity": self._to_float(movie.popularity),
            "runtime": movie.runtime,
            "status": movie.status,
            "language": movie.language,
        }

    def _metadata_similarity(self, base_movie: Movie, candidate: Movie) -> float:
        def overlap_score(left: set[str], right: set[str]) -> float:
            if not left or not right:
                return 0.0
            return len(left & right) / max(len(left), len(right))

        base_genres = {genre.name for genre in base_movie.genres}
        candidate_genres = {genre.name for genre in candidate.genres}
        base_actors = {actor.name for actor in base_movie.actors}
        candidate_actors = {actor.name for actor in candidate.actors}
        base_directors = {director.name for director in base_movie.directors}
        candidate_directors = {director.name for director in candidate.directors}
        base_keywords = {keyword.name for keyword in base_movie.keywords}
        candidate_keywords = {keyword.name for keyword in candidate.keywords}

        return (
            0.35 * overlap_score(base_genres, candidate_genres)
            + 0.25 * overlap_score(base_actors, candidate_actors)
            + 0.20 * overlap_score(base_directors, candidate_directors)
            + 0.20 * overlap_score(base_keywords, candidate_keywords)
        )

    def _build_reason(
        self,
        candidate: Movie,
        semantic_score: float,
        preference_reason_parts: list[str],
        reference_movie: Movie | None = None,
    ) -> str:
        parts = list(preference_reason_parts)
        if reference_movie is not None and semantic_score >= 0.65:
            parts.append(f"сильное смысловое сходство с фильмом {reference_movie.title}")
        elif semantic_score >= 0.70:
            parts.append("похожа по атмосфере и описанию")

        if not parts:
            parts.append("хорошо сочетает рейтинг, популярность и тематическое сходство")
        return "; ".join(dict.fromkeys(parts))

    def _passes_hard_filters(self, movie: Movie, preferences: ParsedPreferences) -> bool:
        if preferences.genres:
            genre_names = {genre.name.lower() for genre in movie.genres}
            if not any(name.lower() in genre_names for name in preferences.genres):
                return False

        if preferences.actors:
            actor_names = {actor.name.lower() for actor in movie.actors}
            if not any(name.lower() in actor_names for name in preferences.actors):
                return False

        if preferences.directors:
            director_names = {director.name.lower() for director in movie.directors}
            if not any(name.lower() in director_names for name in preferences.directors):
                return False
        return True

    def _preference_score(
        self,
        movie: Movie,
        preferences: ParsedPreferences,
        reference_movie: Movie | None,
    ) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []

        genre_names = {genre.name for genre in movie.genres}
        actor_names = {actor.name for actor in movie.actors}
        director_names = {director.name for director in movie.directors}
        keyword_names = {keyword.name.lower() for keyword in movie.keywords}
        combined_text = " ".join(
            [
                movie.title or "",
                movie.original_title or "",
                movie.overview or "",
                " ".join(keyword.name for keyword in movie.keywords),
            ],
        ).lower()

        matched_genres = sorted(set(preferences.genres) & genre_names)
        if matched_genres:
            score += 0.30
            reasons.append(f"подходит по жанру: {', '.join(matched_genres[:3])}")

        matched_actors = sorted(set(preferences.actors) & actor_names)
        if matched_actors:
            score += 0.25
            reasons.append(f"есть актеры: {', '.join(matched_actors[:2])}")

        matched_directors = sorted(set(preferences.directors) & director_names)
        if matched_directors:
            score += 0.20
            reasons.append(f"режиссерский матч: {', '.join(matched_directors[:2])}")

        matched_keywords = [keyword for keyword in preferences.keywords if keyword.lower() in combined_text or keyword.lower() in keyword_names]
        if matched_keywords:
            score += min(0.15, len(matched_keywords) * 0.05)
            reasons.append(f"есть нужная тематика: {', '.join(matched_keywords[:3])}")

        mood_bonus = 0.0
        for mood in preferences.moods:
            if genre_names & self.mood_to_genres.get(mood, set()):
                mood_bonus += 0.05
        if mood_bonus:
            score += min(0.15, mood_bonus)
            reasons.append("совпадает по настроению запроса")

        if reference_movie is not None:
            similarity = self._metadata_similarity(reference_movie, movie)
            if similarity:
                score += 0.30 * similarity
                reasons.append("есть общие жанры, актеры или ключевые слова с референсом")

        return min(score, 1.0), reasons

    def _build_user_profile(self, db: Session, user_id: int | None) -> dict:
        if user_id is None:
            return {}

        liked_movie_ids = set()
        liked_movie_ids.update(
            movie_id for (movie_id,) in db.query(Favorite.movie_id).filter(Favorite.user_id == user_id).all()
        )
        liked_movie_ids.update(
            movie_id
            for (movie_id,) in db.query(UserReaction.movie_id)
            .filter(UserReaction.user_id == user_id, UserReaction.reaction == "like")
            .all()
        )
        liked_movie_ids.update(
            movie_id
            for (movie_id,) in db.query(UserRating.movie_id)
            .filter(UserRating.user_id == user_id, UserRating.rating >= 8)
            .all()
        )
        liked_movie_ids.update(
            movie_id
            for (movie_id,) in db.query(WatchHistory.movie_id)
            .filter(WatchHistory.user_id == user_id, WatchHistory.progress_percent >= 70)
            .all()
        )

        if not liked_movie_ids:
            return {}

        liked_movies = self._movie_query(db).filter(Movie.id.in_(liked_movie_ids)).all()
        profile = defaultdict(set)
        for movie in liked_movies:
            for genre in movie.genres:
                profile["genres"].add(genre.name)
            for actor in movie.actors:
                profile["actors"].add(actor.name)
            for director in movie.directors:
                profile["directors"].add(director.name)
        return profile

    def _user_affinity_score(self, movie: Movie, user_profile: dict) -> float:
        if not user_profile:
            return 0.0

        score = 0.0
        if {genre.name for genre in movie.genres} & user_profile.get("genres", set()):
            score += 0.5
        if {actor.name for actor in movie.actors} & user_profile.get("actors", set()):
            score += 0.3
        if {director.name for director in movie.directors} & user_profile.get("directors", set()):
            score += 0.2
        return min(score, 1.0)

    def _cache_recommendations(
        self,
        db: Session,
        source_movie_id: int,
        recommendations: list[dict],
    ) -> None:
        db.query(RecommendationCache).filter(
            RecommendationCache.source_movie_id == source_movie_id,
        ).delete()
        for item in recommendations:
            db.add(
                RecommendationCache(
                    source_movie_id=source_movie_id,
                    recommended_movie_id=item["id"],
                    score=item["score"],
                    reason=item["reason"],
                ),
            )
        db.commit()

    def get_recommendations(self, db: Session, movie_id: int, limit: int = 10) -> dict:
        base_movie = self._movie_query(db).filter(Movie.id == movie_id).first()
        if base_movie is None:
            raise ValueError("Movie not found.")

        all_movies = self._movie_query(db).filter(Movie.id != movie_id).all()
        if not all_movies:
            return {"base_movie": self._serialize_movie(base_movie), "recommendations": []}

        try:
            embedding_service.ensure_embeddings(db, [base_movie, *all_movies])
            vector = embedding_service.get_movie_vector(db, base_movie)
            semantic_scores = dict(
                faiss_service.search_by_vector(
                    db,
                    vector,
                    top_k=max(limit * 8, 50),
                    exclude_movie_id=base_movie.id,
                ),
            )
        except (EmbeddingServiceError, FAISSServiceError):
            semantic_scores = {}

        max_popularity = max(self._to_float(movie.popularity) for movie in all_movies) or 1.0
        ranked: list[tuple[float, dict]] = []
        for candidate in all_movies:
            semantic = semantic_scores.get(candidate.id, 0.0)
            metadata = self._metadata_similarity(base_movie, candidate)
            quality = min(self._to_float(candidate.rating) / 10.0, 1.0)
            popularity = min(self._to_float(candidate.popularity) / max_popularity, 1.0)
            final_score = 0.55 * semantic + 0.25 * metadata + 0.10 * quality + 0.10 * popularity
            if final_score <= 0:
                continue

            reason = self._build_reason(
                candidate,
                semantic_score=semantic,
                preference_reason_parts=[],
                reference_movie=base_movie,
            )
            payload = self._serialize_movie(candidate)
            payload["score"] = int(round(final_score * 100))
            payload["reason"] = reason
            ranked.append((final_score, payload))

        ranked.sort(key=lambda item: item[0], reverse=True)
        recommendations = [payload for _, payload in ranked[:limit]]
        self._cache_recommendations(db, source_movie_id=base_movie.id, recommendations=recommendations)
        return {
            "base_movie": self._serialize_movie(base_movie),
            "recommendations": recommendations,
        }

    def search_by_preferences(
        self,
        db: Session,
        preferences: ParsedPreferences,
        limit: int = 5,
        user_id: int | None = None,
    ) -> list[dict]:
        if preferences.reference_movie_id is not None and not any(
            [
                preferences.genres,
                preferences.actors,
                preferences.directors,
                preferences.keywords,
                preferences.moods,
            ],
        ):
            return self.get_recommendations(
                db,
                movie_id=preferences.reference_movie_id,
                limit=limit,
            )["recommendations"]

        movies = self._movie_query(db).order_by(desc(Movie.popularity), desc(Movie.rating)).all()
        if not movies:
            return []

        reference_movie = None
        if preferences.reference_movie_id is not None:
            reference_movie = next((movie for movie in movies if movie.id == preferences.reference_movie_id), None)

        semantic_scores: dict[int, float] = {}
        try:
            embedding_service.ensure_embeddings(db, movies)
            if reference_movie is not None:
                vector = embedding_service.get_movie_vector(db, reference_movie)
                semantic_scores = dict(
                    faiss_service.search_by_vector(
                        db,
                        vector,
                        top_k=max(limit * 8, 50),
                        exclude_movie_id=reference_movie.id,
                    ),
                )
            else:
                query_text = " ".join(
                    [
                        preferences.free_text or "",
                        " ".join(preferences.genres),
                        " ".join(preferences.actors),
                        " ".join(preferences.directors),
                        " ".join(preferences.keywords),
                        " ".join(preferences.moods),
                    ],
                ).strip()
                if query_text:
                    query_vector = embedding_service.encode_query(query_text)
                    semantic_scores = dict(
                        faiss_service.search_by_vector(
                            db,
                            query_vector,
                            top_k=max(limit * 8, 50),
                        ),
                    )
        except (EmbeddingServiceError, FAISSServiceError):
            semantic_scores = {}

        user_profile = self._build_user_profile(db, user_id)
        max_popularity = max(self._to_float(movie.popularity) for movie in movies) or 1.0
        ranked: list[tuple[float, dict]] = []

        for movie in movies:
            if reference_movie is not None and movie.id == reference_movie.id:
                continue
            if not self._passes_hard_filters(movie, preferences):
                continue

            semantic = semantic_scores.get(movie.id, 0.0)
            preference_score, reasons = self._preference_score(movie, preferences, reference_movie)
            user_affinity = self._user_affinity_score(movie, user_profile)
            quality = min(self._to_float(movie.rating) / 10.0, 1.0)
            popularity = min(self._to_float(movie.popularity) / max_popularity, 1.0)

            if semantic_scores or preference_score or user_affinity:
                final_score = (
                    0.45 * semantic
                    + 0.25 * preference_score
                    + 0.15 * quality
                    + 0.10 * popularity
                    + 0.05 * user_affinity
                )
            else:
                final_score = 0.6 * quality + 0.4 * popularity

            if final_score <= 0:
                continue

            reason = self._build_reason(
                movie,
                semantic_score=semantic,
                preference_reason_parts=reasons,
                reference_movie=reference_movie,
            )
            payload = self._serialize_movie(movie)
            payload["score"] = int(round(final_score * 100))
            payload["reason"] = reason
            ranked.append((final_score, payload))

        ranked.sort(key=lambda item: item[0], reverse=True)
        return [payload for _, payload in ranked[:limit]]


recommendation_engine = RecommendationEngine()
