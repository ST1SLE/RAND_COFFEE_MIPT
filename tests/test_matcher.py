#!/usr/bin/env python3
"""
Тестовый скрипт для проверки работы Matcher Service (мэтчинг пользователей).

Логика:
1. Создает тестовых пользователей с bio.
2. Ждет генерации embeddings (worker).
3. Создает pending coffee_requests для этих пользователей.
4. Запускает мэтчинг вручную.
5. Проверяет, что заявки matched.

Запуск:
    python test_matcher.py --config config/mipt.json
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from src.db import init_db_pool, get_db_connection
from src.matcher import execute_matching


def load_config(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def create_test_users_with_bio(uni_id: int):
    """Создает 4 тестовых пользователя с разными bio для мэтчинга."""
    test_users = [
        {
            "user_id": 9991001,
            "username": "matcher_test_1",
            "first_name": "Анна",
            "bio": "Увлекаюсь квантовой физикой, математикой и нейронными сетями. Люблю решать олимпиадные задачи.",
        },
        {
            "user_id": 9991002,
            "username": "matcher_test_2",
            "first_name": "Борис",
            "bio": "Интересуюсь машинным обучением, deep learning и компьютерным зрением. Занимаюсь исследованиями в AI.",
        },
        {
            "user_id": 9991003,
            "username": "matcher_test_3",
            "first_name": "Виктор",
            "bio": "Люблю спорт, баскетбол и фитнес. Увлекаюсь здоровым образом жизни и кулинарией.",
        },
        {
            "user_id": 9991004,
            "username": "matcher_test_4",
            "first_name": "Галина",
            "bio": "Занимаюсь йогой, медитацией и саморазвитием. Читаю книги по психологии и философии.",
        },
    ]

    sql = """
        INSERT INTO users (user_id, username, first_name, bio, is_active, created_at, last_seen, university_id)
        VALUES (%s, %s, %s, %s, TRUE, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            bio = EXCLUDED.bio,
            embedding = NULL,
            last_seen = EXCLUDED.last_seen;
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                now = datetime.now()
                for user in test_users:
                    cur.execute(
                        sql,
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
                conn.commit()
                print(f"✅ Добавлено {len(test_users)} тестовых пользователей с bio.")
                return test_users
    except Exception as e:
        print(f"❌ Ошибка при создании тестовых пользователей: {e}")
        raise


def wait_for_embeddings(user_ids, uni_id: int, max_wait=90):
    """Ждет пока worker сгенерирует embeddings для пользователей."""
    sql = """
        SELECT COUNT(*) FROM users
        WHERE user_id = ANY(%s)
          AND university_id = %s
          AND embedding IS NOT NULL;
    """

    print(f"\n⏳ Ожидание генерации embeddings для {len(user_ids)} пользователей...")
    print(f"   (Worker запускается каждые 60 секунд, макс. ожидание: {max_wait} сек)")

    start_time = time.time()
    while time.time() - start_time < max_wait:
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (user_ids, uni_id))
                    count = cur.fetchone()[0]

                    if count == len(user_ids):
                        elapsed = int(time.time() - start_time)
                        print(f"✅ Все embeddings сгенерированы за {elapsed} сек!")
                        return True

                    print(f"   Прогресс: {count}/{len(user_ids)} embeddings готовы...", end="\r")
        except Exception as e:
            print(f"❌ Ошибка проверки embeddings: {e}")
            return False

        time.sleep(5)

    print(f"\n❌ Timeout: embeddings не сгенерированы за {max_wait} сек.")
    return False


def create_pending_requests(user_ids, uni_id: int):
    """Создает pending coffee_requests для тестовых пользователей."""
    # Время встречи через 2 часа от текущего момента
    meet_time = datetime.now(timezone.utc) + timedelta(hours=2)

    # Получаем shop_id (предполагаем, что есть хотя бы одна кофейня)
    shop_sql = "SELECT shop_id FROM coffee_shops WHERE university_id = %s LIMIT 1;"

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(shop_sql, (uni_id,))
                result = cur.fetchone()
                if not result:
                    print("❌ Нет активных кофеен в БД. Запустите seeder.")
                    return []
                shop_id = result[0]
    except Exception as e:
        print(f"❌ Ошибка получения shop_id: {e}")
        return []

    # Создаем pending заявки для всех пользователей
    request_sql = """
        INSERT INTO coffee_requests (creator_user_id, shop_id, meet_time, status, university_id, created_at)
        VALUES (%s, %s, %s, 'pending', %s, %s)
        RETURNING request_id;
    """

    request_ids = []
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                now = datetime.now(timezone.utc)
                for user_id in user_ids:
                    cur.execute(request_sql, (user_id, shop_id, meet_time, uni_id, now))
                    request_id = cur.fetchone()[0]
                    request_ids.append(request_id)
                conn.commit()
                print(f"✅ Создано {len(request_ids)} pending заявок (request_id: {request_ids}).")
                return request_ids
    except Exception as e:
        print(f"❌ Ошибка создания pending requests: {e}")
        return []


def check_matching_results(request_ids, uni_id: int):
    """Проверяет результаты мэтчинга."""
    sql = """
        SELECT request_id, creator_user_id, partner_user_id, status
        FROM coffee_requests
        WHERE request_id = ANY(%s) AND university_id = %s
        ORDER BY request_id;
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (request_ids, uni_id))
                results = cur.fetchall()

                print("\n📊 Результаты мэтчинга:")
                print("=" * 80)
                matched_count = 0
                for row in results:
                    req_id, creator, partner, status = row
                    if status == "matched" and partner is not None:
                        print(f"✅ Request {req_id}: User {creator} ↔ User {partner} (Status: {status})")
                        matched_count += 1
                    else:
                        print(f"⚠️  Request {req_id}: User {creator} (Status: {status}, Partner: {partner})")
                print("=" * 80)

                if matched_count >= 2:  # Ожидаем минимум 2 пары (4 пользователя → 2 пары)
                    print(f"✅ Мэтчинг успешен: {matched_count} пар создано!")
                    return True
                else:
                    print(f"❌ Мэтчинг не удался: только {matched_count} пар создано.")
                    return False
    except Exception as e:
        print(f"❌ Ошибка проверки результатов: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test Matcher Service")
    parser.add_argument("--config", required=True, help="Path to config file")
    args = parser.parse_args()

    config = load_config(args.config)
    uni_id = config.get("university_id")

    if not uni_id:
        print("❌ university_id не найден в конфиге.")
        return

    print(f"🚀 Тестирование Matcher Service для university_id={uni_id}\n")
    print("=" * 80)

    # Инициализация БД
    init_db_pool()

    # Шаг 1: Создание тестовых пользователей
    print("Шаг 1: Создание тестовых пользователей с bio...")
    test_users = create_test_users_with_bio(uni_id)
    user_ids = [u["user_id"] for u in test_users]

    # Шаг 2: Ожидание генерации embeddings
    print("\nШаг 2: Ожидание генерации embeddings...")
    if not wait_for_embeddings(user_ids, uni_id, max_wait=90):
        print("\n❌ ТЕСТ ПРОВАЛЕН: Embeddings не сгенерированы.")
        print("Проверьте логи worker: docker-compose logs worker")
        return

    # Шаг 3: Создание pending заявок
    print("\nШаг 3: Создание pending coffee_requests...")
    request_ids = create_pending_requests(user_ids, uni_id)
    if not request_ids:
        print("❌ ТЕСТ ПРОВАЛЕН: Не удалось создать заявки.")
        return

    # Шаг 4: Запуск мэтчинга вручную
    print("\nШаг 4: Запуск мэтчинга вручную...")
    matched_count = execute_matching(uni_id)
    print(f"   Matcher вернул: {matched_count} пар создано.")

    # Шаг 5: Проверка результатов
    print("\nШаг 5: Проверка результатов мэтчинга...")
    success = check_matching_results(request_ids, uni_id)

    # Результат
    print("\n" + "=" * 80)
    if success:
        print("✅ ТЕСТ ПРОЙДЕН: Мэтчинг работает корректно!")
    else:
        print("❌ ТЕСТ НЕ ПРОЙДЕН: Проверьте логи matcher.")
        print("   docker-compose logs matcher")
    print("=" * 80)


if __name__ == "__main__":
    main()
