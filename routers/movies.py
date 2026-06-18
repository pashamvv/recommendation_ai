from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session, selectinload
from typing import Literal

from embedding_service import EmbeddingServiceError, embedding_service
from excel_service import ExcelServiceError, excel_service
from faiss_service import FAISSServiceError, faiss_service
from database import get_db
from models import Genre, Movie
from recommendation_engine import recommendation_engine
from schemas import (
    MovieCard,
    MovieRead,
    TMDBExcelImportResponse,
)
from tmdb_service import TMDBServiceError, tmdb_service


router = APIRouter(prefix="/movies", tags=["Фильмы"])


def movie_query(db: Session):
    return db.query(Movie).options(
        selectinload(Movie.genres),
        selectinload(Movie.actors),
        selectinload(Movie.directors),
        selectinload(Movie.keywords),
        selectinload(Movie.similar_movies),
        selectinload(Movie.embedding),
    )


@router.post(
    "/parse/tmdb-to-excel",
    summary="Спарсить TMDB и скачать Excel",
    description="Загружает фильмы из TMDB и сразу отдает Excel-файл для скачивания.",
    response_class=FileResponse,
    responses={
        200: {
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
            },
            "description": "Сформированный Excel-файл.",
        },
    },
)
def parse_tmdb_to_excel(
    source: Literal["popular", "now_playing", "top_rated", "upcoming"] = Form(default="popular"),
    pages: int = Form(default=1, ge=1, le=10),
    language: str = Form(default="ru-RU"),
    file_name: str | None = Form(default=None),
):
    try:
        result = excel_service.export_tmdb_to_excel(
            source=source,
            pages=pages,
            language=language,
            file_name=file_name,
        )
        file_path = Path(result["file_path"])
        return FileResponse(
            path=file_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=file_path.name,
        )
    except ExcelServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TMDBServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка экспорта TMDB: {exc}") from exc


@router.post(
    "/import/excel-to-db",
    response_model=TMDBExcelImportResponse,
    summary="Загрузить Excel в базу данных",
    description="Принимает .xlsx файл, читает фильмы и связи из него и сохраняет данные в БД.",
)
async def import_excel_to_db(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    try:
        if not file.filename:
            raise ExcelServiceError("У загруженного файла отсутствует имя.")
        if not file.filename.lower().endswith(".xlsx"):
            raise ExcelServiceError("Поддерживаются только файлы .xlsx.")

        content = await file.read()
        if not content:
            raise ExcelServiceError("Загруженный файл пуст.")

        result = excel_service.import_tmdb_from_bytes(
            db,
            content=content,
            file_name=file.filename,
        )
        warnings: list[str] = []
        skipped_existing_movies = result.get("skipped_existing_movies", 0)
        imported_movies_count = result.get("imported_movies", 0)
        if skipped_existing_movies:
            warnings.append(
                f"Пропущено уже существующих фильмов: {skipped_existing_movies}. Импорт добавляет только отсутствующие записи."
            )
        if imported_movies_count == 0 and skipped_existing_movies:
            warnings.append("Новых фильмов для добавления не найдено.")
        imported_ids = result.pop("imported_movie_ids", [])
        if imported_ids:
            imported_movies = movie_query(db).filter(Movie.id.in_(imported_ids)).all()
            if imported_movies:
                try:
                    embedding_service.ensure_embeddings(db, imported_movies)
                except EmbeddingServiceError as exc:
                    warnings.append(f"Эмбеддинги не были обновлены: {exc}")

                try:
                    faiss_service.rebuild_index(db)
                except FAISSServiceError as exc:
                    warnings.append(f"FAISS-индекс не был обновлен: {exc}")

        result["warnings"] = warnings
        return result
    except ExcelServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка импорта Excel: {exc}") from exc
    finally:
        await file.close()


@router.get(
    "",
    response_model=list[MovieCard],
    summary="Получить список фильмов",
    description="Возвращает список фильмов с поиском по названию и фильтром по жанру.",
)
def list_movies(
    db: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
    genre: str | None = None,
):
    query = movie_query(db)
    if search:
        query = query.filter(
            or_(
                Movie.title.ilike(f"%{search}%"),
                Movie.original_title.ilike(f"%{search}%"),
            ),
        )
    if genre:
        query = query.join(Movie.genres).filter(Genre.name.ilike(f"%{genre}%"))

    return (
        query.order_by(desc(Movie.popularity), desc(Movie.rating))
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.get(
    "/all",
    response_model=list[MovieCard],
    summary="Получить все фильмы из базы",
    description="Возвращает полный список фильмов из базы данных без пагинации.",
)
def get_all_movies(
    db: Session = Depends(get_db),
):
    return movie_query(db).order_by(desc(Movie.created_at), desc(Movie.id)).all()


@router.get(
    "/popular",
    response_model=list[MovieCard],
    summary="Получить популярные фильмы",
    description="Возвращает подборку самых популярных фильмов.",
)
def get_popular_movies(
    db: Session = Depends(get_db),
    limit: int = Query(default=12, ge=1, le=50),
):
    return movie_query(db).order_by(desc(Movie.popularity), desc(Movie.rating)).limit(limit).all()


@router.get(
    "/new",
    response_model=list[MovieCard],
    summary="Получить новинки",
    description="Возвращает фильмы с самой свежей датой выхода.",
)
def get_new_movies(
    db: Session = Depends(get_db),
    limit: int = Query(default=12, ge=1, le=50),
):
    return movie_query(db).order_by(desc(Movie.release_date), desc(Movie.popularity)).limit(limit).all()


@router.get(
    "/genres/{genre_name}",
    response_model=list[MovieCard],
    summary="Получить фильмы по жанру",
    description="Возвращает фильмы, подходящие под выбранный жанр.",
)
def get_movies_by_genre(
    genre_name: str,
    db: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=50),
):
    return (
        movie_query(db)
        .join(Movie.genres)
        .filter(Genre.name.ilike(f"%{genre_name}%"))
        .order_by(desc(Movie.popularity), desc(Movie.rating))
        .limit(limit)
        .all()
    )


@router.get(
    "/{movie_id}/similar",
    response_model=list[MovieCard],
    summary="Получить похожие фильмы",
    description="Возвращает похожие фильмы из TMDB или из AI-рекомендаций.",
)
def get_similar_movies(
    movie_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(default=10, ge=1, le=30),
):
    movie = movie_query(db).filter(Movie.id == movie_id).first()
    if movie is None:
        raise HTTPException(status_code=404, detail="Movie not found.")

    if movie.similar_movies:
        ranked = sorted(
            movie.similar_movies,
            key=lambda item: (float(item.popularity or 0), float(item.rating or 0)),
            reverse=True,
        )
        return ranked[:limit]

    return recommendation_engine.get_recommendations(db, movie_id=movie_id, limit=limit)["recommendations"]


@router.get(
    "/{movie_id}",
    response_model=MovieRead,
    summary="Получить фильм по ID",
    description="Возвращает полную информацию о фильме, его жанрах, актерах, режиссерах и ключевых словах.",
)
def get_movie(movie_id: int, db: Session = Depends(get_db)):
    movie = movie_query(db).filter(Movie.id == movie_id).first()
    if movie is None:
        raise HTTPException(status_code=404, detail="Movie not found.")
    return movie
