import os
import subprocess
import json
from dotenv import load_dotenv
from src.db import add_coffee_shop

# example coffee_shop data
# {
#     "shop_id": 1,
#     "name": "somename",
#     "description": "some_description",
#     "working_hours": {"Пн-Пт": "08:00-22:00", "Cб-Вс": "10:00-21:00"},
# }

# working_hours could be like this:
# {"Ежедневно": "08:00-23:00"}


def expand_schedule(short_schedule):
    """
    Converts a compact schedule into a full, readable 7-day format.
    Example: {"Пн-Пт": "09-18", "Сб": "10-17"} -> {"Понедельник": "09-18", ...}
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
        else:
            pass

    return full_schedule


COFFEE_SHOPS_SOURCE_DATA = [
    {
        "id": 1,
        "name": "Болтай",
        "desc": "Институтский переулок, дом 8.",
        "schedule": {"Ежедневно": "08:30-23:59"},
    },
    {
        "id": 2,
        "name": "Кампус",
        "desc": "Первомайская улица, дом 9/4.",
        "schedule": {"Ежедневно": "08:00-23:00"},
    },
    {
        "id": 3,
        "name": "Теория",
        "desc": "Первомайская улица, дом 9/4.",
        "schedule": {"Ежедневно": "10:00-23:00"},
    },
    {
        "id": 4,
        "name": "Даблби Экспресс",
        "desc": "ГК 2 этаж.",
        "schedule": {"Пн-Пт": "08:00-18:00"},
    },
    {
        "id": 5,
        "name": "Теория Био",
        "desc": "Физтех.Био 1 этаж.",
        "schedule": {"Пн-Пт": "08:00-18:00"},
    },
    {
        "id": 6,
        "name": "Теория Цифра",
        "desc": "Переход между ГК и Физтех.Цифрой.",
        "schedule": {"Пн-Пт": "09:00-17:00"},
    },
    {
        "id": 7,
        "name": "Novana",
        "desc": "Там же, где Ардис: Институтский переулок, дом 6.",
        "schedule": {"Пн-Пт": "08:00-21:00", "Сб-Вс": "08:00-18:00"},
    },
    {
        "id": 9,
        "name": "Адрон",
        "desc": "Дирижабль 1 этаж",
        "schedule": {"Ежедневно": "10:00-22:00"},
    },
    {
        "id": 10,
        "name": "Вкусвилл Кафе",
        "desc": "Вкусвилл в Дирижабле, 1 этаж",
        "schedule": {"Ежедневно": "08:00-22:00"},
    },
    {
        "id": 11,
        "name": "IQ кафе",
        "desc": "Первомайская улицу, дом 18",
        "schedule": {"Ежедневно": "07:00-23:59"},
    },
    {
        "id": 12,
        "name": "Кофейня 6ки",
        "desc": "Первомайская улица, дом 21к6. Нужен пропуск!",
        "schedule": {"Пн-Пт": "19:30-22:30"},
    },
    {
        "id": 13,
        "name": "Циви Сациви",
        "desc": "Дирижабль 2 этаж",
        "schedule": {"Ежедневно": "11:00-23:00"},
    },
    {
        "id": 14,
        "name": "Созвездие",
        "desc": "Дирижабль 1 этаж",
        "schedule": {"Пн-Пт": "07:30-19:00", "Сб": "08:00-19:00"},
    },
    {
        "id": 15,
        "name": "X2 Кофе",
        "desc": "Первомайская улица, дом 17",
        "schedule": {"Пн-Пт": "09:00-20:00", "Сб-Вс": "10:00-20:00"},
    },
    {
        "id": 16,
        "name": "Шоколадница",
        "desc": "Первомайская улица, дом 50",
        "schedule": {"Пн-Пт": "08:00-23:00", "Сб-Вс": "09:00-23:00"},
    },
]


def populate_coffee_shops_table():
    print("Starting to add/update coffee shops...")
    for shop_data in COFFEE_SHOPS_SOURCE_DATA:

        full_working_hours = expand_schedule(shop_data["schedule"])
        working_hours_json = json.dumps(full_working_hours, ensure_ascii=False)

        add_coffee_shop(
            shop_id=shop_data["id"],
            name=shop_data["name"],
            description=shop_data["desc"],
            working_hours=working_hours_json,
        )
    print("\nPopulation complete!")


if __name__ == "__main__":
    populate_coffee_shops_table()
