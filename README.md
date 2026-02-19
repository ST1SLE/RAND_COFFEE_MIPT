# Random Coffee

Telegram-бот для случайных кофе-встреч между студентами. Подбирает пары на основе
cosine similarity эмбеддингов из bio пользователей. Поддерживает несколько вузов
на одной инфраструктуре.

## Стек

- Python 3.12, python-telegram-bot
- PostgreSQL + pgvector 0.5.1
- sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2, 384 dim)
- Docker Compose

## Архитектура

Три типа сервисов (bot, worker, matcher) — по инстансу на вуз, кроме worker (один на все).
Общая PostgreSQL с изоляцией по `university_id`.

- **bot** — Telegram-хэндлеры, регистрация, уведомления, подтверждения встреч
- **worker** — генерация эмбеддингов из bio (каждые 60с)
- **matcher** — подбор пар жадным алгоритмом по cosine similarity (каждые 6ч)

## Запуск

```bash
cp .env.example .env  # заполнить токены и DB credentials
docker compose up --build -d
```

## Конфигурация

Каждый вуз описан в `config/<slug>.json` (university_id, список факультетов, токен бота).
Для добавления нового вуза: создать конфиг, добавить сервисы в `docker-compose.yml`,
вставить запись в таблицу `universities`.

## Тесты

Интеграционные тесты запускаются внутри контейнера (нужна БД):

```bash
docker compose exec -T bot_mipt python tests/test_worker.py --config config/mipt.json
docker compose exec -T bot_mipt python tests/test_matcher.py --config config/mipt.json
docker compose exec -T bot_mipt python tests/test_isolation.py --config config/mipt.json
docker compose exec -T bot_mipt python tests/test_interest_matching.py --config config/mipt.json
```

## Лицензия

MIT
