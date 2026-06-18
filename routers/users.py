from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from auth import authenticate_user, create_access_token, get_password_hash
from config import settings
from database import get_db
from models import Role, User
from schemas import TokenResponse, UserCreate, UserLogin, UserRead


router = APIRouter(prefix="/users", tags=["Пользователи"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать пользователя",
    description="Регистрирует нового пользователя в системе.",
)
def register_user(payload: UserCreate, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.username == payload.username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists.",
        )

    existing_email = db.query(User).filter(User.email == payload.email).first()
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already exists.",
        )

    role = db.query(Role).filter(Role.name == payload.role).first()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role does not exist.",
        )

    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=get_password_hash(payload.password),
        role_id=role.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Войти в систему",
    description="Проверяет логин и пароль пользователя и возвращает токен доступа.",
)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    user = authenticate_user(db, payload.username_or_email, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username/email or password.",
        )

    access_token = create_access_token(
        subject=str(user.id),
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )
    return TokenResponse(access_token=access_token, user=user)


@router.get(
    "/me",
    response_model=UserRead,
    summary="Получить пользователя",
    description="Возвращает пользователя по user_id или первого пользователя в базе, если user_id не передан.",
)
def read_current_user(
    db: Session = Depends(get_db),
    user_id: int | None = Query(default=None),
):
    query = db.query(User)
    if user_id is not None:
        user = query.filter(User.id == user_id).first()
    else:
        user = query.order_by(User.id.asc()).first()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )
    return user
