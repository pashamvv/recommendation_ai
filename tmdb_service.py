from __future__ import annotations

from datetime import datetime
import time

import requests
from requests.adapters import HTTPAdapter
from sqlalchemy import delete, insert
from sqlalchemy.orm import Session
from urllib3.util.retry import Retry

from config import settings
from models import Actor, Director, Genre, Keyword, Movie, movie_actors


class TMDBServiceError(RuntimeError):
    pass


class TMDBService:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.trust_env = False
        retry_strategy = Retry(
            total=0,
            connect=0,
            read=0,
            redirect=0,
            status=0,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    source_endpoints = {
        "popular": "movie/popular",
        "now_playing": "movie/now_playing",
        "top_rated": "movie/top_rated",
        "upcoming": "movie/upcoming",
    }

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not settings.tmdb_api_key:
            raise TMDBServiceError("TMDB_API_KEY не настроен в файле .env.")

        request_params = dict(params or {})
        headers = {"Accept": "application/json"}

        # TMDB supports either a classic v3 api_key or a v4 bearer token.
        if self._looks_like_bearer_token(settings.tmdb_api_key):
            headers["Authorization"] = f"Bearer {settings.tmdb_api_key}"
        else:
            request_params["api_key"] = settings.tmdb_api_key

        last_error: Exception | None = None
        for attempt in range(settings.tmdb_retry_attempts + 1):
            try:
                response = self.session.get(
                    f"{settings.tmdb_base_url}/{path.lstrip('/')}",
                    params=request_params,
                    headers=headers,
                    timeout=settings.tmdb_timeout_seconds,
                )
                if response.status_code in {429, 502, 503, 504} and attempt < settings.tmdb_retry_attempts:
                    time.sleep(1 + attempt)
                    continue
                response.raise_for_status()
                return response.json()
            except requests.Timeout as exc:
                last_error = exc
                if attempt < settings.tmdb_retry_attempts:
                    time.sleep(1 + attempt)
                    continue
                raise TMDBServiceError(
                    "TMDB не ответил вовремя после нескольких попыток. Попробуйте снова или уменьшите количество страниц.",
                ) from exc
            except requests.HTTPError as exc:
                last_error = exc
                if (
                    exc.response is not None
                    and exc.response.status_code in {429, 502, 503, 504}
                    and attempt < settings.tmdb_retry_attempts
                ):
                    time.sleep(1 + attempt)
                    continue
                raise TMDBServiceError(f"TMDB вернул ошибку: {response.text}") from exc
            except requests.RequestException as exc:
                last_error = exc
                raise TMDBServiceError(f"Не удалось подключиться к TMDB: {exc}") from exc

        raise TMDBServiceError(f"Не удалось выполнить запрос к TMDB: {last_error}")

    def _looks_like_bearer_token(self, token: str) -> bool:
        return token.count(".") == 2 and token.startswith("eyJ")

    def build_image_url(self, path: str | None) -> str | None:
        if not path:
            return None
        return f"{settings.tmdb_image_base_url}{path}"

    def parse_date(self, value: str | None):
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None

    def fetch_source_page(self, source: str, page: int, language: str) -> dict:
        endpoint = self.source_endpoints.get(source)
        if endpoint is None:
            raise TMDBServiceError(f"Неподдерживаемый источник TMDB: {source}")
        return self._get(endpoint, {"page": page, "language": language})

    def fetch_genre_map(self, language: str) -> dict[int, str]:
        payload = self._get("genre/movie/list", {"language": language})
        return {
            int(item["id"]): item["name"]
            for item in payload.get("genres", [])
            if item.get("id") is not None and item.get("name")
        }

    def get_movie_bundle(self, tmdb_id: int, language: str) -> dict:
        details = self._get(f"movie/{tmdb_id}", {"language": language})
        credits = self._get(f"movie/{tmdb_id}/credits", {"language": language})
        keywords = self._get(f"movie/{tmdb_id}/keywords")
        similar = self._get(f"movie/{tmdb_id}/similar", {"language": language, "page": 1})
        return {
            "details": details,
            "credits": credits,
            "keywords": keywords,
            "similar": similar,
        }

    def get_movie_export_bundle(self, tmdb_id: int, language: str) -> dict:
        details = self._get(
            f"movie/{tmdb_id}",
            {
                "language": language,
                "append_to_response": "credits,keywords",
            },
        )
        return {
            "details": details,
            "credits": details.get("credits", {}),
            "keywords": details.get("keywords", {}),
        }

    def sync_movies(self, db: Session, source: str, pages: int, language: str) -> list[int]:
        synced_movie_ids: list[int] = []
        for page in range(1, pages + 1):
            payload = self.fetch_source_page(source=source, page=page, language=language)
            for item in payload.get("results", []):
                movie = self.sync_movie(db, tmdb_id=item["id"], language=language)
                synced_movie_ids.append(movie.id)
            db.commit()
        return synced_movie_ids

    def sync_movie(self, db: Session, tmdb_id: int, language: str) -> Movie:
        bundle = self.get_movie_bundle(tmdb_id=tmdb_id, language=language)
        movie = self._upsert_movie(db, bundle["details"])
        self._sync_genres(db, movie, bundle["details"].get("genres", []))
        self._sync_actors(db, movie, bundle["credits"].get("cast", []))
        self._sync_directors(db, movie, bundle["credits"].get("crew", []))
        self._sync_keywords(db, movie, bundle["keywords"].get("keywords") or bundle["keywords"].get("results", []))
        self._sync_similar_movies(db, movie, bundle["similar"].get("results", []))
        db.flush()
        return movie

    def _upsert_movie(self, db: Session, payload: dict) -> Movie:
        movie = db.query(Movie).filter(Movie.tmdb_id == payload["id"]).first()
        if movie is None:
            movie = Movie(tmdb_id=payload["id"])
            db.add(movie)

        movie.title = payload.get("title") or payload.get("name") or "Untitled"
        if payload.get("original_title") is not None:
            movie.original_title = payload.get("original_title")
        if payload.get("overview") is not None:
            movie.overview = payload.get("overview")
        if payload.get("release_date") is not None:
            movie.release_date = self.parse_date(payload.get("release_date"))
        movie.rating = payload.get("vote_average") or 0
        movie.popularity = payload.get("popularity") or 0
        poster_url = self.build_image_url(payload.get("poster_path"))
        backdrop_url = self.build_image_url(payload.get("backdrop_path"))
        if poster_url is not None:
            movie.poster_url = poster_url
        if backdrop_url is not None:
            movie.backdrop_url = backdrop_url
        if "runtime" in payload and payload.get("runtime") is not None:
            movie.runtime = payload.get("runtime")
        if "status" in payload and payload.get("status") is not None:
            movie.status = payload.get("status")
        if payload.get("original_language") is not None:
            movie.language = payload.get("original_language")
        db.flush()
        return movie

    def _sync_genres(self, db: Session, movie: Movie, genres_payload: list[dict]) -> None:
        genres: list[Genre] = []
        for item in genres_payload:
            genre = db.query(Genre).filter(Genre.tmdb_id == item["id"]).first()
            if genre is None:
                genre = db.query(Genre).filter(Genre.name == item["name"]).first()
            if genre is None:
                genre = Genre(tmdb_id=item["id"], name=item["name"])
                db.add(genre)
                db.flush()
            genres.append(genre)
        movie.genres = genres

    def _sync_actors(self, db: Session, movie: Movie, cast_payload: list[dict]) -> None:
        rows: list[dict] = []
        for actor_payload in cast_payload[: settings.max_actor_matches]:
            actor = db.query(Actor).filter(Actor.tmdb_id == actor_payload["id"]).first()
            if actor is None:
                actor = Actor(tmdb_id=actor_payload["id"])
                db.add(actor)

            actor.name = actor_payload.get("name") or actor_payload.get("original_name") or "Unknown"
            actor.photo_url = self.build_image_url(actor_payload.get("profile_path"))
            actor.popularity = actor_payload.get("popularity") or 0
            db.flush()

            rows.append(
                {
                    "movie_id": movie.id,
                    "actor_id": actor.id,
                    "character_name": actor_payload.get("character"),
                    "cast_order": actor_payload.get("order"),
                },
            )

        db.execute(delete(movie_actors).where(movie_actors.c.movie_id == movie.id))
        if rows:
            db.execute(insert(movie_actors), rows)
        db.expire(movie, ["actors"])

    def _sync_directors(self, db: Session, movie: Movie, crew_payload: list[dict]) -> None:
        directors: list[Director] = []
        for member in crew_payload:
            if member.get("job") != "Director":
                continue
            director = db.query(Director).filter(Director.tmdb_id == member["id"]).first()
            if director is None:
                director = Director(tmdb_id=member["id"])
                db.add(director)

            director.name = member.get("name") or member.get("original_name") or "Unknown"
            director.photo_url = self.build_image_url(member.get("profile_path"))
            db.flush()
            directors.append(director)

        movie.directors = directors

    def _sync_keywords(self, db: Session, movie: Movie, keywords_payload: list[dict]) -> None:
        keywords: list[Keyword] = []
        for item in keywords_payload:
            keyword = None
            if item.get("id") is not None:
                keyword = db.query(Keyword).filter(Keyword.tmdb_id == item["id"]).first()
            if keyword is None:
                keyword = db.query(Keyword).filter(Keyword.name == item["name"]).first()
            if keyword is None:
                keyword = Keyword(tmdb_id=item.get("id"), name=item["name"])
                db.add(keyword)
                db.flush()
            keywords.append(keyword)
        movie.keywords = keywords

    def _sync_similar_movies(self, db: Session, movie: Movie, similar_payload: list[dict]) -> None:
        similar_movies: list[Movie] = []
        for item in similar_payload[:20]:
            similar_movie = self._upsert_movie(db, item)
            if similar_movie.id != movie.id:
                similar_movies.append(similar_movie)
        movie.similar_movies = similar_movies


tmdb_service = TMDBService()
