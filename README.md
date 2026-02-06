# Random Coffee MIPT

> Telegram-бот для организации случайных кофе-встреч между студентами с ML-based автоматическим мэтчингом

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](https://www.docker.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-336791.svg)](https://www.postgresql.org/)
[![pgvector](https://img.shields.io/badge/pgvector-0.5.1-green.svg)](https://github.com/pgvector/pgvector)

---

## 📖 О Проекте

**Random Coffee MIPT** — это Telegram-бот, который помогает студентам МФТИ находить единомышленников для неформального общения за чашкой кофе.

### Ключевые возможности:

- 🤖 **ML-based мэтчинг** — автоматический подбор пар на основе интересов
- 🎯 **Умный алгоритм** — косинусное сходство эмбеддингов (sentence-transformers)
- 🏗️ **Микросервисная архитектура** — независимое масштабирование компонентов
- 🔐 **Multi-tenancy** — поддержка нескольких университетов
- 📊 **Production-ready** — connection pooling, graceful shutdown, structured logging

---

## 🏛️ Архитектура

```
┌─────────────┐
│   Telegram  │
│    User     │
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────┐
│          BOT SERVICE (bot.py)           │
│  • Telegram handlers                    │
│  • Notifications                        │
│  • Confirmations                        │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│     PostgreSQL + pgvector (db)          │
│  Tables:                                │
│  • users (bio, embedding vector(384))   │
│  • coffee_requests                      │
│  • coffee_shops                         │
└──────┬──────────────────────┬───────────┘
       │                      │
       ▼                      ▼
┌──────────────────┐   ┌──────────────────┐
│ WORKER SERVICE   │   │ MATCHER SERVICE  │
│ (worker.py)      │   │ (matcher.py)     │
│                  │   │                  │
│ • Load ML model  │   │ • Get pending    │
│ • Generate       │   │ • Calc similarity│
│   embeddings     │   │ • Create pairs   │
│ • Update DB      │   │ • Update DB      │
│                  │   │                  │
│ Every 60s        │   │ Every 5 min      │
└──────────────────┘   └──────────────────┘
```

---

## 🛠️ Технический Стек

### Backend
- **Python 3.12** — основной язык
- **python-telegram-bot 22.2** — Telegram API
- **psycopg2** — PostgreSQL драйвер с connection pooling

### Machine Learning
- **sentence-transformers** — генерация эмбеддингов
- **paraphrase-multilingual-MiniLM-L12-v2** — мультиязычная модель (384 dim)
- **numpy** — матричные операции

### Database
- **PostgreSQL 15** — реляционная БД
- **pgvector 0.5.1** — расширение для векторного поиска

### DevOps
- **Docker + Docker Compose** — контейнеризация и оркестрация
- **python-dotenv** — управление переменными окружения

---

## 🚀 Быстрый Старт

### Требования

- Docker
- Docker Compose
- Telegram Bot Token

### Установка

1. **Клонировать репозиторий:**
```bash
git clone https://github.com/yourusername/randomcoffeeMIPT.git
cd randomcoffeeMIPT
```

2. **Создать `.env` файл:**
```bash
cp .env.example .env
nano .env
```

```env
# Telegram Bot
BOT_TOKEN=your_telegram_bot_token_here

# Database
DB_HOST=db
DB_NAME=coffee_bot_db
DB_USER=coffee_bot_user
DB_PASS=your_secure_password_here
```

3. **Запустить сервисы:**
```bash
docker-compose up --build -d
```

4. **Проверить статус:**
```bash
docker-compose ps
```

Вы должны увидеть 5 сервисов:
- ✅ `db` — PostgreSQL
- ✅ `bot` — Telegram бот
- ✅ `worker` — ML worker
- ✅ `matcher` — Matching service
- ✅ `seeder` — Database seeder (завершится после инициализации)

5. **Проверить логи:**
```bash
docker-compose logs -f bot
docker-compose logs -f worker
docker-compose logs -f matcher
```

---

## 🧪 Тестирование

### Тест ML Worker (генерация embeddings)
```bash
docker cp test_worker.py randomcoffeemipt_bot_1:/app/
docker-compose exec bot python test_worker.py --config config/mipt.json
```

**Ожидаемый результат:**
```
✅ Тест пройден: embeddings сгенерированы (384 dim)
```

### Тест Matcher (алгоритм мэтчинга)
```bash
docker cp test_matcher.py randomcoffeemipt_bot_1:/app/
docker-compose exec bot python test_matcher.py --config config/mipt.json
```

**Ожидаемый результат:**
```
✅ Тест пройден: 2 пары созданы из 4 пользователей
```

### Тест Notifications (система уведомлений)
```bash
docker cp test_notifications.py randomcoffeemipt_bot_1:/app/
docker-compose exec bot python test_notifications.py --config config/mipt.json
```

**Ожидаемый результат:**
```
✅ Тест пройден: Уведомления работают корректно
```

---

## 📚 Документация

### Основные документы:

- 📖 [**TECHNICAL_OVERVIEW.md**](TECHNICAL_OVERVIEW.md) — Полное техническое описание проекта
  - История рефакторинга (v1.0 → v2.0)
  - Детальный разбор каждого этапа
  - Архитектурные решения
  - Production-ready практики
  - Код с подробными комментариями

- 🎤 [**INTERVIEW_CHEATSHEET.md**](INTERVIEW_CHEATSHEET.md) — Шпаргалка для собеседований
  - Elevator pitch
  - Ключевые цифры
  - Готовые ответы на типичные вопросы
  - Wow-факторы для интервью

- 📋 [**refactoring_plan.md**](refactoring_plan.md) — План рефакторинга по этапам

### Дополнительно:

- [schema.sql](schema.sql) — Схема базы данных
- [migrations/](migrations/) — SQL миграции
- [src/](src/) — Исходный код

---

## 🔧 Конфигурация

### Добавление нового университета:

1. Создайте конфиг:
```bash
cp config/template.json config/university_name.json
```

2. Отредактируйте:
```json
{
  "university_id": 2,
  "university_name": "Your University",
  "telegram_chat_id": -1001234567890
}
```

3. Запустите бота с новым конфигом:
```bash
docker-compose exec bot python src/bot.py --config config/university_name.json
```

---

## 📊 Мониторинг

### Логи сервисов:

```bash
# Все логи
docker-compose logs -f

# Конкретный сервис
docker-compose logs -f bot
docker-compose logs -f worker
docker-compose logs -f matcher
```

### Проверка работы джобов:

```bash
# ML Worker (каждые 60 секунд)
docker-compose logs worker | grep "Vectorized"

# Matcher (каждые 5 минут)
docker-compose logs matcher | grep "pairs"

# Notifications (каждые 120 секунд)
docker-compose logs bot | grep "notify_new_matches"
```

---

## 🐛 Troubleshooting

### Проблема: Контейнеры не запускаются

**Решение:**
```bash
docker-compose down
docker-compose up --build -d
```

### Проблема: Worker не генерирует embeddings

**Причины:**
1. Модель еще загружается (ждите 5-10 минут)
2. Нет пользователей с bio

**Проверка:**
```bash
docker-compose logs worker | tail -20
```

### Проблема: Matcher не создает пары

**Причины:**
1. Нет pending заявок
2. Нет embeddings у пользователей
3. Все пары уже встречались

**Проверка:**
```bash
docker-compose exec db psql -U coffee_bot_user -d coffee_bot_db \
  -c "SELECT COUNT(*) FROM coffee_requests WHERE status='pending' AND partner_user_id IS NULL;"
```

### Проблема: ContainerConfig error

**Решение:**
```bash
docker-compose down
docker-compose up --build -d
```

---

## 🚢 Production Deployment

### Kubernetes (рекомендуется)

1. Создайте Helm chart
2. Настройте Ingress (HTTPS)
3. Добавьте HorizontalPodAutoscaler для worker'ов
4. Настройте мониторинг (Prometheus + Grafana)

### Простой деплой (Docker Compose)

1. Настройте reverse proxy (nginx)
2. Добавьте SSL сертификаты (Let's Encrypt)
3. Настройте backup для PostgreSQL
4. Добавьте логирование (Loki)

---

## 🤝 Contributing

Pull requests приветствуются! Для больших изменений сначала откройте issue.

### Как внести вклад:

1. Fork проекта
2. Создайте feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit изменений (`git commit -m 'Add AmazingFeature'`)
4. Push в branch (`git push origin feature/AmazingFeature`)
5. Откройте Pull Request

---

## 📝 Roadmap

- [ ] **Этап 6**: Финализация рефакторинга
  - [ ] Удаление старого кода ручного выбора
  - [ ] Тестирование multi-tenancy
  - [ ] Финальные интеграционные тесты

- [ ] **Kubernetes**: Деплой с Helm charts
- [ ] **Monitoring**: Prometheus + Grafana
- [ ] **A/B Testing**: Сравнение алгоритмов мэтчинга
- [ ] **LLM Integration**: GPT-4 для icebreakers
- [ ] **Mobile App**: React Native приложение

---

## 📄 License

MIT License — see [LICENSE](LICENSE) file for details

---

## 👤 Автор

**[Ваше имя]**

- GitHub: [@yourusername](https://github.com/yourusername)
- Telegram: [@yourtelegram](https://t.me/yourtelegram)
- Email: your.email@example.com

---

## 🙏 Acknowledgments

- [pgvector](https://github.com/pgvector/pgvector) — Vector similarity search for Postgres
- [sentence-transformers](https://www.sbert.net/) — State-of-the-art sentence embeddings
- [python-telegram-bot](https://python-telegram-bot.org/) — Telegram Bot API wrapper

---

## 📸 Screenshots

### Регистрация пользователя
```
👋 Привет! Я помогу тебе найти интересного собеседника для кофе-встречи.

📝 Расскажи немного о себе:
Твои интересы, хобби, чем занимаешься...
```

### Уведомление о матче
```
🎉 Отличные новости!

Мы нашли для тебя пару на основе общих интересов!

👤 Твой партнер: Алексей
📅 Дата: 15.02 в 14:00

Система автоматически подобрала вас, основываясь на ваших интересах.
```

---

**⭐ Если проект понравился, поставьте звезду на GitHub!**
