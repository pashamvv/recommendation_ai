from __future__ import annotations

from sqlalchemy.orm import Session

from models import AssistantMessage, AssistantRecommendedMovie, SearchHistory, User
from nlp_parser import ParsedPreferences, nlp_parser
from recommendation_engine import recommendation_engine


class AIAssistant:
    def build_answer(self, preferences: ParsedPreferences, recommendations: list[dict]) -> str:
        if not recommendations:
            return (
                "Пока не нашёл достаточно подходящих фильмов в локальной базе. "
                "Попробуйте уточнить жанр, актёра, настроение или пример фильма."
            )

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
            return f"{intro} Лучшее совпадение сейчас: {first_movie['title']} — {first_movie['reason']}."
        return intro

    def chat(self, db: Session, message: str, user_id: int | None = None, limit: int = 5) -> dict:
        if user_id is not None and db.get(User, user_id) is None:
            raise ValueError("Пользователь не найден.")

        preferences = nlp_parser.parse(db, message)
        recommendation_result = recommendation_engine.search_by_preferences(
            db,
            preferences=preferences,
            limit=limit,
            user_id=user_id,
        )
        recommendations = recommendation_result["recommendations"]
        answer = self.build_answer(preferences, recommendations)

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
            "strategy": recommendation_result["strategy"],
            "fallback_used": recommendation_result["fallback_used"],
            "warnings": recommendation_result["warnings"],
        }


ai_assistant = AIAssistant()
