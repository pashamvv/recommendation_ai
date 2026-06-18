from datetime import datetime

from sqlalchemy import JSON, Column, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Table, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship

from database import Base


movie_genres = Table(
    "movie_genres",
    Base.metadata,
    Column("movie_id", ForeignKey("movies.movie_id", ondelete="CASCADE"), primary_key=True),
    Column("genre_id", ForeignKey("genres.genre_id", ondelete="CASCADE"), primary_key=True),
)

movie_actors = Table(
    "movie_actors",
    Base.metadata,
    Column("movie_id", ForeignKey("movies.movie_id", ondelete="CASCADE"), primary_key=True),
    Column("actor_id", ForeignKey("actors.actor_id", ondelete="CASCADE"), primary_key=True),
    Column("character_name", String(255), nullable=True),
    Column("cast_order", Integer, nullable=True),
)

movie_directors = Table(
    "movie_directors",
    Base.metadata,
    Column("movie_id", ForeignKey("movies.movie_id", ondelete="CASCADE"), primary_key=True),
    Column("director_id", ForeignKey("directors.director_id", ondelete="CASCADE"), primary_key=True),
)

movie_keywords = Table(
    "movie_keywords",
    Base.metadata,
    Column("movie_id", ForeignKey("movies.movie_id", ondelete="CASCADE"), primary_key=True),
    Column("keyword_id", ForeignKey("keywords.keyword_id", ondelete="CASCADE"), primary_key=True),
)

similar_movies_association = Table(
    "similar_movies",
    Base.metadata,
    Column("movie_id", ForeignKey("movies.movie_id", ondelete="CASCADE"), primary_key=True),
    Column("similar_movie_id", ForeignKey("movies.movie_id", ondelete="CASCADE"), primary_key=True),
)


class Role(Base):
    __tablename__ = "roles"

    id = Column("role_id", Integer, primary_key=True, index=True)
    name = Column("role_name", String(50), nullable=False, unique=True)

    users = relationship("User", back_populates="role")


class User(Base):
    __tablename__ = "users"

    id = Column("user_id", Integer, primary_key=True, index=True)
    username = Column(String(100), nullable=False, index=True)
    email = Column(String(150), unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False)
    role_id = Column(Integer, ForeignKey("roles.role_id", ondelete="RESTRICT"), nullable=False, default=2)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    role = relationship("Role", back_populates="users")
    assistant_messages = relationship("AssistantMessage", back_populates="user", cascade="all, delete-orphan")
    watch_history = relationship("WatchHistory", back_populates="user", cascade="all, delete-orphan")
    favorites = relationship("Favorite", back_populates="user", cascade="all, delete-orphan")
    ratings = relationship("UserRating", back_populates="user", cascade="all, delete-orphan")
    reactions = relationship("UserReaction", back_populates="user", cascade="all, delete-orphan")
    search_history = relationship("SearchHistory", back_populates="user", cascade="all, delete-orphan")

    @property
    def role_name(self) -> str | None:
        return self.role.name if self.role else None


