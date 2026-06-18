from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from models import Actor, Director, Genre, Movie


@dataclass
class ParsedPreferences:
    genres: list[str] = field(default_factory=list)
    actors: list[str] = field(default_factory=list)
    directors: list[str] = field(default_factory=list)
    moods: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    reference_movie: str | None = None
    reference_movie_id: int | None = None
    free_text: str | None = None

    def to_response(self) -> dict:
        return {
            "genres": self.genres,
            "actors": self.actors,
            "directors": self.directors,
            "moods": self.moods,
            "keywords": self.keywords,
            "reference_movie": self.reference_movie,
            "reference_movie_id": self.reference_movie_id,
            "free_text": self.free_text,
        }


class NLPParser:
    mood_map = {
        "груст": "melancholic",
        "печаль": "melancholic",
        "страш": "scary",
        "ужас": "scary",
        "смеш": "funny",
        "весел": "funny",
        "романт": "romantic",
        "добрый": "warm",
        "легк": "easy",
        "вечер": "evening",
        "напряж": "tense",
        "динами": "dynamic",
    }

    genre_synonyms = {
        "фантаст": "фантастика",
        "ужас": "ужасы",
        "боев": "боевик",
        "комед": "комедия",
        "детектив": "детектив",
        "триллер": "триллер",
        "драм": "драма",
        "мелодрам": "мелодрама",
        "приключ": "приключения",
        "кримин": "криминал",
        "фэнтез": "фэнтези",
        "мульт": "мультфильм",
    }

    stopwords = {
        "посоветуй",
        "посоветовать",
        "хочу",
        "посмотреть",
        "что",
        "нибудь",
        "какой",
        "какое",
        "какую",
        "фильм",
        "сериал",
        "мне",
        "для",
        "про",
        "как",
        "похожее",
        "похожий",
        "вечером",
        "вечер",
    }

    def normalize(self, text: str) -> str:
        return re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()

    def parse(self, db: Session, message: str) -> ParsedPreferences:
        normalized = self.normalize(message)
        preferences = ParsedPreferences(free_text=message)

        preferences.genres = self._match_genres(db, normalized)
        preferences.actors = self._match_people(db, normalized, Actor)
        preferences.directors = self._match_people(db, normalized, Director)
        preferences.moods = self._match_moods(normalized)

        reference_movie = self._match_reference_movie(db, message, normalized)
        if reference_movie is not None:
            preferences.reference_movie = reference_movie.title
            preferences.reference_movie_id = reference_movie.id

        preferences.keywords = self._extract_keywords(normalized, preferences)
        return preferences

    def _match_genres(self, db: Session, normalized: str) -> list[str]:
        matches = set()
        for fragment, canonical in self.genre_synonyms.items():
            if fragment in normalized:
                matches.add(canonical)

        for genre in db.query(Genre).all():
            if self.normalize(genre.name) in normalized:
                matches.add(genre.name)
        return sorted(matches)

    def _match_people(self, db: Session, normalized: str, model) -> list[str]:
        matches = []
        for person in db.query(model).all():
            person_name = self.normalize(person.name)
            if person_name and person_name in normalized:
                matches.append(person.name)
        return sorted(set(matches))

    def _match_moods(self, normalized: str) -> list[str]:
        moods = []
        for fragment, mood in self.mood_map.items():
            if fragment in normalized:
                moods.append(mood)
        return sorted(set(moods))

    def _match_reference_movie(self, db: Session, raw_message: str, normalized: str) -> Movie | None:
        patterns = [
            r"как\s+(.+)$",
            r"похож(?:ее|ий|ую)?\s+на\s+(.+)$",
            r"вроде\s+(.+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if not match:
                continue
            fragment = match.group(1).strip(" ?!.")
            movie = (
                db.query(Movie)
                .filter(
                    (Movie.title.ilike(f"%{fragment}%"))
                    | (Movie.original_title.ilike(f"%{fragment}%"))
                )
                .first()
            )
            if movie is not None:
                return movie

        candidates = db.query(Movie).all()
        candidates.sort(key=lambda movie: len(movie.title or ""), reverse=True)
        normalized_message = self.normalize(raw_message)
        for movie in candidates:
            title = self.normalize(movie.title or "")
            original_title = self.normalize(movie.original_title or "")
            if title and title in normalized_message:
                return movie
            if original_title and original_title in normalized_message:
                return movie
        return None

    def _extract_keywords(self, normalized: str, preferences: ParsedPreferences) -> list[str]:
        occupied = {
            self.normalize(preferences.reference_movie or ""),
            *[self.normalize(name) for name in preferences.genres],
            *[self.normalize(name) for name in preferences.actors],
            *[self.normalize(name) for name in preferences.directors],
        }

        keywords = []
        for token in re.findall(r"[a-zA-Zа-яА-Я0-9]+", normalized):
            if len(token) < 4:
                continue
            if token in self.stopwords or token in occupied:
                continue
            keywords.append(token)
        return sorted(set(keywords))


nlp_parser = NLPParser()
