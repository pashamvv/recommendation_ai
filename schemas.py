from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class GenreRead(ORMModel):
    id: int
    tmdb_id: Optional[int] = None
    name: str


class ActorRead(ORMModel):
    id: int
    tmdb_id: Optional[int] = None
    name: str
    photo_url: Optional[str] = None
    popularity: float = 0.0


class DirectorRead(ORMModel):
    id: int
    tmdb_id: Optional[int] = None
    name: str
    photo_url: Optional[str] = None


class KeywordRead(ORMModel):
    id: int
    tmdb_id: Optional[int] = None
    name: str


class MovieCard(ORMModel):
    id: int
    tmdb_id: int
    title: str
    original_title: Optional[str] = None
    overview: Optional[str] = None
    release_date: Optional[date] = None
    rating: float
    poster_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    popularity: float
    runtime: Optional[int] = None
    status: Optional[str] = None
    language: Optional[str] = None


class MovieRead(MovieCard):
    genres: list[GenreRead] = Field(default_factory=list)
    actors: list[ActorRead] = Field(default_factory=list)
    directors: list[DirectorRead] = Field(default_factory=list)
    keywords: list[KeywordRead] = Field(default_factory=list)
    similar_movies: list[MovieCard] = Field(default_factory=list)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(min_length=6, max_length=255)
    role: str = "user"


class UserLogin(BaseModel):
    username_or_email: str
    password: str


class UserRead(ORMModel):
    id: int
    username: str
    email: EmailStr
    role_id: int
    role_name: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead


class RecommendationRequest(BaseModel):
    movie_id: int = Field(ge=1)
    user_id: Optional[int] = Field(default=None, ge=1)
    limit: int = Field(default=10, ge=1, le=30)


class RecommendationItem(MovieCard):
    score: int = Field(ge=0, le=100)
    reason: str


class RecommendationResponse(BaseModel):
    base_movie: Optional[MovieCard] = None
    recommendations: list[RecommendationItem]
    strategy: str = "hybrid_movie_similarity"
    fallback_used: bool = False
    warnings: list[str] = Field(default_factory=list)


class ParsedPreferencesRead(BaseModel):
    genres: list[str] = Field(default_factory=list)
    actors: list[str] = Field(default_factory=list)
    directors: list[str] = Field(default_factory=list)
    moods: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    reference_movie: Optional[str] = None
    reference_movie_id: Optional[int] = None
    free_text: Optional[str] = None


class AssistantChatRequest(BaseModel):
    message: str = Field(min_length=2, max_length=1000)
    user_id: Optional[int] = Field(default=None, ge=1)
    limit: int = Field(default=5, ge=1, le=20)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("Message must contain at least 2 visible characters.")
        return normalized


class AssistantChatResponse(BaseModel):
    answer: str
    movies: list[RecommendationItem]
    parsed_preferences: ParsedPreferencesRead
    strategy: str = "assistant_hybrid"
    fallback_used: bool = False
    warnings: list[str] = Field(default_factory=list)


class TMDBSyncRequest(BaseModel):
    source: Literal["popular", "now_playing", "top_rated", "upcoming"] = "popular"
    pages: int = Field(default=1, ge=1, le=10)
    language: str = "ru-RU"


class TMDBExcelExportRequest(TMDBSyncRequest):
    file_name: Optional[str] = None


class TMDBExcelExportResponse(BaseModel):
    source: str
    pages: int
    language: str
    file_path: str
    exported_movies: int
    exported_at: datetime


class TMDBExcelImportRequest(BaseModel):
    file_path: str


class TMDBExcelImportResponse(BaseModel):
    file_path: str
    processed_movies: int = 0
    imported_movies: int
    upserted_movies: int
    skipped_existing_movies: int = 0
    imported_at: datetime
    warnings: list[str] = Field(default_factory=list)


class SyncResponse(BaseModel):
    source: str
    pages: int
    synced_movies: int


class AssistantMessageRead(ORMModel):
    id: int
    user_id: int
    message: str
    response: str
    created_at: datetime
