from __future__ import annotations

from collections import defaultdict

from sqlalchemy import desc
from sqlalchemy.orm import Session, selectinload

from embedding_service import EmbeddingServiceError, embedding_service
from faiss_service import FAISSServiceError, faiss_service
from models import Favorite, Movie, RecommendationCache, User, UserRating, UserReaction, WatchHistory
from nlp_parser import ParsedPreferences
from preference_model_service import (
    PersonalizationResult,
    PreferenceModelServiceError,
    preference_model_service,
)


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

    def _ensure_user_exists(self, db: Session, user_id: int | None) -> None:
        if user_id is not None and db.get(User, user_id) is None:
            raise ValueError("Пользователь не найден.")

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

    def _overlap_score(self, left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / max(len(left), len(right))

    def _metadata_similarity(self, base_movie: Movie, candidate: Movie) -> float:
        base_genres = {genre.name for genre in base_movie.genres}
        candidate_genres = {genre.name for genre in candidate.genres}
        base_actors = {actor.name for actor in base_movie.actors}
        candidate_actors = {actor.name for actor in candidate.actors}
        base_directors = {director.name for director in base_movie.directors}
        candidate_directors = {director.name for director in candidate.directors}
        base_keywords = {keyword.name for keyword in base_movie.keywords}
        candidate_keywords = {keyword.name for keyword in candidate.keywords}

        return (
            0.35 * self._overlap_score(base_genres, candidate_genres)
            + 0.25 * self._overlap_score(base_actors, candidate_actors)
            + 0.20 * self._overlap_score(base_directors, candidate_directors)
            + 0.20 * self._overlap_score(base_keywords, candidate_keywords)
        )

    def _shared_metadata_parts(self, base_movie: Movie, candidate: Movie) -> list[str]:
        parts: list[str] = []
        shared_genres = sorted({genre.name for genre in base_movie.genres} & {genre.name for genre in candidate.genres})
        shared_actors = sorted({actor.name for actor in base_movie.actors} & {actor.name for actor in candidate.actors})
        shared_directors = sorted(
            {director.name for director in base_movie.directors}
            & {director.name for director in candidate.directors},
        )
        shared_keywords = sorted(
            {keyword.name for keyword in base_movie.keywords}
            & {keyword.name for keyword in candidate.keywords},
        )

        if shared_genres:
            parts.append(f"общие жанры: {', '.join(shared_genres[:3])}")
        if shared_actors:
            parts.append(f"общие актеры: {', '.join(shared_actors[:2])}")
        if shared_directors:
            parts.append(f"тот же режиссер: {', '.join(shared_directors[:2])}")
        if shared_keywords:
            parts.append(f"похожие темы: {', '.join(shared_keywords[:3])}")
        return parts

    def _build_reason(
        self,
        semantic_score: float,
        preference_reason_parts: list[str],
        shared_metadata_parts: list[str] | None = None,
        reference_movie: Movie | None = None,
        user_affinity: float = 0.0,
        personal_probability: float = 0.0,
    ) -> str:
        parts = list(preference_reason_parts)
        parts.extend(shared_metadata_parts or [])

        if reference_movie is not None and semantic_score >= 0.62:
            parts.append(f"сильное смысловое сходство с фильмом {reference_movie.title}")
        elif semantic_score >= 0.70:
            parts.append("близка по атмосфере и описанию")

        if personal_probability >= 0.78:
            parts.append("нейросеть видит очень высокий шанс, что фильм понравится именно вам")
        elif personal_probability >= 0.62:
            parts.append("нейросеть видит хорошее совпадение с вашими просмотренными фильмами")

        if user_affinity >= 0.55:
            parts.append("похоже на то, что вы уже досматривали, лайкали или высоко оценивали")

        unique_parts = [part for part in dict.fromkeys(parts) if part]
        if not unique_parts:
            unique_parts.append("хорошо сочетает рейтинг, популярность и тематическое сходство")
        return "; ".join(unique_parts[:3])

    def _passes_hard_filters(
        self,
        movie: Movie,
        preferences: ParsedPreferences,
        *,
        strict: bool = True,
    ) -> bool:
        if not strict:
            return True

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
            reasons.append(f"есть актеры из запроса: {', '.join(matched_actors[:2])}")

        matched_directors = sorted(set(preferences.directors) & director_names)
        if matched_directors:
            score += 0.20
            reasons.append(f"совпадает по режиссеру: {', '.join(matched_directors[:2])}")

        matched_keywords = [
            keyword
            for keyword in preferences.keywords
            if keyword.lower() in combined_text or keyword.lower() in keyword_names
        ]
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
                reasons.append("перекликается с фильмом-референсом по составу и темам")

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

    def _get_personalization_result(
        self,
        db: Session,
        user_id: int | None,
        candidate_movies: list[Movie],
    ) -> PersonalizationResult:
        if user_id is None or not candidate_movies:
            return PersonalizationResult()

        try:
            return preference_model_service.predict_user_preferences(db, user_id, candidate_movies)
        except PreferenceModelServiceError as exc:
            return PersonalizationResult(
                seen_movie_ids=preference_model_service.get_seen_movie_ids(db, user_id),
                warnings=[str(exc)],
            )

    def _semantic_scores_for_reference(
        self,
        db: Session,
        reference_movie: Movie,
        movies: list[Movie],
        limit: int,
    ) -> tuple[dict[int, float], list[str]]:
        try:
            embedding_service.ensure_embeddings(db, [reference_movie, *movies])
            vector = embedding_service.get_movie_vector(db, reference_movie)
            return (
                dict(
                    faiss_service.search_by_vector(
                        db,
                        vector,
                        top_k=max(limit * 8, 50),
                        exclude_movie_id=reference_movie.id,
                    ),
                ),
                [],
            )
        except (EmbeddingServiceError, FAISSServiceError):
            return {}, ["Семантический поиск недоступен, поэтому выдача опирается на метаданные и пользовательские сигналы."]

    def _semantic_scores_for_query(
        self,
        db: Session,
        preferences: ParsedPreferences,
        movies: list[Movie],
        limit: int,
    ) -> tuple[dict[int, float], list[str]]:
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
        if not query_text:
            return {}, []

        try:
            embedding_service.ensure_embeddings(db, movies)
            query_vector = embedding_service.encode_query(query_text)
            return (
                dict(
                    faiss_service.search_by_vector(
                        db,
                        query_vector,
                        top_k=max(limit * 8, 50),
                    ),
                ),
                [],
            )
        except (EmbeddingServiceError, FAISSServiceError):
            return {}, ["Семантический поиск по тексту недоступен, поэтому выдача опирается на фильтры и популярность."]

    def get_recommendations(
        self,
        db: Session,
        movie_id: int,
        limit: int = 10,
        user_id: int | None = None,
    ) -> dict:
        self._ensure_user_exists(db, user_id)
        base_movie = self._movie_query(db).filter(Movie.id == movie_id).first()
        if base_movie is None:
            raise ValueError("Фильм не найден.")

        all_movies = self._movie_query(db).filter(Movie.id != movie_id).all()
        personalization = self._get_personalization_result(db, user_id, all_movies)
        seen_movie_ids = personalization.seen_movie_ids - {base_movie.id}
        candidate_movies = [movie for movie in all_movies if movie.id not in seen_movie_ids]

        warnings = list(personalization.warnings)
        if user_id is not None and seen_movie_ids:
            warnings.append("Уже просмотренные фильмы скрыты из персональной выдачи.")

        if not candidate_movies:
            return {
                "base_movie": self._serialize_movie(base_movie),
                "recommendations": [],
                "strategy": "hybrid_movie_similarity",
                "fallback_used": True,
                "warnings": warnings,
            }

        semantic_scores, semantic_warnings = self._semantic_scores_for_reference(
            db,
            base_movie,
            candidate_movies,
            limit,
        )
        warnings.extend(semantic_warnings)

        user_profile = self._build_user_profile(db, user_id)
        max_popularity = max(self._to_float(movie.popularity) for movie in candidate_movies) or 1.0
        ranked: list[tuple[float, dict]] = []

        for candidate in candidate_movies:
            semantic = semantic_scores.get(candidate.id, 0.0)
            metadata = self._metadata_similarity(base_movie, candidate)
            quality = min(self._to_float(candidate.rating) / 10.0, 1.0)
            popularity = min(self._to_float(candidate.popularity) / max_popularity, 1.0)
            user_affinity = self._user_affinity_score(candidate, user_profile)
            personal_probability = personalization.probabilities.get(candidate.id, 0.0)

            if personalization.probabilities:
                final_score = (
                    0.40 * semantic
                    + 0.18 * metadata
                    + 0.10 * quality
                    + 0.08 * popularity
                    + 0.10 * user_affinity
                    + 0.14 * personal_probability
                )
            else:
                final_score = (
                    0.55 * semantic
                    + 0.25 * metadata
                    + 0.10 * quality
                    + 0.10 * popularity
                )
            if final_score <= 0:
                continue

            reason = self._build_reason(
                semantic_score=semantic,
                preference_reason_parts=[],
                shared_metadata_parts=self._shared_metadata_parts(base_movie, candidate),
                reference_movie=base_movie,
                user_affinity=user_affinity,
                personal_probability=personal_probability,
            )
            payload = self._serialize_movie(candidate)
            payload["score"] = int(round(final_score * 100))
            payload["reason"] = reason
            ranked.append((final_score, payload))

        ranked.sort(key=lambda item: item[0], reverse=True)
        recommendations = [payload for _, payload in ranked[:limit]]

        if user_id is None:
            self._cache_recommendations(db, source_movie_id=base_movie.id, recommendations=recommendations)

        strategy = "hybrid_movie_similarity"
        if personalization.probabilities:
            strategy += "_with_neural_personalization"

        return {
            "base_movie": self._serialize_movie(base_movie),
            "recommendations": recommendations,
            "strategy": strategy,
            "fallback_used": bool(semantic_warnings),
            "warnings": list(dict.fromkeys(warnings)),
        }

    def _rank_movies_by_preferences(
        self,
        movies: list[Movie],
        preferences: ParsedPreferences,
        reference_movie: Movie | None,
        semantic_scores: dict[int, float],
        user_profile: dict,
        personalization: PersonalizationResult,
        limit: int,
        *,
        strict_filters: bool,
    ) -> list[dict]:
        max_popularity = max((self._to_float(movie.popularity) for movie in movies), default=1.0) or 1.0
        ranked: list[tuple[float, dict]] = []

        for movie in movies:
            if reference_movie is not None and movie.id == reference_movie.id:
                continue
            if movie.id in personalization.seen_movie_ids:
                continue
            if not self._passes_hard_filters(movie, preferences, strict=strict_filters):
                continue

            semantic = semantic_scores.get(movie.id, 0.0)
            preference_score, reasons = self._preference_score(movie, preferences, reference_movie)
            user_affinity = self._user_affinity_score(movie, user_profile)
            personal_probability = personalization.probabilities.get(movie.id, 0.0)
            quality = min(self._to_float(movie.rating) / 10.0, 1.0)
            popularity = min(self._to_float(movie.popularity) / max_popularity, 1.0)

            if personalization.probabilities:
                final_score = (
                    0.32 * semantic
                    + 0.21 * preference_score
                    + 0.12 * quality
                    + 0.08 * popularity
                    + 0.10 * user_affinity
                    + 0.17 * personal_probability
                )
            elif semantic_scores or preference_score or user_affinity:
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

            shared_parts = self._shared_metadata_parts(reference_movie, movie) if reference_movie is not None else []
            reason = self._build_reason(
                semantic_score=semantic,
                preference_reason_parts=reasons,
                shared_metadata_parts=shared_parts,
                reference_movie=reference_movie,
                user_affinity=user_affinity,
                personal_probability=personal_probability,
            )
            payload = self._serialize_movie(movie)
            payload["score"] = int(round(final_score * 100))
            payload["reason"] = reason
            ranked.append((final_score, payload))

        ranked.sort(key=lambda item: item[0], reverse=True)
        return [payload for _, payload in ranked[:limit]]

    def search_by_preferences(
        self,
        db: Session,
        preferences: ParsedPreferences,
        limit: int = 5,
        user_id: int | None = None,
    ) -> dict:
        self._ensure_user_exists(db, user_id)
        if preferences.reference_movie_id is not None and not any(
            [
                preferences.genres,
                preferences.actors,
                preferences.directors,
                preferences.keywords,
                preferences.moods,
            ],
        ):
            recommendation_result = self.get_recommendations(
                db,
                movie_id=preferences.reference_movie_id,
                limit=limit,
                user_id=user_id,
            )
            return {
                "recommendations": recommendation_result["recommendations"],
                "strategy": recommendation_result["strategy"],
                "fallback_used": recommendation_result["fallback_used"],
                "warnings": recommendation_result["warnings"],
            }

        movies = self._movie_query(db).order_by(desc(Movie.popularity), desc(Movie.rating)).all()
        if not movies:
            return {
                "recommendations": [],
                "strategy": "assistant_hybrid",
                "fallback_used": True,
                "warnings": ["В каталоге пока нет фильмов для рекомендаций."],
            }

        reference_movie = None
        if preferences.reference_movie_id is not None:
            reference_movie = next((movie for movie in movies if movie.id == preferences.reference_movie_id), None)

        personalization = self._get_personalization_result(db, user_id, movies)
        warnings = list(personalization.warnings)
        if user_id is not None and personalization.seen_movie_ids:
            warnings.append("Из ответа убраны фильмы, которые пользователь уже просмотрел.")

        if reference_movie is not None:
            semantic_scores, semantic_warnings = self._semantic_scores_for_reference(
                db,
                reference_movie,
                movies,
                limit,
            )
        else:
            semantic_scores, semantic_warnings = self._semantic_scores_for_query(
                db,
                preferences,
                movies,
                limit,
            )
        warnings.extend(semantic_warnings)

        user_profile = self._build_user_profile(db, user_id)
        strict_results = self._rank_movies_by_preferences(
            movies,
            preferences,
            reference_movie,
            semantic_scores,
            user_profile,
            personalization,
            limit,
            strict_filters=True,
        )

        fallback_used = bool(semantic_warnings)
        explicit_filters_present = bool(preferences.genres or preferences.actors or preferences.directors)
        if len(strict_results) < limit and explicit_filters_present:
            relaxed_results = self._rank_movies_by_preferences(
                movies,
                preferences,
                reference_movie,
                semantic_scores,
                user_profile,
                personalization,
                limit * 3,
                strict_filters=False,
            )
            existing_ids = {item["id"] for item in strict_results}
            for item in relaxed_results:
                if item["id"] in existing_ids:
                    continue
                strict_results.append(item)
                existing_ids.add(item["id"])
                if len(strict_results) >= limit:
                    break
            if relaxed_results:
                fallback_used = True
                warnings.append("Строгие фильтры сузили выборку, поэтому поиск был расширен на близкие по смыслу фильмы.")

        strategy = "assistant_hybrid"
        if personalization.probabilities:
            strategy += "_with_neural_personalization"

        return {
            "recommendations": strict_results[:limit],
            "strategy": strategy,
            "fallback_used": fallback_used,
            "warnings": list(dict.fromkeys(warnings)),
        }


recommendation_engine = RecommendationEngine()
