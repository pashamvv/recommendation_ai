from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from faiss_service import FAISSServiceError, faiss_service
from recommendation_engine import recommendation_engine
from schemas import RecommendationRequest, RecommendationResponse


router = APIRouter(prefix="/recommendations", tags=["Рекомендации"])


@router.post(
    "",
    response_model=RecommendationResponse,
    summary="Получить AI-рекомендации по фильму",
    description="Возвращает список фильмов, похожих на выбранный фильм.",
)
def get_recommendations(payload: RecommendationRequest, db: Session = Depends(get_db)):
    try:
        return recommendation_engine.get_recommendations(
            db,
            movie_id=payload.movie_id,
            user_id=payload.user_id,
            limit=payload.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/rebuild-index",
    summary="Перестроить индекс рекомендаций",
    description="Пересобирает FAISS-индекс по сохраненным эмбеддингам фильмов.",
)
def rebuild_recommendation_index(db: Session = Depends(get_db)):
    try:
        indexed_movies = faiss_service.rebuild_index(db)
    except FAISSServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"indexed_movies": indexed_movies}
