"""
Модуль для автоматического мэтчинга пользователей на основе косинусного сходства эмбеддингов.

Используется жадный алгоритм:
1. Рассчитывает матрицу сходства между всеми pending заявками
2. Выбирает пары с наибольшим сходством
3. Исключает повторные встречи (проверка истории)
"""
import numpy as np
import logging
from src.db import (
    get_pending_requests_for_matching,
    get_user_meeting_history,
    pair_user_for_request,
    get_interest_search_users,
    create_interest_match,
)

logger = logging.getLogger(__name__)


def cosine_similarity(vec1, vec2):
    """
    Вычисляет косинусное сходство между двумя векторами.

    Args:
        vec1: numpy array или list
        vec2: numpy array или list

    Returns:
        float: Косинусное сходство от -1 до 1 (обычно 0 до 1 для эмбеддингов)
    """
    vec1 = np.array(vec1)
    vec2 = np.array(vec2)

    dot_product = np.dot(vec1, vec2)
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return dot_product / (norm1 * norm2)


def parse_pgvector_string(vec_str):
    """
    Преобразует строку pgvector '[0.1, 0.2, ...]' в numpy array.

    Args:
        vec_str: строка вида '[0.1, 0.2, 0.3]' или уже список

    Returns:
        numpy array
    """
    if isinstance(vec_str, (list, np.ndarray)):
        return np.array(vec_str)

    # Убираем скобки и парсим
    vec_str = vec_str.strip('[]')
    values = [float(x.strip()) for x in vec_str.split(',')]
    return np.array(values)


def greedy_matching(requests, uni_id: int):
    """
    Жадный алгоритм мэтчинга на основе косинусного сходства.

    Args:
        requests: list из get_pending_requests_for_matching()
                  [(request_id, creator_user_id, embedding, meet_time, shop_id), ...]
        uni_id: ID университета (для фильтрации)

    Returns:
        list: Список успешных пар [(request_id_1, request_id_2), ...]
    """
    if len(requests) < 2:
        logger.info(f"Недостаточно заявок для мэтчинга ({len(requests)}). Нужно минимум 2.")
        return []

    n = len(requests)
    logger.info(f"Начинаем мэтчинг для {n} заявок...")

    # Извлекаем данные
    request_ids = [r[0] for r in requests]
    creator_ids = [r[1] for r in requests]
    embeddings = [parse_pgvector_string(r[2]) for r in requests]

    # Предзагружаем историю встреч для всех пользователей
    meeting_histories = {}
    for user_id in creator_ids:
        meeting_histories[user_id] = get_user_meeting_history(user_id, uni_id)

    # Вычисляем матрицу сходства
    similarity_matrix = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):  # Только верхний треугольник (симметричная матрица)
            sim = cosine_similarity(embeddings[i], embeddings[j])
            similarity_matrix[i][j] = sim
            similarity_matrix[j][i] = sim  # Симметрия

    # Жадный алгоритм: выбираем пары с наибольшим сходством
    matched_pairs = []
    used_indices = set()

    # Создаем список всех возможных пар с их сходством
    candidate_pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            # Проверяем, что пользователи не встречались ранее
            user_i = creator_ids[i]
            user_j = creator_ids[j]

            if user_j in meeting_histories.get(user_i, set()):
                logger.debug(f"Пропускаем пару ({user_i}, {user_j}) - они уже встречались.")
                continue

            candidate_pairs.append((i, j, similarity_matrix[i][j]))

    # Сортируем по убыванию сходства
    candidate_pairs.sort(key=lambda x: x[2], reverse=True)

    # Жадно выбираем пары
    for i, j, sim_score in candidate_pairs:
        if i in used_indices or j in used_indices:
            continue  # Уже использованы в другой паре

        request_id_i = request_ids[i]
        request_id_j = request_ids[j]
        user_i = creator_ids[i]
        user_j = creator_ids[j]

        logger.info(f"Мэтчим: Request {request_id_i} (User {user_i}) ↔ Request {request_id_j} (User {user_j}), Similarity: {sim_score:.3f}")

        matched_pairs.append((request_id_i, request_id_j))
        used_indices.add(i)
        used_indices.add(j)

    logger.info(f"✅ Сформировано {len(matched_pairs)} пар из {n} заявок.")
    return matched_pairs


