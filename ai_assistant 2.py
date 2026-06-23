from __future__ import annotations

import requests
from sqlalchemy.orm import Session

from models import AssistantMessage, AssistantRecommendedMovie, SearchHistory
from nlp_parser import ParsedPreferences, nlp_parser
from recommendation_engine import recommendation_engine


OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.1"


def local_llm_generate(prompt: str) -> str:
    """
    Генерация ответа через локальную LLM-модель Ollama.
    """

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.7,
                    "top_p": 0.9,
                },
            },
            timeout=60,
        )

        response.raise_for_status()
        data = response.json()

        return data.get("response", "").strip()

    except requests.exceptions.RequestException:
        return ""


class AIAssistant:
    def format_learning_context(self, learning_context: dict | None) -> str:
        if not learning_context:
            return "Пока накопленных пользовательских сигналов мало."

        lines: list[str] = []

        liked_movies = learning_context.get("liked_movies", [])
        if liked_movies:
            lines.append(f"- Уже понравившиеся фильмы: {', '.join(liked_movies[:4])}")

        liked_genres = learning_context.get("liked_genres", [])
        if liked_genres:
            lines.append(f"- Чаще всего нравятся жанры: {', '.join(liked_genres[:4])}")

        liked_actors = learning_context.get("liked_actors", [])
        if liked_actors:
            lines.append(f"- Любимые актеры по истории: {', '.join(liked_actors[:3])}")

        liked_directors = learning_context.get("liked_directors", [])
        if liked_directors:
            lines.append(f"- Любимые режиссеры по истории: {', '.join(liked_directors[:3])}")

        avoided_genres = learning_context.get("avoided_genres", [])
        if avoided_genres:
            lines.append(f"- Жанры, которые чаще не заходят: {', '.join(avoided_genres[:3])}")

        recent_query_genres = learning_context.get("recent_query_genres", [])
        if recent_query_genres:
            lines.append(f"- В недавних запросах часто искались жанры: {', '.join(recent_query_genres[:4])}")

        recent_query_moods = learning_context.get("recent_query_moods", [])
        if recent_query_moods:
            lines.append(f"- В недавних запросах часто встречались настроения: {', '.join(recent_query_moods[:3])}")

        recent_reference_movies = learning_context.get("recent_reference_movies", [])
        if recent_reference_movies:
            lines.append(f"- Пользователь часто ориентируется на фильмы: {', '.join(recent_reference_movies[:3])}")

        recent_dialogue = learning_context.get("recent_dialogue", [])
        if recent_dialogue:
            lines.append("- Последние диалоги:")
            for item in recent_dialogue[-2:]:
                user_text = (item.get("user") or "").strip()
                assistant_text = (item.get("assistant") or "").strip()
                if user_text:
                    lines.append(f"  Пользователь: {user_text[:140]}")
                if assistant_text:
                    lines.append(f"  Ассистент: {assistant_text[:160]}")

        return "\n".join(lines) if lines else "Пока накопленных пользовательских сигналов мало."

    def build_prompt(
        self,
        preferences: ParsedPreferences,
        recommendations: list[dict],
        user_message: str,
        learning_context: dict | None = None,
    ) -> str:
        movies_text = "\n".join(
            f"""
Фильм: {item.get("title", "Без названия")}
Причина рекомендации: {item.get("reason", "подходит под запрос пользователя")}
Оценка совпадения: {item.get("score", 0)}
"""
            for item in recommendations[:5]
        )

        parsed_text = preferences.to_response()
        learning_text = self.format_learning_context(learning_context)

        return f"""
Ты — AI-ассистент онлайн-кинотеатра NoctaFilm.

Твоя задача:
- отвечать живо, понятно и по-человечески;
- рекомендовать фильмы;
- объяснять, почему фильм подходит;
- не писать сухими шаблонами;
- не придумывать фильмы, которых нет в списке рекомендаций;
- отвечать только на русском языке.

Запрос пользователя:
{user_message}

Распознанные предпочтения пользователя:
{parsed_text}

Чему ты уже научился о пользователе:
{learning_text}

Найденные фильмы из локальной базы:
{movies_text}

Сформируй красивый ответ пользователю.
Если фильмов несколько — предложи 3–5 вариантов.
Если есть причина рекомендации — объясни её простыми словами.
Используй накопленный профиль пользователя как мягкий приоритет.
Если текущий запрос явно просит другое настроение, жанр или фильм, текущий запрос важнее старых привычек.
"""

    def fallback_answer(
        self,
        preferences: ParsedPreferences,
        recommendations: list[dict],
        learning_context: dict | None = None,
    ) -> str:
        if not recommendations:
            answer = (
                "Пока не нашёл достаточно подходящих фильмов в локальной базе. "
                "Уточни жанр, актёра, настроение или пример фильма, который тебе понравился."
            )
            liked_genres = (learning_context or {}).get("liked_genres", [])
            if liked_genres:
                answer += f" Я запомнил, что тебе часто заходят {', '.join(liked_genres[:2])}."
            return answer

        titles = ", ".join(item.get("title", "Без названия") for item in recommendations[:3])

        if preferences.reference_movie:
            intro = f"Если тебе понравился {preferences.reference_movie}, можно попробовать {titles}."
        elif preferences.genres:
            intro = f"Под твой запрос по жанру {' / '.join(preferences.genres)} подойдут {titles}."
        elif preferences.actors:
            intro = f"Если хочется фильм с {', '.join(preferences.actors[:2])}, посмотри {titles}."
        elif preferences.moods:
            intro = f"Под такое настроение хорошо подойдут {titles}."
        else:
            intro = f"Я бы предложил тебе {titles}."

        extra_reason = recommendations[0].get("reason", "")
        learned_hint = ""
        liked_genres = (learning_context or {}).get("liked_genres", [])
        liked_movies = (learning_context or {}).get("liked_movies", [])
        if liked_genres:
            learned_hint = f" Я учёл, что тебе часто заходят {', '.join(liked_genres[:2])}."
        elif liked_movies:
            learned_hint = f" Я помню, что тебе нравились фильмы вроде {', '.join(liked_movies[:2])}."

        if extra_reason:
            return f"{intro} Первый вариант особенно хорош, потому что {extra_reason}.{learned_hint}"

        return intro + learned_hint

    def build_answer(
        self,
        preferences: ParsedPreferences,
        recommendations: list[dict],
        user_message: str,
        learning_context: dict | None = None,
    ) -> str:
        if not recommendations:
            return self.fallback_answer(preferences, recommendations, learning_context)

        prompt = self.build_prompt(
            preferences=preferences,
            recommendations=recommendations,
            user_message=user_message,
            learning_context=learning_context,
        )

        llm_answer = local_llm_generate(prompt)

        if llm_answer:
            return llm_answer

        return self.fallback_answer(preferences, recommendations, learning_context)

    def chat(
        self,
        db: Session,
        message: str,
        user_id: int | None = None,
        limit: int = 5,
    ) -> dict:
        preferences = nlp_parser.parse(db, message)

        recommendations = recommendation_engine.search_by_preferences(
            db,
            preferences=preferences,
            limit=limit,
            user_id=user_id,
        )

        answer = self.build_answer(
            preferences=preferences,
            recommendations=recommendations,
            user_message=message,
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
                        score=item.get("score", 0),
                        reason=item.get("reason", ""),
                    )
                )

            db.add(
                SearchHistory(
                    user_id=user_id,
                    query=message,
                )
            )

            db.commit()

        return {
            "answer": answer,
            "movies": recommendations,
            "parsed_preferences": preferences.to_response(),
        }


ai_assistant = AIAssistant()
