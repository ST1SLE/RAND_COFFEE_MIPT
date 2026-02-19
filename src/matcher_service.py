#!/usr/bin/env python3
"""Сервис периодического мэтчинга по интересам."""

import os
import time
import logging
import argparse
import json
import schedule
from dotenv import load_dotenv
from src.db import init_db_pool, count_searching_users_without_embeddings
from src.matcher import execute_interest_matching

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

MATCHER_CONFIG = {}
MATCHING_INTERVAL_HOURS = int(os.getenv("MATCHING_INTERVAL_HOURS", "6"))


def load_config(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _wait_for_embeddings(uni_id: int, max_retries: int = 2, wait_seconds: int = 90):
    """Ждет, пока worker обработает пользователей без эмбеддингов."""
    for attempt in range(max_retries):
        missing = count_searching_users_without_embeddings(uni_id)
        if missing == 0:
            return
        logger.warning(
            f"{missing} searching user(s) lack embeddings, "
            f"waiting {wait_seconds}s (attempt {attempt + 1}/{max_retries})"
        )
        time.sleep(wait_seconds)

    remaining = count_searching_users_without_embeddings(uni_id)
    if remaining > 0:
        logger.warning(f"Still {remaining} user(s) without embeddings after retries, proceeding")


def run_interest_matching_job():
    uni_id = MATCHER_CONFIG.get("university_id")
    if not uni_id:
        logger.error("university_id is not set in config")
        return

    try:
        _wait_for_embeddings(uni_id)

        logger.info(f"Starting interest matching for university_id={uni_id}")
        matched_count = execute_interest_matching(uni_id)

        if matched_count > 0:
            logger.info(f"Interest matching completed: {matched_count} pairs created")
        else:
            logger.info("No new interest matches this cycle")
    except Exception as e:
        logger.error(f"run_interest_matching_job: {e}", exc_info=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    global MATCHER_CONFIG
    MATCHER_CONFIG = load_config(args.config)

    uni_id = MATCHER_CONFIG.get("university_id")
    logger.info(f"Matcher service starting for university_id={uni_id}, interval={MATCHING_INTERVAL_HOURS}h")

    init_db_pool()

    schedule.every(MATCHING_INTERVAL_HOURS).hours.do(run_interest_matching_job)

    # первый запуск сразу
    run_interest_matching_job()

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
