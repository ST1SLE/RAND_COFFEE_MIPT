#!/usr/bin/env python3
"""
Тест режима "Мэтчинг по интересам" — полный lifecycle.

Проверяет:
1. set_interest_search / is_user_searching_interest / get_interest_search_count
2. create_interest_match — создание мэтча + автосброс is_searching
3. get_pending_interest_match / get_interest_match_by_id
4. propose_meeting — переговоры, negotiation_round
5. accept_meeting_proposal — создание coffee_request, статус accepted
6. decline_interest_match — отклонение
7. expire_interest_matches — таймауты (proposed 24h, negotiating 12h, rounds >= 5)
8. Изоляция по university_id

Запуск:
    DB_PORT=5433 python test_interest_matching.py --config config/mipt.json
"""
import argparse
import json
import numpy as np
from datetime import datetime, timedelta, timezone
from src.db import (
    init_db_pool,
    get_db_connection,
    set_interest_search,
    is_user_searching_interest,
    get_interest_search_count,
    create_interest_match,
    get_pending_interest_match,
    get_interest_match_by_id,
    get_new_interest_matches_for_notification,
    propose_meeting,
    accept_meeting_proposal,
    decline_interest_match,
    expire_interest_matches,
)

# Тестовые user_id (гарантированно не конфликтуют с production)
TEST_USERS = [7771001, 7771002, 7771003, 7771004]
FAKE_UNI_ID = 99998


