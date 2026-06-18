from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ai_assistant import ai_assistant
from database import get_db
from schemas import AssistantChatRequest, AssistantChatResponse


router = APIRouter(prefix="/assistant", tags=["AI-ассистент"])


@router.post(
    "/chat",
    response_model=AssistantChatResponse,
    summary="Отправить сообщение ассистенту",
    description="Анализирует текст запроса пользователя и возвращает подходящие рекомендации.",
)
def assistant_chat(
    payload: AssistantChatRequest,
    db: Session = Depends(get_db),
):
    try:
        return ai_assistant.chat(
            db,
            message=payload.message,
            user_id=payload.user_id,
            limit=payload.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
