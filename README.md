# Nocta Film AI Backend

Backend-сервис для онлайн-кинотеатра `Nocta Film` на `FastAPI`, `SQLAlchemy` и `PostgreSQL` с:

- синхронизацией фильмов из `TMDB`;
- AI-рекомендациями на `Sentence Transformers + FAISS`;
- чат-ассистентом для подбора фильмов;
- пользовательскими маршрутами без обязательной авторизации;
- совместимостью с вашей схемой БД.

## Что внутри

- `tmdb_service.py` загружает фильмы, жанры, актёров, режиссёров, ключевые слова и похожие фильмы.
- `excel_service.py` сохраняет распарсенные данные TMDB в `.xlsx` и умеет импортировать их в БД.
- `embedding_service.py` строит эмбеддинги для описаний фильмов через `all-MiniLM-L6-v2`.
- `faiss_service.py` хранит и пересобирает индекс похожести.
- `recommendation_engine.py` комбинирует смысловое сходство, жанры, актёров, режиссёров, рейтинг, популярность и пользовательские сигналы.
- `ai_assistant.py` анализирует текст запроса и формирует ответ естественным языком.
- `preference_model_service.py` обучает персональную нейросеть на просмотренных, лайкнутых и оценённых фильмах пользователя.

## Подготовка БД

У вас уже есть собственная PostgreSQL-схема. ORM в проекте настроен под неё:

- `roles.role_id / role_name`
- `users.user_id / role_id`
- `movies.movie_id`
- `movie_embeddings.embedding FLOAT8[]`
- `recommendations_cache.source_movie_id`
- `assistant_messages.message_id`

Если нужно развернуть БД с нуля, можно использовать ваш SQL-скрипт из `schema.sql`.

## Запуск

1. Создайте виртуальное окружение:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Установите зависимости:

```bash
pip install -r requirements.txt
```

3. Заполните `.env`:

```env
TMDB_API_KEY=YOUR_API_KEY
DB_HOST=localhost
DB_PORT=5432
DB_NAME=nocta_film
DB_USER=postgres
DB_PASSWORD=
SECRET_KEY=change_me_to_a_long_random_secret
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440
```

При необходимости можно оставить и один `DATABASE_URL`, проект поддерживает оба способа конфигурации.

4. Запустите API:

```bash
uvicorn main:app --reload
```

5. Откройте документацию:

- `http://127.0.0.1:8000/docs`

## Основные эндпоинты

- `POST /api/users/register`
- `POST /api/users/login`
- `GET /api/users/me`
- `POST /api/movies/parse/tmdb-to-excel`
- `POST /api/movies/import/excel-to-db`
- `GET /api/movies/all`
- `GET /api/movies/popular`
- `GET /api/movies/new`
- `GET /api/movies/genres/{genre_name}`
- `GET /api/movies/{movie_id}/similar`
- `POST /api/recommendations`
- `POST /api/recommendations/rebuild-index`
- `POST /api/assistant/chat`

## Примеры запросов

Регистрация:

```json
{
  "username": "pavel",
  "email": "pavel@example.com",
  "password": "strong_password",
  "role": "user"
}
```

Получение рекомендаций:

```json
{
  "movie_id": 15,
  "user_id": 7,
  "limit": 10
}
```

Сообщение ассистенту:

```json
{
  "message": "Посоветуй что-нибудь похожее на Интерстеллар",
  "limit": 5
}
```

Сейчас авторизация отключена: токен для вызова API не нужен, а в `/api/assistant/chat` и `/api/recommendations` для персонализации можно передать `user_id`.

Парсинг TMDB в Excel:

В Swagger укажите `source / pages / language / file_name`, нажмите `Execute`, и API сразу отдаст `.xlsx` на скачивание.

Файл формируется в одном основном листе `movies` со столбцами:

- `tmdb_id`
- `title`
- `description`
- `release_year`
- `poster_path`
- `tmdb_popularity`
- `tmdb_vote`
- `genres`
- `tags`
- `countries`
- `languages`
- `cast`
- `crew`

Импорт Excel в БД:

Во второй ручке выберите скачанный `.xlsx` через поле выбора файла и отправьте его как `multipart/form-data`.

При импорте:

- `genres`, `tags`, `cast`, `crew` читаются из строк, разделенных символом `|`;
- из `crew` в БД сохраняются режиссеры;
- из `languages` в БД сохраняется первое значение, потому что в текущей схеме у фильма одно поле `language`.

## Как работает рекомендация

Финальный скор строится из нескольких факторов:

- semantic similarity по эмбеддингам;
- совпадение жанров;
- совпадение актёров;
- совпадение режиссёров;
- совпадение ключевых слов;
- рейтинг;
- популярность;
- пользовательские сигналы: лайки, избранное, оценки и история просмотров.
- если передан `user_id`, дополнительно обучается небольшая `PyTorch`-модель, которая оценивает вероятность, что фильм понравится именно этому пользователю.
- уже просмотренные фильмы пользователя автоматически исключаются из персональной выдачи.

## Важно

- Для реальных рекомендаций сначала распарсите TMDB в Excel, затем импортируйте файл в БД.
- При первом построении эмбеддингов модель `all-MiniLM-L6-v2` будет скачана автоматически.
- Для персональной нейросети нужен `torch` из `requirements.txt` и достаточное количество пользовательских сигналов в БД.
- Если запрос в `/api/assistant/chat` приходит без `user_id`, ответ вернётся, но история диалога не будет сохранена в БД.