class Movie(Base):
    __tablename__ = "movies"

    id = Column("movie_id", Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, unique=True, nullable=False, index=True)
    title = Column(String(255), nullable=False, index=True)
    original_title = Column(String(255), nullable=True)
    overview = Column(Text, nullable=True)
    release_date = Column(Date, nullable=True)
    rating = Column(Numeric(3, 1), nullable=False, default=0)
    popularity = Column(Numeric(10, 3), nullable=False, default=0)
    poster_url = Column(Text, nullable=True)
    backdrop_url = Column(Text, nullable=True)
    runtime = Column(Integer, nullable=True)
    status = Column(String(100), nullable=True)
    language = Column(String(20), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    genres = relationship("Genre", secondary=movie_genres, back_populates="movies")
    actors = relationship("Actor", secondary=movie_actors, back_populates="movies")
    directors = relationship("Director", secondary=movie_directors, back_populates="movies")
    keywords = relationship("Keyword", secondary=movie_keywords, back_populates="movies")
    similar_movies = relationship(
        "Movie",
        secondary=similar_movies_association,
        primaryjoin=id == similar_movies_association.c.movie_id,
        secondaryjoin=id == similar_movies_association.c.similar_movie_id,
    )
    embedding = relationship(
        "MovieEmbedding",
        back_populates="movie",
        uselist=False,
        cascade="all, delete-orphan",
    )
    recommendation_sources = relationship(
        "RecommendationCache",
        back_populates="source_movie",
        cascade="all, delete-orphan",
        foreign_keys="RecommendationCache.source_movie_id",
    )
    recommendation_targets = relationship(
        "RecommendationCache",
        back_populates="recommended_movie",
        cascade="all, delete-orphan",
        foreign_keys="RecommendationCache.recommended_movie_id",
    )
    watch_history = relationship("WatchHistory", back_populates="movie", cascade="all, delete-orphan")
    favorites = relationship("Favorite", back_populates="movie", cascade="all, delete-orphan")
    ratings = relationship("UserRating", back_populates="movie", cascade="all, delete-orphan")
    reactions = relationship("UserReaction", back_populates="movie", cascade="all, delete-orphan")
    assistant_recommendations = relationship(
        "AssistantRecommendedMovie",
        back_populates="movie",
        cascade="all, delete-orphan",
    )


class Genre(Base):
    __tablename__ = "genres"

    id = Column("genre_id", Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, unique=True, nullable=True)
    name = Column("genre_name", String(100), unique=True, nullable=False, index=True)

    movies = relationship("Movie", secondary=movie_genres, back_populates="genres")


class Actor(Base):
    __tablename__ = "actors"

    id = Column("actor_id", Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, unique=True, nullable=True, index=True)
    name = Column("actor_name", String(255), nullable=False, index=True)
    photo_url = Column(Text, nullable=True)
    popularity = Column(Numeric(10, 3), nullable=False, default=0)

    movies = relationship("Movie", secondary=movie_actors, back_populates="actors")


class Director(Base):
    __tablename__ = "directors"

    id = Column("director_id", Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, unique=True, nullable=True, index=True)
    name = Column("director_name", String(255), nullable=False, index=True)
    photo_url = Column(Text, nullable=True)

    movies = relationship("Movie", secondary=movie_directors, back_populates="directors")


class Keyword(Base):
    __tablename__ = "keywords"

    id = Column("keyword_id", Integer, primary_key=True, index=True)
    tmdb_id = Column(Integer, unique=True, nullable=True, index=True)
    name = Column("keyword_name", String(150), unique=True, nullable=False, index=True)

    movies = relationship("Movie", secondary=movie_keywords, back_populates="keywords")


class MovieEmbedding(Base):
    __tablename__ = "movie_embeddings"

    id = Column("embedding_id", Integer, primary_key=True, index=True)
    movie_id = Column(Integer, ForeignKey("movies.movie_id", ondelete="CASCADE"), unique=True, nullable=False)
    model_name = Column("embedding_model", String(100), nullable=False)
    embedding = Column(ARRAY(Float).with_variant(JSON, "sqlite"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    movie = relationship("Movie", back_populates="embedding")


class RecommendationCache(Base):
    __tablename__ = "recommendations_cache"
    __table_args__ = (
        UniqueConstraint("source_movie_id", "recommended_movie_id", name="unique_recommendation"),
    )

    id = Column("recommendation_id", Integer, primary_key=True, index=True)
    source_movie_id = Column(Integer, ForeignKey("movies.movie_id", ondelete="CASCADE"), nullable=False, index=True)
    recommended_movie_id = Column(Integer, ForeignKey("movies.movie_id", ondelete="CASCADE"), nullable=False, index=True)
    score = Column(Numeric(5, 2), nullable=False)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    source_movie = relationship("Movie", foreign_keys=[source_movie_id], back_populates="recommendation_sources")
    recommended_movie = relationship("Movie", foreign_keys=[recommended_movie_id], back_populates="recommendation_targets")


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"

    id = Column("message_id", Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    message = Column("user_message", Text, nullable=False)
    response = Column("assistant_response", Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", back_populates="assistant_messages")
    recommended_movies = relationship(
        "AssistantRecommendedMovie",
        back_populates="assistant_message",
        cascade="all, delete-orphan",
    )


class AssistantRecommendedMovie(Base):
    __tablename__ = "assistant_recommended_movies"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("assistant_messages.message_id", ondelete="CASCADE"), nullable=False)
    movie_id = Column(Integer, ForeignKey("movies.movie_id", ondelete="CASCADE"), nullable=False)
    score = Column(Numeric(5, 2), nullable=True)
    reason = Column(Text, nullable=True)

    assistant_message = relationship("AssistantMessage", back_populates="recommended_movies")
    movie = relationship("Movie", back_populates="assistant_recommendations")


class WatchHistory(Base):
    __tablename__ = "watch_history"

    id = Column("history_id", Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    movie_id = Column(Integer, ForeignKey("movies.movie_id", ondelete="CASCADE"), nullable=False, index=True)
    watched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    progress_percent = Column(Integer, nullable=False, default=0)

    user = relationship("User", back_populates="watch_history")
    movie = relationship("Movie", back_populates="watch_history")


class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "movie_id", name="unique_favorite"),)

    id = Column("favorite_id", Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    movie_id = Column(Integer, ForeignKey("movies.movie_id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", back_populates="favorites")
    movie = relationship("Movie", back_populates="favorites")


class UserRating(Base):
    __tablename__ = "user_ratings"
    __table_args__ = (UniqueConstraint("user_id", "movie_id", name="unique_user_movie_rating"),)

    id = Column("rating_id", Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    movie_id = Column(Integer, ForeignKey("movies.movie_id", ondelete="CASCADE"), nullable=False)
    rating = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", back_populates="ratings")
    movie = relationship("Movie", back_populates="ratings")


class UserReaction(Base):
    __tablename__ = "user_reactions"
    __table_args__ = (UniqueConstraint("user_id", "movie_id", name="unique_user_movie_reaction"),)

    id = Column("reaction_id", Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    movie_id = Column(Integer, ForeignKey("movies.movie_id", ondelete="CASCADE"), nullable=False)
    reaction = Column(String(20), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", back_populates="reactions")
    movie = relationship("Movie", back_populates="reactions")


class SearchHistory(Base):
    __tablename__ = "search_history"

    id = Column("search_id", Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    query = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", back_populates="search_history")
