#!/usr/bin/env python3
"""
Тест изоляции данных между университетами (multi-tenancy).

Проверяет, что данные одного university_id полностью невидимы для другого.
Это критически важно для SaaS-compliance.

Логика:
1. Создает тестовых пользователей для двух разных university_id.
2. Создает pending coffee_requests для каждого university.
3. Проверяет, что get_pending_requests() видит только свои заявки.
4. Проверяет, что get_request_details() с чужим uni_id возвращает пустой результат.
5. Проверяет, что execute_matching() мэтчит только внутри своего university_id.
6. Очищает тестовые данные.

Запуск:
    python test_isolation.py --config config/mipt.json
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from src.db import (
    init_db_pool,
    get_db_connection,
    get_pending_requests,
    get_request_details,
    get_users_without_embeddings,
)
from src.matcher import execute_matching

# Тестовые university_id: используем основной из конфига + фиктивный
FAKE_UNI_ID = 99999  # гарантированно не существует в production


def load_config(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def cleanup_test_data(real_uni_id: int):
    """Удаляет все тестовые данные перед/после теста."""
    test_user_ids = [8881001, 8881002, 8881003, 8881004]
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM coffee_requests WHERE creator_user_id = ANY(%s);",
                    (test_user_ids,),
                )
                cur.execute(
                    "DELETE FROM users WHERE user_id = ANY(%s);",
                    (test_user_ids,),
                )
                # Удаляем фиктивную кофейню и университет
                cur.execute(
                    "DELETE FROM coffee_shops WHERE university_id = %s;",
                    (FAKE_UNI_ID,),
                )
                cur.execute(
                    "DELETE FROM universities WHERE id = %s;",
                    (FAKE_UNI_ID,),
                )
                conn.commit()
        print("   Тестовые данные очищены.")
    except Exception as e:
        print(f"   Ошибка очистки: {e}")


def setup_test_data(real_uni_id: int):
    """
    Создает тестовые данные для двух university_id:
    - 2 пользователя + 2 заявки для real_uni_id
    - 2 пользователя + 2 заявки для FAKE_UNI_ID
    """
    now = datetime.now(timezone.utc)
    meet_time = now + timedelta(hours=2)

    # Пользователи для реального университета
    real_users = [
        (8881001, "iso_real_1", "Реальный1", "Люблю физику и математику", real_uni_id),
        (8881002, "iso_real_2", "Реальный2", "Интересуюсь биологией и химией", real_uni_id),
    ]
    # Пользователи для фиктивного университета
    fake_users = [
        (8881003, "iso_fake_1", "Фейковый1", "Музыка и искусство", FAKE_UNI_ID),
        (8881004, "iso_fake_2", "Фейковый2", "Спорт и фитнес", FAKE_UNI_ID),
    ]

    user_sql = """
        INSERT INTO users (user_id, username, first_name, bio, is_active, created_at, last_seen, university_id)
        VALUES (%s, %s, %s, %s, TRUE, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            bio = EXCLUDED.bio,
            university_id = EXCLUDED.university_id,
            embedding = NULL,
            last_seen = EXCLUDED.last_seen;
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Создаем фиктивный университет (для FK constraint)
                cur.execute(
                    """INSERT INTO universities (id, slug, name, is_active)
                       VALUES (%s, %s, %s, TRUE)
                       ON CONFLICT (id) DO NOTHING;""",
                    (FAKE_UNI_ID, "fake_test_uni", "Fake Test University"),
                )

                # Создаем пользователей
                for uid, uname, fname, bio, uni in real_users + fake_users:
                    cur.execute(user_sql, (uid, uname, fname, bio, now, now, uni))

                # Получаем shop_id для реального университета
                cur.execute(
                    "SELECT shop_id FROM coffee_shops WHERE university_id = %s LIMIT 1;",
                    (real_uni_id,),
                )
                real_shop = cur.fetchone()
                if not real_shop:
                    print("❌ Нет кофеен для real_uni_id. Запустите seeder.")
                    return None
                real_shop_id = real_shop[0]

                # Фиксируем SERIAL sequence для coffee_shops (может быть out-of-sync)
                cur.execute(
                    "SELECT setval('coffee_shops_shop_id_seq', (SELECT COALESCE(MAX(shop_id), 0) FROM coffee_shops));"
                )

                # Создаем фиктивную кофейню для FAKE_UNI_ID
                cur.execute(
                    """INSERT INTO coffee_shops (name, university_id)
                       VALUES ('Test Fake Shop', %s)
                       ON CONFLICT (name, university_id) DO UPDATE SET name = EXCLUDED.name
                       RETURNING shop_id;""",
                    (FAKE_UNI_ID,),
                )
                fake_shop_id = cur.fetchone()[0]

                # Создаем pending заявки
                req_sql = """
                    INSERT INTO coffee_requests (creator_user_id, shop_id, meet_time, status, university_id, created_at)
                    VALUES (%s, %s, %s, 'pending', %s, %s)
                    RETURNING request_id;
                """

                real_request_ids = []
                for uid, _, _, _, uni in real_users:
                    cur.execute(req_sql, (uid, real_shop_id, meet_time, real_uni_id, now))
                    real_request_ids.append(cur.fetchone()[0])

                fake_request_ids = []
                for uid, _, _, _, uni in fake_users:
                    cur.execute(req_sql, (uid, fake_shop_id, meet_time, FAKE_UNI_ID, now))
                    fake_request_ids.append(cur.fetchone()[0])

                conn.commit()

        print(f"   Real uni ({real_uni_id}): users [8881001, 8881002], requests {real_request_ids}")
        print(f"   Fake uni ({FAKE_UNI_ID}): users [8881003, 8881004], requests {fake_request_ids}")

        return {
            "real_uni_id": real_uni_id,
            "fake_uni_id": FAKE_UNI_ID,
            "real_user_ids": [8881001, 8881002],
            "fake_user_ids": [8881003, 8881004],
            "real_request_ids": real_request_ids,
            "fake_request_ids": fake_request_ids,
        }
    except Exception as e:
        print(f"❌ Ошибка создания тестовых данных: {e}")
        return None


def test_get_pending_requests_isolation(data):
    """
    Тест 1: get_pending_requests() должен видеть только заявки своего university.
    """
    print("\n--- Тест 1: get_pending_requests() изоляция ---")
    passed = True

    # Пользователь 8881003 (fake_uni) запрашивает pending — не должен видеть заявки real_uni
    real_pending = get_pending_requests(user_id=8881003, uni_id=data["real_uni_id"])
    real_req_ids_found = [r[0] for r in real_pending]

    # Проверяем, что заявки fake_uni не попали в результат real_uni
    for fake_req_id in data["fake_request_ids"]:
        if fake_req_id in real_req_ids_found:
            print(f"   ❌ FAIL: Заявка {fake_req_id} (fake_uni) видна через real_uni_id!")
            passed = False

    # Пользователь 8881001 (real_uni) запрашивает pending — не должен видеть заявки fake_uni
    fake_pending = get_pending_requests(user_id=8881001, uni_id=data["fake_uni_id"])
    fake_req_ids_found = [r[0] for r in fake_pending]

    for real_req_id in data["real_request_ids"]:
        if real_req_id in fake_req_ids_found:
            print(f"   ❌ FAIL: Заявка {real_req_id} (real_uni) видна через fake_uni_id!")
            passed = False

    if passed:
        print("   ✅ PASS: get_pending_requests() корректно фильтрует по university_id")
    return passed


def test_get_request_details_isolation(data):
    """
    Тест 2: get_request_details() с чужим uni_id должен возвращать пустой результат.
    """
    print("\n--- Тест 2: get_request_details() изоляция ---")
    passed = True

    # Запрашиваем заявку real_uni, но с fake_uni_id — должен быть пустой результат
    for req_id in data["real_request_ids"]:
        details = get_request_details(req_id, uni_id=data["fake_uni_id"])
        if details:
            print(f"   ❌ FAIL: Заявка {req_id} (real_uni) видна через fake_uni_id!")
            passed = False

    # Запрашиваем заявку fake_uni, но с real_uni_id — должен быть пустой результат
    for req_id in data["fake_request_ids"]:
        details = get_request_details(req_id, uni_id=data["real_uni_id"])
        if details:
            print(f"   ❌ FAIL: Заявка {req_id} (fake_uni) видна через real_uni_id!")
            passed = False

    # Позитивная проверка: своя заявка видна
    for req_id in data["real_request_ids"]:
        details = get_request_details(req_id, uni_id=data["real_uni_id"])
        if not details:
            print(f"   ❌ FAIL: Заявка {req_id} (real_uni) не видна через свой uni_id!")
            passed = False

    if passed:
        print("   ✅ PASS: get_request_details() корректно фильтрует по university_id")
    return passed


def test_get_users_without_embeddings_isolation(data):
    """
    Тест 3: get_users_without_embeddings() должен возвращать только пользователей своего university.
    """
    print("\n--- Тест 3: get_users_without_embeddings() изоляция ---")
    passed = True

    # Для real_uni — не должно быть пользователей fake_uni
    real_users = get_users_without_embeddings(data["real_uni_id"], limit=100)
    real_user_ids = [u[0] for u in real_users]
    for fake_uid in data["fake_user_ids"]:
        if fake_uid in real_user_ids:
            print(f"   ❌ FAIL: User {fake_uid} (fake_uni) виден в real_uni_id!")
            passed = False

    # Для fake_uni — не должно быть пользователей real_uni
    fake_users = get_users_without_embeddings(data["fake_uni_id"], limit=100)
    fake_user_ids = [u[0] for u in fake_users]
    for real_uid in data["real_user_ids"]:
        if real_uid in fake_user_ids:
            print(f"   ❌ FAIL: User {real_uid} (real_uni) виден в fake_uni_id!")
            passed = False

    if passed:
        print("   ✅ PASS: get_users_without_embeddings() корректно фильтрует по university_id")
    return passed


def test_matching_isolation(data):
    """
    Тест 4: execute_matching() мэтчит только внутри одного university_id.
    """
    print("\n--- Тест 4: execute_matching() изоляция ---")
    passed = True

    # Проставляем фейковые embeddings напрямую (чтобы не ждать worker)
    import numpy as np

    fake_embedding_1 = np.random.rand(384).tolist()
    fake_embedding_2 = np.random.rand(384).tolist()

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                for uid in data["real_user_ids"] + data["fake_user_ids"]:
                    emb = fake_embedding_1 if uid % 2 == 1 else fake_embedding_2
                    cur.execute(
                        "UPDATE users SET embedding = %s WHERE user_id = %s;",
                        (json.dumps(emb), uid),
                    )
                conn.commit()
        print("   Фейковые embeddings проставлены.")
    except Exception as e:
        print(f"   ❌ Ошибка проставления embeddings: {e}")
        return False

    # Запускаем мэтчинг только для real_uni
    matched_real = execute_matching(data["real_uni_id"])
    print(f"   execute_matching(real_uni={data['real_uni_id']}): {matched_real} пар")

    # Проверяем, что заявки fake_uni остались pending
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT request_id, status, partner_user_id
                       FROM coffee_requests
                       WHERE request_id = ANY(%s);""",
                    (data["fake_request_ids"],),
                )
                fake_results = cur.fetchall()
                for req_id, status, partner in fake_results:
                    if status != "pending":
                        print(f"   ❌ FAIL: Заявка {req_id} (fake_uni) стала '{status}' после мэтчинга real_uni!")
                        passed = False
                    if partner is not None:
                        print(f"   ❌ FAIL: Заявка {req_id} (fake_uni) получила partner после мэтчинга real_uni!")
                        passed = False
    except Exception as e:
        print(f"   ❌ Ошибка проверки: {e}")
        return False

    if passed:
        print("   ✅ PASS: execute_matching() не затрагивает чужие university_id")
    return passed


def main():
    parser = argparse.ArgumentParser(description="Test Multi-Tenancy Isolation")
    parser.add_argument("--config", required=True, help="Path to config file")
    args = parser.parse_args()

    config = load_config(args.config)
    real_uni_id = config.get("university_id")

    if not real_uni_id:
        print("❌ university_id не найден в конфиге.")
        return

    print(f"🔒 Тест изоляции данных (multi-tenancy)")
    print(f"   Real university_id: {real_uni_id}")
    print(f"   Fake university_id: {FAKE_UNI_ID}")
    print("=" * 80)

    # Инициализация
    init_db_pool()

    # Очистка перед тестом
    print("\n📋 Подготовка: очистка тестовых данных...")
    cleanup_test_data(real_uni_id)

    # Создание тестовых данных
    print("\n📋 Создание тестовых данных для двух university_id...")
    data = setup_test_data(real_uni_id)
    if not data:
        print("❌ ТЕСТ ПРОВАЛЕН: не удалось создать тестовые данные.")
        return

    # Запуск тестов
    results = []
    results.append(test_get_pending_requests_isolation(data))
    results.append(test_get_request_details_isolation(data))
    results.append(test_get_users_without_embeddings_isolation(data))
    results.append(test_matching_isolation(data))

    # Очистка после теста
    print("\n📋 Очистка тестовых данных...")
    cleanup_test_data(real_uni_id)

    # Итог
    print("\n" + "=" * 80)
    passed = sum(results)
    total = len(results)
    if all(results):
        print(f"✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ ({passed}/{total})")
        print("   Данные разных university_id полностью изолированы!")
    else:
        print(f"❌ ТЕСТЫ НЕ ПРОЙДЕНЫ ({passed}/{total})")
        print("   Обнаружена утечка данных между университетами!")
    print("=" * 80)


if __name__ == "__main__":
    main()