def execute_matching(uni_id: int):
    """
    Основная функция для выполнения мэтчинга и записи результатов в БД.

    Args:
        uni_id: ID университета

    Returns:
        int: Количество успешных мэтчей
    """
    logger.info(f"🔄 Запуск мэтчинга для university_id={uni_id}")

    # Получаем pending заявки
    requests = get_pending_requests_for_matching(uni_id)

    if len(requests) < 2:
        logger.info("Недостаточно pending заявок с эмбеддингами для мэтчинга.")
        return 0

    # Выполняем жадный мэтчинг
    matched_pairs = greedy_matching(requests, uni_id)

    if not matched_pairs:
        logger.info("Не удалось сформировать пары.")
        return 0

    # Записываем результаты в БД
    success_count = 0
    for req_id_1, req_id_2 in matched_pairs:
        # Берем заявку с меньшим ID как "основную", вторую — как партнера
        # (можно поменять логику, если нужно)
        main_request = min(req_id_1, req_id_2)
        partner_request = max(req_id_1, req_id_2)

        # Находим user_id партнера
        partner_user_id = None
        for req in requests:
            if req[0] == partner_request:
                partner_user_id = req[1]
                break

        if not partner_user_id:
            logger.error(f"Не найден user_id для request {partner_request}")
            continue

        # Обновляем основную заявку, добавляя партнера
        if pair_user_for_request(main_request, partner_user_id, uni_id):
            success_count += 1
            logger.info(f"✅ Matched: Request {main_request} + Partner {partner_user_id}")
        else:
            logger.warning(f"❌ Не удалось замэтчить request {main_request} с partner {partner_user_id}")

    logger.info(f"🎉 Мэтчинг завершен: {success_count}/{len(matched_pairs)} пар записано в БД.")
    return success_count


INTEREST_SIMILARITY_THRESHOLD = 0.5


def execute_interest_matching(uni_id: int) -> int:
    """
    Мэтчинг по интересам: подбирает пары среди пользователей в режиме поиска.

    1. Получает всех пользователей с is_searching_interest_match=TRUE и эмбеддингами
    2. Вычисляет cosine similarity между всеми парами
    3. Фильтрует по порогу (INTEREST_SIMILARITY_THRESHOLD) и истории встреч
    4. Жадно формирует пары с наибольшим сходством
    5. Создает interest_match записи (status=proposed)

    Returns:
        int: Количество созданных мэтчей
    """
    logger.info(f"🔍 Запуск мэтчинга по интересам для university_id={uni_id}")

    users = get_interest_search_users(uni_id)

    if len(users) < 2:
        logger.info(f"Недостаточно пользователей в режиме поиска ({len(users)}). Нужно минимум 2.")
        return 0

    n = len(users)
    logger.info(f"Пользователей в пуле: {n}")

    user_ids = [u[0] for u in users]
    embeddings = [parse_pgvector_string(u[1]) for u in users]

    # Предзагружаем историю встреч
    meeting_histories = {}
    for uid in user_ids:
        meeting_histories[uid] = get_user_meeting_history(uid, uni_id)

    # Вычисляем матрицу сходства и формируем кандидатов
    candidate_pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            sim = cosine_similarity(embeddings[i], embeddings[j])

            # Порог совместимости
            if sim < INTEREST_SIMILARITY_THRESHOLD:
                continue

            # Проверяем историю встреч
            if user_ids[j] in meeting_histories.get(user_ids[i], set()):
                logger.debug(f"Пропускаем ({user_ids[i]}, {user_ids[j]}) — уже встречались.")
                continue

            candidate_pairs.append((i, j, sim))

    if not candidate_pairs:
        logger.info("Не найдено подходящих пар выше порога совместимости.")
        return 0

    # Сортируем по убыванию сходства
    candidate_pairs.sort(key=lambda x: x[2], reverse=True)

    # Жадно выбираем пары
    used_indices = set()
    success_count = 0

    for i, j, sim_score in candidate_pairs:
        if i in used_indices or j in used_indices:
            continue

        user_i = user_ids[i]
        user_j = user_ids[j]

        match_id = create_interest_match(user_i, user_j, sim_score, uni_id)
        if match_id:
            success_count += 1
            used_indices.add(i)
            used_indices.add(j)
            logger.info(
                f"✅ Interest match #{match_id}: User {user_i} ↔ User {user_j}, "
                f"Similarity: {sim_score:.3f}"
            )
        else:
            logger.warning(f"❌ Не удалось создать interest_match для ({user_i}, {user_j})")

    logger.info(f"🎉 Мэтчинг по интересам завершен: {success_count} пар создано.")
    return success_count
