#!/usr/bin/env python3
"""
Тестовый скрипт для проверки системы уведомлений о ML-мэтчинге.

Логика:
1. Проверяет наличие флага is_match_notification_sent в БД
2. Создает тестовую ситуацию с matched заявкой без уведомления
3. Проверяет, что get_new_matches_for_notification корректно её получает
4. Проверяет, что флаг обновляется после получения

Запуск:
    python test_notifications.py --config config/mipt.json
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from src.db import init_db_pool, get_db_connection


def load_config(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def check_migration_applied(uni_id: int):
    """Проверяет, что миграция 002 применена (есть колонка is_match_notification_sent)."""
    sql = """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'coffee_requests'
          AND column_name = 'is_match_notification_sent';
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                result = cur.fetchone()

                if result:
                    print("✅ Миграция 002 применена: колонка is_match_notification_sent существует")
                    return True
                else:
                    print("❌ Миграция 002 НЕ применена: колонка is_match_notification_sent не найдена")
                    return False
    except Exception as e:
        print(f"❌ Ошибка проверки миграции: {e}")
        return False


def create_test_matched_request(uni_id: int):
    """Создает тестовую matched заявку с is_match_notification_sent = FALSE."""
    # Создаем двух тестовых пользователей
    user_sql = """
        INSERT INTO users (user_id, username, first_name, bio, is_active, created_at, last_seen, university_id)
        VALUES (%s, %s, %s, %s, TRUE, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            bio = EXCLUDED.bio,
            last_seen = EXCLUDED.last_seen;
    """

    test_users = [
        {
            "user_id": 9992001,
            "username": "notif_test_1",
            "first_name": "Алексей",
            "bio": "Тестовый пользователь для проверки уведомлений",
        },
        {
            "user_id": 9992002,
            "username": "notif_test_2",
            "first_name": "Мария",
            "bio": "Тестовый пользователь для проверки уведомлений",
        },
    ]

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                now = datetime.now(timezone.utc)

                # Создаем пользователей
                for user in test_users:
                    cur.execute(
                        user_sql,
                        (
                            user["user_id"],
                            user["username"],
                            user["first_name"],
                            user["bio"],
                            now,
                            now,
                            uni_id,
                        ),
                    )

                # Получаем shop_id
                cur.execute("SELECT shop_id FROM coffee_shops WHERE university_id = %s LIMIT 1;", (uni_id,))
                result = cur.fetchone()
                if not result:
                    print("❌ Нет кофеен в БД. Запустите seeder.")
                    return None
                shop_id = result[0]

                # Создаем matched заявку
                meet_time = datetime.now(timezone.utc) + timedelta(hours=2)
                request_sql = """
                    INSERT INTO coffee_requests
                    (creator_user_id, partner_user_id, shop_id, meet_time, status,
                     university_id, created_at, is_match_notification_sent)
                    VALUES (%s, %s, %s, %s, 'matched', %s, %s, FALSE)
                    RETURNING request_id;
                """

                cur.execute(
                    request_sql,
                    (test_users[0]["user_id"], test_users[1]["user_id"], shop_id, meet_time, uni_id, now),
                )
                request_id = cur.fetchone()[0]

                conn.commit()
                print(f"✅ Создана тестовая matched заявка: request_id={request_id}")
                print(f"   Creator: {test_users[0]['first_name']} (ID: {test_users[0]['user_id']})")
                print(f"   Partner: {test_users[1]['first_name']} (ID: {test_users[1]['user_id']})")
                return request_id
    except Exception as e:
        print(f"❌ Ошибка создания тестовой заявки: {e}")
        return None


def test_get_new_matches(uni_id: int):
    """Тестирует функцию get_new_matches_for_notification."""
    from src.db import get_new_matches_for_notification

    print("\n🔍 Проверяем get_new_matches_for_notification...")

    matches = get_new_matches_for_notification(uni_id)

    if not matches:
        print("❌ Функция не вернула матчи (возможно, все уже были отправлены)")
        return False

    print(f"✅ Функция вернула {len(matches)} матч(ей):")
    for match in matches:
        print(f"   Request ID: {match['request_id']}, Creator: {match['creator_user_id']}, Partner: {match['partner_user_id']}")

    return True


def verify_flag_updated(request_id: int, uni_id: int):
    """Проверяет, что флаг is_match_notification_sent обновился."""
    sql = """
        SELECT is_match_notification_sent
        FROM coffee_requests
        WHERE request_id = %s AND university_id = %s;
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (request_id, uni_id))
                result = cur.fetchone()

                if result and result[0]:
                    print(f"✅ Флаг is_match_notification_sent обновлен для request_id={request_id}")
                    return True
                else:
                    print(f"❌ Флаг is_match_notification_sent НЕ обновлен для request_id={request_id}")
                    return False
    except Exception as e:
        print(f"❌ Ошибка проверки флага: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test Notification System")
    parser.add_argument("--config", required=True, help="Path to config file")
    args = parser.parse_args()

    config = load_config(args.config)
    uni_id = config.get("university_id")

    if not uni_id:
        print("❌ university_id не найден в конфиге.")
        return

    print(f"🚀 Тестирование системы уведомлений для university_id={uni_id}\n")
    print("=" * 80)

    # Инициализация БД
    init_db_pool()

    # Шаг 1: Проверка миграции
    print("\nШаг 1: Проверка применения миграции 002...")
    if not check_migration_applied(uni_id):
        print("\n❌ ТЕСТ ПРОВАЛЕН: Миграция не применена.")
        print("Примените миграцию: docker-compose exec db psql -U <user> -d <db> -f /docker-entrypoint-initdb.d/002_add_match_notification_flag.sql")
        return

    # Шаг 2: Создание тестовой matched заявки
    print("\nШаг 2: Создание тестовой matched заявки...")
    request_id = create_test_matched_request(uni_id)
    if not request_id:
        print("\n❌ ТЕСТ ПРОВАЛЕН: Не удалось создать тестовую заявку.")
        return

    # Шаг 3: Проверка get_new_matches_for_notification
    print("\nШаг 3: Проверка get_new_matches_for_notification...")
    if not test_get_new_matches(uni_id):
        print("\n⚠️  ВНИМАНИЕ: Функция не вернула матчи (может быть нормально, если все уже отправлены).")

    # Шаг 4: Проверка обновления флага
    print("\nШаг 4: Проверка обновления флага...")
    if verify_flag_updated(request_id, uni_id):
        print("\n✅ ТЕСТ ПРОЙДЕН: Система уведомлений работает корректно!")
        print("\n📝 Следующий шаг: Запустите бот и проверьте логи:")
        print("   docker-compose logs -f bot | grep 'notify_new_matches'")
    else:
        print("\n❌ ТЕСТ НЕ ПРОЙДЕН: Флаг не обновился после вызова get_new_matches_for_notification.")

    print("=" * 80)


if __name__ == "__main__":
    main()
