import argparse
import json
import logging

from src.db import add_coffee_shop, init_db_pool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def expand_schedule(short_schedule):
    """
    Converts a compact schedule into a full, readable 7-day format.
    """
    full_schedule = {}
    days_of_week = [
        "Понедельник",
        "Вторник",
        "Среда",
        "Четверг",
        "Пятница",
        "Суббота",
        "Воскресенье",
    ]

    for key, hours in short_schedule.items():
        if key == "Ежедневно":
            for day in days_of_week:
                full_schedule[day] = hours
        elif key == "Пн-Пт":
            for i in range(5):
                full_schedule[days_of_week[i]] = hours
        elif key == "Сб-Вс":
            full_schedule[days_of_week[5]] = hours
            full_schedule[days_of_week[6]] = hours
        elif key == "Сб":
            full_schedule[days_of_week[5]] = hours

    return full_schedule


def populate_coffee_shops_table(shops_config_path: str):
    with open(shops_config_path, encoding="utf-8") as f:
        config = json.load(f)

    university_id = config["university_id"]
    shops = config["shops"]

    init_db_pool()
    logger.info("Adding/updating coffee shops for university_id=%s ...", university_id)

    for shop_data in shops:
        full_working_hours = expand_schedule(shop_data["schedule"])
        working_hours_json = json.dumps(full_working_hours, ensure_ascii=False)
        add_coffee_shop(
            shop_id=shop_data["id"],
            name=shop_data["name"],
            description=shop_data["desc"],
            working_hours=working_hours_json,
            uni_id=university_id,
        )

    logger.info("Done! Added/updated %d shops.", len(shops))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed coffee shops from a JSON config.")
    parser.add_argument(
        "--config",
        required=True,
        help="Path to coffee shops JSON config (e.g. data/coffee_shops_mipt.json)",
    )
    args = parser.parse_args()
    populate_coffee_shops_table(args.config)
