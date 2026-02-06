#!/usr/bin/env python3
"""
Сервис для автоматического мэтчинга пользователей на основе косинусного сходства.

Запускается как отдельный контейнер в docker-compose.
Периодически (каждые 5 минут) ищет pending заявки и создает пары.
"""
import os
import time
import logging
import argparse
import json
import schedule
from dotenv import load_dotenv
from src.db import init_db_pool
from src.matcher import execute_matching

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

MATCHER_CONFIG = {}


def load_config(path: str):
    """Загружает конфигурацию из JSON файла."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_matching_job():
    """
    Функция для периодического запуска мэтчинга.
    Получает конфиг, запускает execute_matching для заданного university_id.
    """
    uni_id = MATCHER_CONFIG.get("university_id")
    if not uni_id:
        logger.error("university_id is not set in config. Aborting.")
        return

    try:
        logger.info(f"🔄 Starting matching job for university_id={uni_id}")
        matched_count = execute_matching(uni_id)

        if matched_count > 0:
            logger.info(f"✅ Matching job completed: {matched_count} pairs created.")
        else:
            logger.info("No new matches created this cycle.")
    except Exception as e:
        logger.error(f"ERROR in run_matching_job: {e}", exc_info=True)


def main():
    parser = argparse.ArgumentParser(description="Matcher Service for Random Coffee Bot")
    parser.add_argument("--config", help="Path to configuration file", required=True)
    args = parser.parse_args()

    global MATCHER_CONFIG
    MATCHER_CONFIG = load_config(args.config)

    logger.info(f"🚀 Matcher Service starting for university_id={MATCHER_CONFIG.get('university_id')}")
    logger.info(f"Running matching every 5 minutes...")

    # Инициализация БД
    init_db_pool()

    # Настройка расписания: запуск каждые 5 минут
    schedule.every(5).minutes.do(run_matching_job)

    logger.info("🎯 Matcher service is running. Checking for matches every 5 minutes...")

    # Запускаем первую проверку сразу (не ждем 5 минут)
    run_matching_job()

    # Бесконечный цикл
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
