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

MODEL = None
WORKER_CONFIG = {}
UNIVERSITY_IDS = []


def load_model():
    global MODEL
    logger.info("Loading sentence-transformers model...")
    MODEL = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
    logger.info("Model loaded (dim=384)")


def vectorize_users():
    if not MODEL:
        logger.error("Model is not loaded, skipping")
        return

    batch_size = WORKER_CONFIG.get("batch_size", 30)

    for uni_id in UNIVERSITY_IDS:
        try:
            users = get_users_without_embeddings(uni_id=uni_id, limit=batch_size)
            if not users:
                continue

            logger.info(f"[uni={uni_id}] {len(users)} users without embeddings, vectorizing")

            user_ids = [u[0] for u in users]

            # факультет + курс + bio -> единый текст для эмбеддинга
            enriched_texts = []
            for user_id, bio, school, year in users:
                parts = []
                if school and school != "Никакой из них":
                    parts.append(f"Факультет: {school}")
                if year:
                    parts.append(f"Курс: {year}")
                parts.append(f"О себе: {bio}")
                enriched_texts.append(". ".join(parts))

            embeddings = MODEL.encode(enriched_texts, convert_to_numpy=True, show_progress_bar=False)

            success_count = 0
            for user_id, embedding in zip(user_ids, embeddings):
                if update_user_embedding(user_id, embedding.tolist(), uni_id):
                    success_count += 1
                else:
                    logger.warning(f"Failed to update embedding for user {user_id}")

            logger.info(f"[uni={uni_id}] Vectorized {success_count}/{len(users)} users")

        except Exception as e:
            logger.error(f"vectorize_users uni={uni_id}: {e}")


def load_config(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", nargs="+", required=True)
    args = parser.parse_args()

    global WORKER_CONFIG, UNIVERSITY_IDS

    for config_path in args.config:
        cfg = load_config(config_path)
        uni_id = cfg.get("university_id")
        if uni_id and uni_id not in UNIVERSITY_IDS:
            UNIVERSITY_IDS.append(uni_id)
        # batch_size из первого конфига
        if not WORKER_CONFIG:
            WORKER_CONFIG = cfg

    logger.info(f"Worker starting for university_ids={UNIVERSITY_IDS}")

    init_db_pool()
    load_model()

    schedule.every(60).seconds.do(vectorize_users)

    # первая проверка сразу, не ждем 60с
    vectorize_users()

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