def load_config(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def cleanup(real_uni_id: int):
    """Удаляет все тестовые данные."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Удаляем interest_matches ДО coffee_requests (FK constraint)
                cur.execute(
                    "DELETE FROM interest_matches WHERE user_1_id = ANY(%s) OR user_2_id = ANY(%s);",
                    (TEST_USERS, TEST_USERS),
                )
                # Удаляем coffee_requests
                cur.execute(
                    "DELETE FROM coffee_requests WHERE creator_user_id = ANY(%s);",
                    (TEST_USERS,),
                )
                # Удаляем пользователей
                cur.execute(
                    "DELETE FROM users WHERE user_id = ANY(%s);",
                    (TEST_USERS,),
                )
                # Удаляем фиктивные данные
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


def setup(real_uni_id: int):
    """Создает тестовых пользователей с эмбеддингами и кофейню."""
    emb_1 = np.random.rand(384).tolist()
    emb_2 = np.random.rand(384).tolist()

    users = [
        (7771001, "im_test_1", "Алиса", real_uni_id, emb_1),
        (7771002, "im_test_2", "Борис", real_uni_id, emb_2),
        (7771003, "im_test_3", "Вера", real_uni_id, emb_1),  # same embedding as Алиса
        (7771004, "im_test_4", "Глеб", FAKE_UNI_ID, emb_2),  # different uni
    ]

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Фиктивный университет для изоляции
                cur.execute(
                    """INSERT INTO universities (id, slug, name, is_active)
                       VALUES (%s, %s, %s, TRUE)
                       ON CONFLICT (id) DO NOTHING;""",
                    (FAKE_UNI_ID, "im_test_uni", "IM Test University"),
                )

                now = datetime.now(timezone.utc)
                for uid, uname, fname, uni, emb in users:
                    cur.execute(
                        """INSERT INTO users (user_id, username, first_name, is_active,
                                             created_at, last_seen, university_id, embedding)
                           VALUES (%s, %s, %s, TRUE, %s, %s, %s, %s)
                           ON CONFLICT (user_id) DO UPDATE SET
                               university_id = EXCLUDED.university_id,
                               embedding = EXCLUDED.embedding,
                               is_searching_interest_match = FALSE;""",
                        (uid, uname, fname, now, now, uni, json.dumps(emb)),
                    )

                # Кофейня для тестов
                cur.execute(
                    "SELECT shop_id FROM coffee_shops WHERE university_id = %s LIMIT 1;",
                    (real_uni_id,),
                )
                shop = cur.fetchone()
                if not shop:
                    print("   ❌ Нет кофеен для real_uni_id. Запустите seeder.")
                    return None
                shop_id = shop[0]

                conn.commit()

        print(f"   Пользователи: {[u[0] for u in users]}")
        print(f"   shop_id для тестов: {shop_id}")
        return {"real_uni_id": real_uni_id, "shop_id": shop_id}
    except Exception as e:
        print(f"   ❌ Ошибка setup: {e}")
        return None


def test_search_toggle(uni_id: int):
    """Тест 1: Включение/выключение режима поиска."""
    print("\n--- Тест 1: set_interest_search / is_user_searching / count ---")
    passed = True

    # Изначально не ищет
    if is_user_searching_interest(7771001, uni_id):
        print("   ❌ FAIL: Пользователь ищет до включения")
        passed = False

    # Включаем поиск
    set_interest_search(7771001, uni_id, True)
    set_interest_search(7771002, uni_id, True)

    if not is_user_searching_interest(7771001, uni_id):
        print("   ❌ FAIL: Пользователь не ищет после включения")
        passed = False

    count = get_interest_search_count(uni_id)
    if count < 2:
        print(f"   ❌ FAIL: count={count}, ожидалось >= 2")
        passed = False

    # Выключаем поиск
    set_interest_search(7771001, uni_id, False)
    set_interest_search(7771002, uni_id, False)

    if is_user_searching_interest(7771001, uni_id):
        print("   ❌ FAIL: Пользователь всё ещё ищет после выключения")
        passed = False

    if passed:
        print("   ✅ PASS: Переключение режима поиска работает корректно")
    return passed


def test_create_match_and_reset(uni_id: int):
    """Тест 2: Создание мэтча + автосброс is_searching."""
    print("\n--- Тест 2: create_interest_match + автосброс is_searching ---")
    passed = True

    # Включаем поиск
    set_interest_search(7771001, uni_id, True)
    set_interest_search(7771002, uni_id, True)

    # Создаем мэтч
    match_id = create_interest_match(7771001, 7771002, 0.85, uni_id)
    if not match_id:
        print("   ❌ FAIL: create_interest_match вернул None")
        return False

    print(f"   Создан match_id={match_id}")

    # Проверяем автосброс
    if is_user_searching_interest(7771001, uni_id):
        print("   ❌ FAIL: user_1 всё ещё ищет после создания мэтча")
        passed = False
    if is_user_searching_interest(7771002, uni_id):
        print("   ❌ FAIL: user_2 всё ещё ищет после создания мэтча")
        passed = False

    # Проверяем get_pending_interest_match
    m = get_pending_interest_match(7771001, uni_id)
    if not m:
        print("   ❌ FAIL: get_pending_interest_match вернул None для user_1")
        passed = False
    elif m["status"] != "proposed":
        print(f"   ❌ FAIL: статус = '{m['status']}', ожидался 'proposed'")
        passed = False

    # Проверяем get_interest_match_by_id
    m2 = get_interest_match_by_id(match_id, uni_id)
    if not m2:
        print("   ❌ FAIL: get_interest_match_by_id вернул None")
        passed = False
    elif abs(m2["similarity_score"] - 0.85) > 0.01:
        print(f"   ❌ FAIL: similarity_score = {m2['similarity_score']}, ожидалось ~0.85")
        passed = False

    if passed:
        print("   ✅ PASS: Создание мэтча и автосброс поиска работают")

    # Очищаем мэтч для следующих тестов
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM interest_matches WHERE match_id = %s;", (match_id,))
                conn.commit()
    except Exception:
        pass

    return passed


def test_notification(uni_id: int):
    """Тест 3: Уведомления о новых мэтчах."""
    print("\n--- Тест 3: get_new_interest_matches_for_notification ---")
    passed = True

    match_id = create_interest_match(7771001, 7771002, 0.75, uni_id)
    if not match_id:
        print("   ❌ FAIL: не удалось создать мэтч")
        return False

    # Первый вызов — должен вернуть мэтч
    notifs = get_new_interest_matches_for_notification(uni_id)
    found = any(n["match_id"] == match_id for n in notifs)
    if not found:
        print("   ❌ FAIL: мэтч не найден в первом вызове notification")
        passed = False

    # Второй вызов — не должен возвращать (уже отмечен)
    notifs2 = get_new_interest_matches_for_notification(uni_id)
    found2 = any(n["match_id"] == match_id for n in notifs2)
    if found2:
        print("   ❌ FAIL: мэтч повторно вернулся во втором вызове")
        passed = False

    if passed:
        print("   ✅ PASS: Уведомления работают (однократная доставка)")

    # Очищаем
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM interest_matches WHERE match_id = %s;", (match_id,))
                conn.commit()
    except Exception:
        pass

    return passed


def test_propose_and_accept(uni_id: int, shop_id: int):
    """Тест 4: Полный цикл propose → accept → coffee_request."""
    print("\n--- Тест 4: propose_meeting + accept_meeting_proposal ---")
    passed = True

    match_id = create_interest_match(7771001, 7771002, 0.90, uni_id)
    if not match_id:
        print("   ❌ FAIL: не удалось создать мэтч")
        return False

    meet_time = datetime.now(timezone.utc) + timedelta(hours=3)

    # Propose
    ok = propose_meeting(match_id, shop_id, meet_time, 7771001, uni_id)
    if not ok:
        print("   ❌ FAIL: propose_meeting вернул False")
        passed = False

    # Проверяем статус negotiating
    m = get_interest_match_by_id(match_id, uni_id)
    if m["status"] != "negotiating":
        print(f"   ❌ FAIL: статус = '{m['status']}', ожидался 'negotiating'")
        passed = False
    if m["negotiation_round"] != 1:
        print(f"   ❌ FAIL: negotiation_round = {m['negotiation_round']}, ожидался 1")
        passed = False

    # Accept
    request_id = accept_meeting_proposal(match_id, uni_id)
    if not request_id:
        print("   ❌ FAIL: accept_meeting_proposal вернул None")
        passed = False
    else:
        print(f"   Создан coffee_request #{request_id}")

    # Проверяем финальный статус
    m2 = get_interest_match_by_id(match_id, uni_id)
    if m2["status"] != "accepted":
        print(f"   ❌ FAIL: статус = '{m2['status']}', ожидался 'accepted'")
        passed = False
    if m2["coffee_request_id"] != request_id:
        print(f"   ❌ FAIL: coffee_request_id = {m2['coffee_request_id']}, ожидался {request_id}")
        passed = False

    # Проверяем coffee_request в БД
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, creator_user_id, partner_user_id FROM coffee_requests WHERE request_id = %s;",
                    (request_id,),
                )
                req = cur.fetchone()
                if not req:
                    print("   ❌ FAIL: coffee_request не найден в БД")
                    passed = False
                elif req[0] != "matched":
                    print(f"   ❌ FAIL: coffee_request.status = '{req[0]}', ожидался 'matched'")
                    passed = False
    except Exception as e:
        print(f"   ❌ Ошибка проверки: {e}")
        passed = False

    if passed:
        print("   ✅ PASS: Полный цикл propose → accept → coffee_request работает")

    # Очищаем (interest_matches до coffee_requests из-за FK)
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM interest_matches WHERE match_id = %s;", (match_id,))
                if request_id:
                    cur.execute("DELETE FROM coffee_requests WHERE request_id = %s;", (request_id,))
                conn.commit()
    except Exception:
        pass

    return passed


def test_decline(uni_id: int):
    """Тест 5: Отклонение мэтча."""
    print("\n--- Тест 5: decline_interest_match ---")
    passed = True

    match_id = create_interest_match(7771001, 7771002, 0.60, uni_id)
    if not match_id:
        print("   ❌ FAIL: не удалось создать мэтч")
        return False

    result = decline_interest_match(match_id, uni_id)
    if not result:
        print("   ❌ FAIL: decline вернул None")
        passed = False
    elif result["user_1_id"] != 7771001 or result["user_2_id"] != 7771002:
        print(f"   ❌ FAIL: decline вернул неверных пользователей: {result}")
        passed = False

    m = get_interest_match_by_id(match_id, uni_id)
    if m["status"] != "declined":
        print(f"   ❌ FAIL: статус = '{m['status']}', ожидался 'declined'")
        passed = False

    # Повторный decline не должен работать
    result2 = decline_interest_match(match_id, uni_id)
    if result2:
        print("   ❌ FAIL: повторный decline не вернул None")
        passed = False

    if passed:
        print("   ✅ PASS: Отклонение мэтча работает корректно")

    # Очищаем
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM interest_matches WHERE match_id = %s;", (match_id,))
                conn.commit()
    except Exception:
        pass

    return passed


def test_negotiation_rounds(uni_id: int, shop_id: int):
    """Тест 6: Лимит раундов переговоров (макс 5)."""
    print("\n--- Тест 6: Лимит negotiation_round (макс 5) ---")
    passed = True

    match_id = create_interest_match(7771001, 7771002, 0.80, uni_id)
    if not match_id:
        print("   ❌ FAIL: не удалось создать мэтч")
        return False

    meet_time = datetime.now(timezone.utc) + timedelta(hours=3)

    # 5 propose должны пройти
    for i in range(5):
        proposer = 7771001 if i % 2 == 0 else 7771002
        ok = propose_meeting(match_id, shop_id, meet_time, proposer, uni_id)
        if not ok:
            print(f"   ❌ FAIL: propose #{i+1} вернул False (должен был пройти)")
            passed = False
            break

    # 6-й propose НЕ должен пройти (negotiation_round уже = 5)
    ok = propose_meeting(match_id, shop_id, meet_time, 7771001, uni_id)
    if ok:
        print("   ❌ FAIL: 6-й propose прошел (должен был быть отклонен)")
        passed = False

    m = get_interest_match_by_id(match_id, uni_id)
    if m["negotiation_round"] != 5:
        print(f"   ❌ FAIL: negotiation_round = {m['negotiation_round']}, ожидалось 5")
        passed = False

    if passed:
        print("   ✅ PASS: Лимит 5 раундов переговоров соблюдается")

    # Очищаем
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM interest_matches WHERE match_id = %s;", (match_id,))
                conn.commit()
    except Exception:
        pass

    return passed


def test_expiration(uni_id: int, shop_id: int):
    """Тест 7: Автоматическая экспирация по таймаутам."""
    print("\n--- Тест 7: expire_interest_matches ---")
    passed = True

    now = datetime.now(timezone.utc)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Мэтч 1: proposed, created_at = 25 часов назад (должен экспирироваться)
                cur.execute(
                    """INSERT INTO interest_matches
                       (user_1_id, user_2_id, similarity_score, status, university_id,
                        created_at, updated_at)
                       VALUES (%s, %s, %s, 'proposed', %s, %s, %s)
                       RETURNING match_id;""",
                    (7771001, 7771002, 0.70, uni_id,
                     now - timedelta(hours=25), now - timedelta(hours=25)),
                )
                match_proposed_old = cur.fetchone()[0]

                # Мэтч 2: negotiating, updated_at = 13 часов назад (должен экспирироваться)
                cur.execute(
                    """INSERT INTO interest_matches
                       (user_1_id, user_2_id, similarity_score, status, university_id,
                        created_at, updated_at, proposed_shop_id, proposed_meet_time,
                        proposed_by, negotiation_round)
                       VALUES (%s, %s, %s, 'negotiating', %s, %s, %s, %s, %s, %s, 2)
                       RETURNING match_id;""",
                    (7771001, 7771003, 0.65, uni_id,
                     now - timedelta(hours=14), now - timedelta(hours=13),
                     shop_id, now + timedelta(hours=5), 7771001),
                )
                match_negotiating_old = cur.fetchone()[0]

                # Мэтч 3: negotiating, rounds = 5 (должен экспирироваться)
                cur.execute(
                    """INSERT INTO interest_matches
                       (user_1_id, user_2_id, similarity_score, status, university_id,
                        created_at, updated_at, proposed_shop_id, proposed_meet_time,
                        proposed_by, negotiation_round)
                       VALUES (%s, %s, %s, 'negotiating', %s, %s, %s, %s, %s, %s, 5)
                       RETURNING match_id;""",
                    (7771002, 7771003, 0.60, uni_id,
                     now - timedelta(hours=1), now - timedelta(minutes=30),
                     shop_id, now + timedelta(hours=5), 7771002),
                )
                match_max_rounds = cur.fetchone()[0]

                # Мэтч 4: proposed, created_at = 1 час назад (НЕ должен экспирироваться)
                cur.execute(
                    """INSERT INTO interest_matches
                       (user_1_id, user_2_id, similarity_score, status, university_id,
                        created_at, updated_at)
                       VALUES (%s, %s, %s, 'proposed', %s, %s, %s)
                       RETURNING match_id;""",
                    (7771001, 7771003, 0.55, uni_id,
                     now - timedelta(hours=1), now - timedelta(hours=1)),
                )
                match_fresh = cur.fetchone()[0]

                conn.commit()

        print(f"   Создано: old_proposed={match_proposed_old}, old_negotiating={match_negotiating_old}, "
              f"max_rounds={match_max_rounds}, fresh={match_fresh}")

        # Экспирируем
        expired = expire_interest_matches(uni_id)
        expired_ids = [e[0] for e in expired]

        # Проверяем
        if match_proposed_old not in expired_ids:
            print(f"   ❌ FAIL: мэтч {match_proposed_old} (proposed 25h) не экспирирован")
            passed = False

        if match_negotiating_old not in expired_ids:
            print(f"   ❌ FAIL: мэтч {match_negotiating_old} (negotiating 13h) не экспирирован")
            passed = False

        if match_max_rounds not in expired_ids:
            print(f"   ❌ FAIL: мэтч {match_max_rounds} (5 rounds) не экспирирован")
            passed = False

        if match_fresh in expired_ids:
            print(f"   ❌ FAIL: мэтч {match_fresh} (свежий 1h) ошибочно экспирирован!")
            passed = False

        # Проверяем статус свежего мэтча
        m = get_interest_match_by_id(match_fresh, uni_id)
        if m["status"] != "proposed":
            print(f"   ❌ FAIL: свежий мэтч имеет статус '{m['status']}', ожидался 'proposed'")
            passed = False

        if passed:
            print(f"   ✅ PASS: Экспирация работает ({len(expired)} мэтчей экспирировано, свежий сохранен)")

        # Очищаем
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM interest_matches WHERE match_id = ANY(%s);",
                    ([match_proposed_old, match_negotiating_old, match_max_rounds, match_fresh],),
                )
                conn.commit()

    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        passed = False

    return passed


def test_university_isolation(real_uni_id: int, shop_id: int):
    """Тест 8: Изоляция interest matching по university_id."""
    print("\n--- Тест 8: Изоляция interest matching по university_id ---")
    passed = True

    # user 7771004 принадлежит FAKE_UNI_ID
    # Создаем мэтч для real_uni
    match_id = create_interest_match(7771001, 7771002, 0.80, real_uni_id)
    if not match_id:
        print("   ❌ FAIL: не удалось создать мэтч")
        return False

    # Попытка получить мэтч через fake_uni
    m_fake = get_pending_interest_match(7771001, FAKE_UNI_ID)
    if m_fake:
        print("   ❌ FAIL: мэтч real_uni виден через fake_uni_id!")
        passed = False

    m_fake2 = get_interest_match_by_id(match_id, FAKE_UNI_ID)
    if m_fake2:
        print("   ❌ FAIL: get_interest_match_by_id с fake_uni вернул результат!")
        passed = False

    # propose через fake_uni не должен работать
    meet_time = datetime.now(timezone.utc) + timedelta(hours=3)
    ok = propose_meeting(match_id, shop_id, meet_time, 7771001, FAKE_UNI_ID)
    if ok:
        print("   ❌ FAIL: propose_meeting прошел с fake_uni_id!")
        passed = False

    # decline через fake_uni не должен работать
    result = decline_interest_match(match_id, FAKE_UNI_ID)
    if result:
        print("   ❌ FAIL: decline_interest_match прошел с fake_uni_id!")
        passed = False

    # Верификация: через real_uni всё доступно
    m_real = get_pending_interest_match(7771001, real_uni_id)
    if not m_real:
        print("   ❌ FAIL: мэтч не найден через real_uni_id!")
        passed = False

    if passed:
        print("   ✅ PASS: Interest matching полностью изолирован по university_id")

    # Очищаем
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM interest_matches WHERE match_id = %s;", (match_id,))
                conn.commit()
    except Exception:
        pass

    return passed


def main():
    parser = argparse.ArgumentParser(description="Test Interest Matching Lifecycle")
    parser.add_argument("--config", required=True, help="Path to config file")
    args = parser.parse_args()

    config = load_config(args.config)
    real_uni_id = config.get("university_id")

    if not real_uni_id:
        print("❌ university_id не найден в конфиге.")
        return

    print(f"🔍 Тест режима 'Мэтчинг по интересам'")
    print(f"   university_id: {real_uni_id}")
    print("=" * 80)

    init_db_pool()

    # Подготовка
    print("\n📋 Очистка и создание тестовых данных...")
    cleanup(real_uni_id)
    data = setup(real_uni_id)
    if not data:
        print("❌ Не удалось создать тестовые данные.")
        return

    shop_id = data["shop_id"]

    # Запуск тестов
    results = []
    results.append(test_search_toggle(real_uni_id))
    results.append(test_create_match_and_reset(real_uni_id))
    results.append(test_notification(real_uni_id))
    results.append(test_propose_and_accept(real_uni_id, shop_id))
    results.append(test_decline(real_uni_id))
    results.append(test_negotiation_rounds(real_uni_id, shop_id))
    results.append(test_expiration(real_uni_id, shop_id))
    results.append(test_university_isolation(real_uni_id, shop_id))

    # Очистка
    print("\n📋 Очистка тестовых данных...")
    cleanup(real_uni_id)

    # Итог
    print("\n" + "=" * 80)
    passed = sum(results)
    total = len(results)
    if all(results):
        print(f"✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ ({passed}/{total})")
        print("   Режим 'Мэтчинг по интересам' работает корректно!")
    else:
        print(f"❌ ТЕСТЫ НЕ ПРОЙДЕНЫ ({passed}/{total})")
        failed = [i + 1 for i, r in enumerate(results) if not r]
        print(f"   Провалены тесты: {failed}")
    print("=" * 80)


if __name__ == "__main__":
    main()
