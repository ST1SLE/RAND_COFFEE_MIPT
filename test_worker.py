#!/usr/bin/env python3
"""
Тестовый скрипт для проверки работы ML Worker (генерация эмбеддингов).

Логика:
1. Добавляет 3 тестовых пользователя с разными bio.
2. Проверяет, что embedding = NULL.
3. Ждет 65 секунд (worker запускается каждые 60 сек).
4. Проверяет, что embedding заполнен.

Запуск:
    python test_worker.py --config config/mipt.json
"""
import argparse
import json
import time
from datetime import datetime
from src.db import init_db_pool, get_db_connection


def load_config(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def create_test_users(uni_id: int):
    """Создает 3 тестовых пользователя с bio, но без embedding."""
    test_users = [
        {
            "user_id": 9990001,
            "username": "test_ml_1",
            "first_name": "Алиса",
            "bio": "Люблю математику, квантовую физику и шахматы. Интересуюсь машинным обучением.",
        },
        {
            "user_id": 9990002,
            "username": "test_ml_2",
            "first_name": "Боб",
            "bio": "Занимаюсь спортом, увлекаюсь фотографией и путешествиями. Люблю кофе и новые знакомства.",
        },
        {
            "user_id": 9990003,
            "username": "test_ml_3",
            "first_name": "Карл",
            "bio": "Программист, читаю научную фантастику, играю на гитаре. Люблю обсуждать философию и технологии.",
        },
    ]

    sql = """
        INSERT INTO users (user_id, username, first_name, bio, is_active, created_at, last_seen, university_id)
        VALUES (%s, %s, %s, %s, TRUE, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            bio = EXCLUDED.bio,
            embedding = NULL,  -- Сбрасываем вектор для повторного теста
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
                print(f"✅ Добавлено {len(test_users)} тестовых пользователей.")
    except Exception as e:
        print(f"❌ Ошибка при создании тестовых пользователей: {e}")
        raise


def check_embeddings_status(uni_id: int):
    """Проверяет статус эмбеддингов для тестовых пользователей."""
    sql = """
        SELECT user_id, username,
               CASE WHEN embedding IS NULL THEN 'НЕТ' ELSE 'ДА' END as has_embedding,
               CASE WHEN embedding IS NOT NULL THEN vector_dims(embedding) ELSE 0 END as vector_dim
        FROM users
        WHERE user_id IN (9990001, 9990002, 9990003)
          AND university_id = %s
        ORDER BY user_id;
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (uni_id,))
                results = cur.fetchall()

                if not results:
                    print("⚠️  Тестовые пользователи не найдены в БД.")
                    return False

                print("\n📊 Статус эмбеддингов:")
                print("=" * 60)
                all_have_embeddings = True
                for row in results:
                    user_id, username, has_emb, dim = row
                    print(f"User {user_id} (@{username}): Embedding = {has_emb}", end="")
                    if has_emb == "ДА":
                        print(f" (размерность: {dim})")
                    else:
                        print()
                        all_have_embeddings = False
                print("=" * 60)
                return all_have_embeddings
    except Exception as e:
        print(f"❌ Ошибка при проверке эмбеддингов: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test ML Worker")
    parser.add_argument("--config", required=True, help="Path to config file")
    args = parser.parse_args()

    config = load_config(args.config)
    uni_id = config.get("university_id")

    if not uni_id:
        print("❌ university_id не найден в конфиге.")
        return

    print(f"🚀 Тестирование ML Worker для university_id={uni_id}\n")

    # Инициализация БД
    init_db_pool()

    # Шаг 1: Создание тестовых пользователей
    print("Шаг 1: Создание тестовых пользователей с bio...")
    create_test_users(uni_id)

    # Шаг 2: Проверка начального состояния
    print("\nШаг 2: Проверка начального состояния (должно быть: Embedding = НЕТ)...")
    check_embeddings_status(uni_id)

    # Шаг 3: Ожидание работы воркера
    wait_time = 65
    print(f"\nШаг 3: Ожидание {wait_time} секунд для обработки воркером...")
    print("(Worker запускается каждые 60 секунд)")
    for i in range(wait_time, 0, -5):
        print(f"  Осталось: {i} сек...", end="\r")
        time.sleep(5)
    print(" " * 50)  # Очистка строки

    # Шаг 4: Проверка финального состояния
    print("\nШаг 4: Проверка финального состояния (должно быть: Embedding = ДА)...")
    success = check_embeddings_status(uni_id)

    # Результат
    print("\n" + "=" * 60)
    if success:
        print("✅ ТЕСТ ПРОЙДЕН: Все эмбеддинги сгенерированы корректно!")
    else:
        print("❌ ТЕСТ НЕ ПРОЙДЕН: Эмбеддинги не сгенерированы.")
        print("\nПроверьте логи worker:")
        print("  docker-compose logs worker")
    print("=" * 60)


if __name__ == "__main__":
    main()
