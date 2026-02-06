import os
import time
import logging
import argparse
import json
import schedule
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
from db import init_db_pool, get_users_without_embeddings, update_user_embedding

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Глобальная переменная для модели (загружается один раз)
MODEL = None
WORKER_CONFIG = {}


def load_model():
    """
    Загружает модель sentence-transformers один раз при старте воркера.
    Модель: paraphrase-multilingual-MiniLM-L12-v2
    Размерность эмбеддингов: 384
    """
    global MODEL
    logger.info("Loading sentence-transformers model...")
    try:
        MODEL = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
        logger.info("✅ Model loaded successfully. Embedding dimension: 384")
    except Exception as e:
        logger.error(f"❌ Failed to load model: {e}")
        raise


def vectorize_users():
    """
    Основная функция воркера:
    1. Получает пользователей с bio, но без embedding
    2. Генерирует векторы батчами
    3. Сохраняет обратно в БД

    SaaS-compliance: фильтрация по university_id из конфига.
    """
    if not MODEL:
        logger.error("Model is not loaded. Skipping vectorization.")
        return

    uni_id = WORKER_CONFIG.get("university_id")
    if not uni_id:
        logger.error("university_id is not set in config. Aborting.")
        return

    batch_size = WORKER_CONFIG.get("batch_size", 30)

    try:
        # Получаем пользователей без эмбеддингов (с фильтрацией по university_id)
        users = get_users_without_embeddings(uni_id=uni_id, limit=batch_size)

        if not users:
            logger.info(f"No users without embeddings for university_id={uni_id}. Sleeping...")
            return

        logger.info(f"Found {len(users)} users without embeddings. Vectorizing...")

        # Разбиваем на батчи для обработки
        # (хотя get_users_without_embeddings уже возвращает ограниченное количество)
        user_ids = [u[0] for u in users]
        bios = [u[1] for u in users]

        # Генерация эмбеддингов батчем (быстрее, чем по одному)
        embeddings = MODEL.encode(bios, convert_to_numpy=True, show_progress_bar=False)

        # Сохраняем в БД
        success_count = 0
        for user_id, embedding in zip(user_ids, embeddings):
            # embedding.tolist() преобразует numpy array в список Python
            if update_user_embedding(user_id, embedding.tolist(), uni_id):
                success_count += 1
            else:
                logger.warning(f"Failed to update embedding for user {user_id}")

        logger.info(f"✅ Successfully vectorized {success_count}/{len(users)} users.")

    except Exception as e:
        logger.error(f"ERROR in vectorize_users: {e}")


def load_config(path: str):
    """Загружает конфигурацию из JSON файла."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="ML Worker for Random Coffee Bot")
    parser.add_argument("--config", help="Path to configuration file", required=True)
    args = parser.parse_args()

    global WORKER_CONFIG
    WORKER_CONFIG = load_config(args.config)

    logger.info(f"Worker starting for university_id={WORKER_CONFIG.get('university_id')}")
    logger.info(f"Batch size: {WORKER_CONFIG.get('batch_size', 30)}")

    # Инициализация БД и модели
    init_db_pool()
    load_model()

    # Настройка расписания: запуск каждые 60 секунд
    schedule.every(60).seconds.do(vectorize_users)

    logger.info("🚀 Worker is running. Checking for users every 60 seconds...")

    # Запускаем первую проверку сразу (не ждем 60 сек)
    vectorize_users()

    # Бесконечный цикл
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
