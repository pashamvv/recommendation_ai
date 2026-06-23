from __future__ import annotations

from collections import Counter
from functools import lru_cache
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

from sqlalchemy import desc
from sqlalchemy.orm import Session, selectinload

from models import (
    AssistantMessage,
    AssistantRecommendedMovie,
    Favorite,
    Movie,
    SearchHistory,
    User,
    UserRating,
    UserReaction,
    WatchHistory,
)
from nlp_parser import ParsedPreferences, nlp_parser
from recommendation_engine import recommendation_engine


@lru_cache(maxsize=1)
def _load_secondary_assistant_module() -> ModuleType | None:
    module_path = Path(__file__).with_name("ai_assistant 2.py")
    if not module_path.exists():
        return None

    spec = spec_from_file_location("ai_assistant_2_runtime", module_path)
    if spec is None or spec.loader is None:
        return None

    module = module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return None
    return module


class AIAssistant:
    def __init__(self) -> None:
        self._secondary_assistant = self._build_secondary_assistant()

    def _build_secondary_assistant(self):
        module = _load_secondary_assistant_module()
        if module is None:
            return None

        assistant_cls = getattr(module, "AIAssistant", None)
        if assistant_cls is None:
            return None

        try:
            return assistant_cls()
        except Exception:
            return None

    def _movie_query(self, db: Session):
        return db.query(Movie).options(
            selectinload(Movie.genres),
            selectinload(Movie.actors),
            selectinload(Movie.directors),
        )

    def _top_names(self, counter: Counter[str], limit: int = 4) -> list[str]:
        return [name for name, _ in counter.most_common(limit)]

    def _collect_feedback_movie_ids(self, db: Session, user_id: int) -> tuple[set[int], set[int], set[int]]:
        positive_ids: set[int] = set()
        negative_ids: set[int] = set()
        seen_ids: set[int] = set()

        positive_ids.update(
            movie_id
            for (movie_id,) in db.query(Favorite.movie_id).filter(Favorite.user_id == user_id).all()
        )

        for movie_id, reaction in db.query(UserReaction.movie_id, UserReaction.reaction).filter(
            UserReaction.user_id == user_id,
        ):
            normalized_reaction = (reaction or "").strip().lower()
            if normalized_reaction == "like":
                positive_ids.add(movie_id)
            elif normalized_reaction in {"dislike", "skip", "not_interested"}:
                negative_ids.add(movie_id)

        for movie_id, rating in db.query(UserRating.movie_id, UserRating.rating).filter(
            UserRating.user_id == user_id,
        ):
            if rating >= 8:
                positive_ids.add(movie_id)
            elif rating <= 4:
                negative_ids.add(movie_id)

        for movie_id, progress_percent in db.query(
            WatchHistory.movie_id,
            WatchHistory.progress_percent,
        ).filter(WatchHistory.user_id == user_id):
            seen_ids.add(movie_id)
            if progress_percent >= 70:
                positive_ids.add(movie_id)

        return positive_ids, negative_ids, seen_ids

    def _summarize_movies(self, movies: list[Movie]) -> dict[str, list[str]]:
        genre_counter: Counter[str] = Counter()
        actor_counter: Counter[str] = Counter()
        director_counter: Counter[str] = Counter()
        titles: list[str] = []

        for movie in movies:
            if movie.title:
                titles.append(movie.title)
            genre_counter.update(genre.name for genre in movie.genres)
            actor_counter.update(actor.name for actor in movie.actors)
            director_counter.update(director.name for director in movie.directors)

        return {
            "movies": titles[:4],
            "genres": self._top_names(genre_counter),
            "actors": self._top_names(actor_counter, limit=3),
            "directors": self._top_names(director_counter, limit=3),
        }

    def _build_learning_context(self, db: Session, user_id: int | None) -> dict:
        if user_id is None:
            return {}

        positive_ids, negative_ids, seen_ids = self._collect_feedback_movie_ids(db, user_id)
        positive_movies = self._movie_query(db).filter(Movie.id.in_(positive_ids)).all() if positive_ids else []
        negative_movies = self._movie_query(db).filter(Movie.id.in_(negative_ids)).all() if negative_ids else []

        positive_summary = self._summarize_movies(positive_movies)
        negative_summary = self._summarize_movies(negative_movies)

        recent_queries = [
            query
            for (query,) in db.query(SearchHistory.query)
            .filter(SearchHistory.user_id == user_id)
            .order_by(SearchHistory.created_at.desc())
            .limit(5)
            .all()
        ]

        recent_messages = (
            db.query(AssistantMessage)
            .filter(AssistantMessage.user_id == user_id)
            .order_by(AssistantMessage.created_at.desc())
            .limit(3)
            .all()
        )

        query_genres: Counter[str] = Counter()
        query_actors: Counter[str] = Counter()
        query_directors: Counter[str] = Counter()
        query_moods: Counter[str] = Counter()
        query_reference_movies: Counter[str] = Counter()

        for query in recent_queries:
            parsed_query = nlp_parser.parse(db, query)
            query_genres.update(parsed_query.genres)
            query_actors.update(parsed_query.actors)
            query_directors.update(parsed_query.directors)
            query_moods.update(parsed_query.moods)
            if parsed_query.reference_movie:
                query_reference_movies.update([parsed_query.reference_movie])

        recent_dialogue = [
            {
                "user": message.message,
                "assistant": message.response,
            }
            for message in reversed(recent_messages)
        ]

        learning_context = {
            "liked_movies": positive_summary["movies"],
            "liked_genres": positive_summary["genres"],
            "liked_actors": positive_summary["actors"],
            "liked_directors": positive_summary["directors"],
            "avoided_genres": negative_summary["genres"],
            "recent_queries": list(reversed(recent_queries)),
            "recent_query_genres": self._top_names(query_genres),
            "recent_query_actors": self._top_names(query_actors, limit=3),
            "recent_query_directors": self._top_names(query_directors, limit=3),
            "recent_query_moods": self._top_names(query_moods, limit=3),
            "recent_reference_movies": self._top_names(query_reference_movies, limit=3),
            "recent_dialogue": recent_dialogue,
            "positive_signal_count": len(positive_ids),
            "negative_signal_count": len(negative_ids),
            "seen_movie_count": len(seen_ids),
        }

        return {
            key: value
            for key, value in learning_context.items()
            if value not in (None, [], {}, "", 0)
        }

    def _build_learning_hint(self, learning_context: dict) -> str:
        if not learning_context:
            return ""

        parts: list[str] = []
        liked_genres = learning_context.get("liked_genres", [])
        liked_movies = learning_context.get("liked_movies", [])
        recent_moods = learning_context.get("recent_query_moods", [])

        if liked_genres:
            parts.append(f"я учёл, что вам часто заходят {', '.join(liked_genres[:2])}")
        if liked_movies:
            parts.append(f"и фильмы вроде {', '.join(liked_movies[:2])}")
        if recent_moods:
            parts.append(f"а в последних запросах вы часто искали настроение {', '.join(recent_moods[:2])}")

        if not parts:
            return ""

        if len(parts) == 1:
            return parts[0].capitalize() + "."
        return " ".join([parts[0].capitalize(), *parts[1:]]) + "."

    def _fallback_answer(
        self,
        preferences: ParsedPreferences,
        recommendations: list[dict],
        learning_context: dict,
    ) -> str:
        if not recommendations:
            answer = (
                "Пока не нашёл достаточно подходящих фильмов в локальной базе. "
                "Попробуйте уточнить жанр, актёра, настроение или пример фильма."
            )
            learning_hint = self._build_learning_hint(learning_context)
            if learning_hint:
                answer = f"{answer} {learning_hint}"
            return answer

        top_movies = recommendations[:3]
        titles = ", ".join(item["title"] for item in top_movies)
        first_movie = top_movies[0]

        if preferences.reference_movie:
            intro = f"Если вам понравился {preferences.reference_movie}, попробуйте {titles}."
        elif preferences.genres:
            intro = f"Под запрос по жанру {' / '.join(preferences.genres)} хорошо подходят {titles}."
        elif preferences.actors:
            intro = f"Если хочется фильм с {', '.join(preferences.actors[:2])}, можно начать с {titles}."
        elif preferences.moods:
            intro = f"Под такое настроение лучше всего подходят {titles}."
        else:
            intro = f"По вашему запросу я бы начал с {titles}."

        if first_movie.get("reason"):
            answer = f"{intro} Лучшее совпадение сейчас: {first_movie['title']} — {first_movie['reason']}."
        else:
            answer = intro

        learning_hint = self._build_learning_hint(learning_context)
        if learning_hint:
            answer = f"{answer} {learning_hint}"
        return answer

    def build_answer(
        self,
        preferences: ParsedPreferences,
        recommendations: list[dict],
        user_message: str,
        learning_context: dict,
    ) -> str:
        if self._secondary_assistant is not None:
            try:
                answer = self._secondary_assistant.build_answer(
                    preferences=preferences,
                    recommendations=recommendations,
                    user_message=user_message,
                    learning_context=learning_context,
                )
            except Exception:
                answer = ""

            if isinstance(answer, str) and answer.strip():
                return answer.strip()

        return self._fallback_answer(preferences, recommendations, learning_context)

    def chat(self, db: Session, message: str, user_id: int | None = None, limit: int = 5) -> dict:
        if user_id is not None and db.get(User, user_id) is None:
            raise ValueError("Пользователь не найден.")

        learning_context = self._build_learning_context(db, user_id)
        preferences = nlp_parser.parse(db, message)
        recommendation_result = recommendation_engine.search_by_preferences(
            db,
            preferences=preferences,
            limit=limit,
            user_id=user_id,
        )
        recommendations = recommendation_result["recommendations"]
        warnings = list(recommendation_result["warnings"])
        strategy = recommendation_result["strategy"]
        if learning_context:
            strategy += "_with_user_learning"
            warnings.append("Ассистент учёл историю вкусов, поисков и прошлых диалогов пользователя.")

        answer = self.build_answer(
            preferences=preferences,
            recommendations=recommendations,
            user_message=message,
            learning_context=learning_context,
        )

        if user_id is not None:
            assistant_message = AssistantMessage(
                user_id=user_id,
                message=message,
                response=answer,
            )
            db.add(assistant_message)
            db.flush()

            for item in recommendations:
                db.add(
                    AssistantRecommendedMovie(
                        message_id=assistant_message.id,
                        movie_id=item["id"],
                        score=item["score"],
                        reason=item["reason"],
                    ),
                )

            db.add(SearchHistory(user_id=user_id, query=message))
            db.commit()

        return {
            "answer": answer,
            "movies": recommendations,
            "parsed_preferences": preferences.to_response(),
            "strategy": strategy,
            "fallback_used": recommendation_result["fallback_used"],
            "warnings": list(dict.fromkeys(warnings)),
        }


ai_assistant = AIAssistant()
