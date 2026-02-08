#!/usr/bin/env python3
"""
Сервис мэтчинга по интересам.

Запускается как отдельный контейнер в docker-compose.
Раз в день подбирает пары среди пользователей в режиме поиска
на основе косинусного сходства эмбеддингов.
"""
import time
import logging
import argparse
import json
import schedule
from dotenv import load_dotenv
from src.db import init_db_pool
from src.matcher import execute_interest_matching

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

MATCHER_CONFIG = {}

# Время ежедневного запуска мэтчинга (МСК)
MATCHING_TIME = "12:00"


def load_config(path: str):
    """Загружает конфигурацию из JSON файла."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_interest_matching_job():
    """
    Ежедневный мэтчинг по интересам.
    Подбирает пары среди пользователей с is_searching_interest_match=TRUE.
    """
    uni_id = MATCHER_CONFIG.get("university_id")
    if not uni_id:
        logger.error("university_id is not set in config. Aborting.")
        return

    try:
        logger.info(f"🔍 Starting interest matching job for university_id={uni_id}")
        matched_count = execute_interest_matching(uni_id)

        if matched_count > 0:
            logger.info(f"✅ Interest matching completed: {matched_count} pairs created.")
        else:
            logger.info("No new interest matches created this cycle.")
    except Exception as e:
        logger.error(f"ERROR in run_interest_matching_job: {e}", exc_info=True)


def main():
    parser = argparse.ArgumentParser(description="Interest Matching Service for Random Coffee Bot")
    parser.add_argument("--config", help="Path to configuration file", required=True)
    args = parser.parse_args()

    global MATCHER_CONFIG
    MATCHER_CONFIG = load_config(args.config)

    logger.info(f"🚀 Interest Matching Service starting for university_id={MATCHER_CONFIG.get('university_id')}")

    init_db_pool()

    # Ежедневный запуск в MATCHING_TIME
    schedule.every().day.at(MATCHING_TIME).do(run_interest_matching_job)

    logger.info(f"🎯 Interest matching service is running. Matching daily at {MATCHING_TIME}.")

    # Первый запуск сразу при старте
    run_interest_matching_job()

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
