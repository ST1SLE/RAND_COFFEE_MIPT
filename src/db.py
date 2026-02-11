import os
import time
import psycopg2
import psycopg2.extras
from psycopg2 import pool
from datetime import datetime, timezone
from contextlib import contextmanager
from dotenv import load_dotenv
import json
import logging

load_dotenv()

logger = logging.getLogger(__name__)

DB_POOL = None


def init_db_pool(max_retries=10, retry_delay=3):
    global DB_POOL
    for attempt in range(1, max_retries + 1):
        try:
            DB_POOL = pool.SimpleConnectionPool(
                minconn=1,
                maxconn=10,
                host=os.getenv("DB_HOST"),
                database=os.getenv("DB_NAME"),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASS"),
                port=os.getenv("DB_PORT"),
            )
            print("Database connection pool created successfully.")
            return
        except psycopg2.OperationalError as e:
            if attempt < max_retries:
                print(f"DB not ready (attempt {attempt}/{max_retries}): {e}")
                print(f"Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                print(f"Failed to connect to DB after {max_retries} attempts.")
                raise


@contextmanager
def get_db_connection():
    global DB_POOL
    if not DB_POOL:
        init_db_pool()

    conn = None
    try:
        conn = DB_POOL.getconn()
        yield conn
    except psycopg2.OperationalError as e:
        print(f"OperationalError in DB connection: {e}")
        raise
    except Exception as e:
        print(f"General DB Error: {e}")
        raise
    finally:
        if conn:
            DB_POOL.putconn(conn)


def add_or_update_user(user_id: int, username: str, first_name: str, uni_id: int):
    sql = """
    INSERT into users (user_id, username, first_name, is_active, created_at, last_seen, university_id)
    VALUES (%s,%s,%s,TRUE,%s,%s, %s)
    ON CONFLICT (user_id) DO UPDATE SET
        username = EXCLUDED.username,
        first_name = EXCLUDED.first_name,
        last_seen = EXCLUDED.last_seen,
        university_id = EXCLUDED.university_id;
    """

    now = datetime.now()

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, username, first_name, now, now, uni_id))
                conn.commit()
    except Exception as e:
        print(f"DB ERROR in add_or_update_user: {e}")


def add_coffee_shop(
    shop_id: int, name: str, description: str, working_hours: str, uni_id: int
):
    sql = """
    INSERT into coffee_shops (shop_id, name, description, working_hours, is_active, university_id)
    VALUES (%s, %s, %s, %s, TRUE, %s)
    on CONFLICT (shop_id) DO UPDATE SET
        name = EXCLUDED.name,
        description = EXCLUDED.description,
        working_hours = EXCLUDED.working_hours,
        is_active = TRUE,
        university_id = EXCLUDED.university_id;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (shop_id, name, description, working_hours, uni_id))
                conn.commit()
    except Exception as e:
        print(f"DB ERROR in add_coffee_shop: {e}")


def get_active_coffee_shops(uni_id: int) -> list:
    sql = "SELECT shop_id, name, promo_label FROM coffee_shops WHERE is_active = TRUE AND university_id = %s ORDER BY name;"

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (uni_id,))
                return cur.fetchall()
    except Exception as e:
        print(f"error in get_active_coffee_shops(): {e}")
        return []


def get_all_active_users(uni_id: int) -> list:
    sql = "SELECT user_id FROM users WHERE is_active = TRUE AND university_id = %s;"
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (uni_id,))
                return [row[0] for row in cur.fetchall()]
    except Exception as e:
        print(f"ERROR in get_all_active_users: {e}")
        return []


def get_shop_details(shop_id: int, uni_id: int) -> dict:
    sql = "SELECT name, description FROM coffee_shops WHERE shop_id = %s AND university_id = %s;"
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (shop_id, uni_id))
                result = cur.fetchone()
                return result if result else {}
    except Exception as e:
        print(f"error in get_shop_details(): {e}")
        return {}


def get_shop_working_hours(shop_id: int, uni_id: int) -> dict:
    sql = "SELECT working_hours FROM coffee_shops WHERE shop_id = %s AND university_id = %s;"
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (shop_id, uni_id))
                result = cur.fetchone()
                if result:
                    return result["working_hours"]
                return {}
    except Exception as e:
        print(f"error in get_shop_working_hours(): {e}")
        return {}


def create_coffee_request(
    creator_user_id: int, shop_id: int, meet_time: datetime, uni_id: int
):
    sql = """INSERT INTO coffee_requests (
        creator_user_id,
        shop_id,
        meet_time,
        status,
        created_at,
        is_reminder_sent,
        is_failure_notification_sent,
        university_id
    )
    VALUES (%s, %s, %s, 'pending', %s, FALSE, FALSE, %s)
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                now_utc = datetime.now(timezone.utc)
                cur.execute(sql, (creator_user_id, shop_id, meet_time, now_utc, uni_id))
                conn.commit()
                print(
                    f"SUCCESS in creating coffee request for user with id: {creator_user_id}"
                )
    except Exception as e:
        print(f"error in create_coffee_request(): {e}")


def get_pending_requests(user_id: int, uni_id: int) -> list:
    sql = """
    SELECT
        r.request_id,
        s.name,
        s.promo_label,
        r.meet_time,
        u.coffee_streak,
        CASE
            WHEN u.embedding IS NOT NULL AND viewer.embedding IS NOT NULL
            THEN GREATEST(0, ROUND((1 - (u.embedding <=> viewer.embedding))::numeric * 100))
            ELSE NULL
        END as similarity_percent
    FROM
        coffee_requests AS r
    JOIN
        coffee_shops AS s ON r.shop_id = s.shop_id
    JOIN
        users AS u ON r.creator_user_id = u.user_id
    LEFT JOIN
        users AS viewer ON viewer.user_id = %s AND viewer.university_id = %s
    WHERE
        r.status = 'pending'
        AND r.creator_user_id != %s
        AND r.meet_time > NOW()
        AND r.university_id = %s
    ORDER BY
        similarity_percent DESC NULLS LAST,
        r.meet_time ASC;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, uni_id, user_id, uni_id))
                return cur.fetchall()
    except Exception as e:
        print(f"ERROR in get_pending_requests(): {e}")
        return []


def increment_streaks(request_id: int, uni_id: int):
    sql = """
    UPDATE users
    SET coffee_streak = coffee_streak + 1
    WHERE user_id IN (
        SELECT creator_user_id FROM coffee_requests WHERE request_id = %s AND university_id = %s
        UNION
        SELECT partner_user_id FROM coffee_requests WHERE request_id = %s AND university_id = %s
    ) AND university_id = %s;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (request_id, uni_id, request_id, uni_id, uni_id))
                conn.commit()
    except Exception as e:
        print(f"ERROR in increment_streaks: {e}")


def reset_user_streak(user_id: int, uni_id: int):
    sql = (
        "UPDATE users SET coffee_streak = 0 WHERE user_id = %s AND university_id = %s;"
    )
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, uni_id))
                conn.commit()
    except Exception as e:
        print(f"ERROR in reset_user_streak: {e}")


def get_request_details(request_id: int, uni_id: int) -> dict:
    """
    Получает детали заявки по request_id.
    Фильтрация по university_id обязательна (SaaS-compliance).
    """
    sql = """
    SELECT
        r.creator_user_id,
        c.username as creator_username,
        c.first_name as creator_first_name,
        r.partner_user_id,
        p.username as partner_username,
        p.first_name as partner_first_name,
        s.name as shop_name,
        r.meet_time
    FROM
        coffee_requests as r
    JOIN
        coffee_shops as s ON r.shop_id = s.shop_id
    JOIN
        users as c ON r.creator_user_id = c.user_id
    LEFT JOIN
        users as p ON r.partner_user_id = p.user_id
    WHERE
        r.request_id = %s
        AND r.university_id = %s;
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (request_id, uni_id))
                result = cur.fetchone()
                return result if result else {}
    except Exception as e:
        print(f"ERROR in get_request_details(): {e}")
        return {}


def get_user_details(user_id: int, uni_id: int) -> dict:
    sql = """
    SELECT
        username,
        first_name,
        phystech_school,
        year_as_student,
        coffee_streak,
        bio
    FROM
        users
    WHERE
        user_id = %s AND university_id = %s;
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, uni_id))
                result = cur.fetchone()
                if result:
                    return {
                        "username": result[0],
                        "first_name": result[1],
                        "phystech_school": result[2],
                        "year_as_student": result[3],
                        "coffee_streak": result[4] if result[4] else 0,
                        "bio": result[5],  # Добавили возврат bio
                    }
    except Exception as e:
        print(f"ERROR in get_user_details(): {e}")
    return {}


def update_user_profile(
    user_id: int, school: str, year: int | None, bio: str | None, uni_id: int
):
    sql = """
    UPDATE users
    SET
        phystech_school = %s,
        year_as_student = %s,
        bio = %s,
        embedding = NULL  -- Сбрасываем вектор, так как текст изменился
    WHERE
        user_id = %s AND university_id = %s;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (school, year, bio, user_id, uni_id))
                conn.commit()
    except Exception as e:
        print(f"ERROR in update_user_profile(): {e}")


def update_user_bio(user_id: int, bio: str, uni_id: int):
    """
    Обновляет только поле "О себе" и сбрасывает эмбеддинг для пересчета.
    """
    sql = """
    UPDATE users
    SET
        bio = %s,
        embedding = NULL
    WHERE
        user_id = %s AND university_id = %s;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (bio, user_id, uni_id))
                conn.commit()
    except Exception as e:
        print(f"ERROR in update_user_bio(): {e}")


def get_user_requests(user_id: int, uni_id: int) -> list:
    sql = """
    SELECT
        r.request_id,
        r.status,
        r.meet_time,
        s.name as shop_name,
        r.creator_user_id,
        creator.username as creator_username,
        r.partner_user_id,
        partner.username as partner_username,
        r.is_confirmed_by_creator,
        r.is_confirmed_by_partner
    FROM
        coffee_requests as r
    JOIN
        coffee_shops as s ON r.shop_id = s.shop_id
    JOIN
        users as creator ON r.creator_user_id = creator.user_id
    LEFT JOIN
        users as partner ON r.partner_user_id = partner.user_id
    WHERE
        (r.creator_user_id = %s OR r.partner_user_id = %s)
        AND r.university_id = %s
        AND (
            (r.status IN ('pending', 'matched') AND r.meet_time > NOW())
            OR
            (r.status = 'matched' AND r.meet_time BETWEEN NOW() - INTERVAL '2 days' AND NOW())
            OR
            (r.status = 'cancelled' AND r.created_at > NOW() - INTERVAL '1 hour')
        )
    ORDER BY
        r.meet_time DESC;
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (user_id, user_id, uni_id))
                return cur.fetchall()
    except Exception as e:
        print(f"ERROR in get_user_requests(): {e}")
        return []


def pair_user_for_request(request_id: int, partner_user_id: int, uni_id: int) -> bool:
    """
    Ручной мэтчинг (v1.0 fallback): пользователь сам выбирает заявку.

    Ставим is_match_notification_sent = TRUE, чтобы notify_new_matches_job
    не отправил дубликат уведомления (уведомление уже уходит через
    notify_users_about_pairing в bot.py).
    """
    success = False
    sql = """
    UPDATE coffee_requests
    SET
        partner_user_id = %s,
        status = 'matched',
        is_match_notification_sent = TRUE,
        is_confirmed_by_partner = CASE
            WHEN meet_time < (NOW() + INTERVAL '45 minutes') THEN TRUE
            ELSE FALSE
        END,
        is_confirmed_by_creator = CASE
            WHEN meet_time < (NOW() + INTERVAL '45 minutes') THEN TRUE
            ELSE FALSE
        END,
        is_confirmation_sent = CASE
            WHEN meet_time < (NOW() + INTERVAL '45 minutes') THEN TRUE
            ELSE FALSE
        END
    WHERE
        request_id = %s
        AND status = 'pending'
        AND partner_user_id IS NULL
        AND university_id = %s;
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (partner_user_id, request_id, uni_id),
                )

                if cur.rowcount == 1:
                    conn.commit()
                    success = True
                else:
                    conn.rollback()
    except Exception as e:
        print(f"ERROR in pair_user_for_request(): {e}")
        success = False

    return success


def log_cancellation_event(conn, request_id: int, user_id: int, event_type: str):
    sql = """
    INSERT INTO cancellation_logs (request_id, user_id, event_type, event_time)
    VALUES (%s, %s, %s, NOW());
    """
    with conn.cursor() as cur:
        cur.execute(sql, (request_id, user_id, event_type))


def cancel_request(request_id: int, user_id: int, uni_id: int) -> bool:
    success = False
    sql = """
    UPDATE
        coffee_requests
    SET
        status = 'cancelled'
    WHERE
        request_id = %s
        AND creator_user_id = %s
        AND university_id = %s
        AND status = 'pending';
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (request_id, user_id, uni_id))
                if cur.rowcount == 1:
                    log_cancellation_event(
                        conn, request_id, user_id, "creator_cancel_pending"
                    )
                    conn.commit()
                    success = True
                else:
                    conn.rollback()
    except Exception as e:
        print(f"DB ERROR in cancel_request(): {e}")
        success = False

    return success


def cancel_request_by_creator(
    request_id: int, creator_user_id: int, uni_id: int
) -> int | None:
    check_sql = """
        SELECT is_confirmed_by_creator 
        FROM coffee_requests 
        WHERE request_id = %s AND university_id = %s
    """
    update_sql = """
    UPDATE coffee_requests
    SET status = 'cancelled'
    WHERE request_id = %s 
      AND creator_user_id = %s 
      AND status = 'matched'
      AND university_id = %s
    RETURNING partner_user_id;
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(check_sql, (request_id, uni_id))
                res = cur.fetchone()
                should_reset_streak = res[0] if res else False

                cur.execute(update_sql, (request_id, creator_user_id, uni_id))
                if cur.rowcount == 1:
                    partner_id = cur.fetchone()[0]

                    if should_reset_streak:
                        cur.execute(
                            "UPDATE users SET coffee_streak = 0 WHERE user_id = %s AND university_id = %s",
                            (creator_user_id, uni_id),
                        )

                    log_cancellation_event(
                        conn, request_id, creator_user_id, "creator_cancel_matched"
                    )
                    conn.commit()
                    return partner_id
                else:
                    conn.rollback()
                    return None
    except Exception as e:
        print(f"DB ERROR in cancel_request_by_creator(): {e}")
        return None


def unmatch_request(request_id: int, partner_user_id: int, uni_id: int) -> int | None:
    check_sql = """
        SELECT is_confirmed_by_partner 
        FROM coffee_requests 
        WHERE request_id = %s AND university_id = %s
    """
    update_sql = """
    UPDATE coffee_requests
    SET status = 'pending', partner_user_id = NULL
    WHERE request_id = %s 
      AND partner_user_id = %s 
      AND status = 'matched'
      AND university_id = %s
    RETURNING creator_user_id;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(check_sql, (request_id, uni_id))
                res = cur.fetchone()
                should_reset_streak = res[0] if res else False

                cur.execute(update_sql, (request_id, partner_user_id, uni_id))
                if cur.rowcount == 1:
                    creator_id = cur.fetchone()[0]

                    if should_reset_streak:
                        cur.execute(
                            "UPDATE users SET coffee_streak = 0 WHERE user_id = %s AND university_id = %s",
                            (partner_user_id, uni_id),
                        )

                    log_cancellation_event(
                        conn, request_id, partner_user_id, "partner_unmatch"
                    )
                    conn.commit()
                    return creator_id
                else:
                    conn.rollback()
                    return None
    except Exception as e:
        print(f"DB ERROR in unmatch_request(): {e}")
        return None


def get_meetings_for_icebreaker(uni_id: int) -> list:
    sql = """
    UPDATE coffee_requests
    SET is_icebreaker_sent = TRUE
    WHERE request_id IN (
        SELECT request_id
        FROM coffee_requests
        WHERE
            status = 'matched'
            AND is_icebreaker_sent = FALSE
            AND is_confirmed_by_creator = TRUE
            AND is_confirmed_by_partner = TRUE
            AND meet_time BETWEEN NOW() AND NOW() + INTERVAL '7 minutes'
            AND university_id = %s
        FOR UPDATE SKIP LOCKED
    )
    RETURNING
        request_id,
        creator_user_id,
        (SELECT username FROM users WHERE user_id = creator_user_id) as creator_username,
        partner_user_id,
        (SELECT username FROM users WHERE user_id = partner_user_id) as partner_username,
        (SELECT name FROM coffee_shops WHERE shop_id = coffee_requests.shop_id) AS shop_name,
        (SELECT partner_chat_id FROM coffee_shops WHERE shop_id = coffee_requests.shop_id) AS partner_chat_id,
        (SELECT discount_amount FROM coffee_shops WHERE shop_id = coffee_requests.shop_id) AS discount_amount,
        meet_time;
    """

    meetings = []
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (uni_id,))
                meetings = cur.fetchall()
                conn.commit()
    except Exception as e:
        print(f"ERROR in get_meetings_for_icebreaker(): {e}")

    return meetings


def save_verification_code(request_id: int, code: str, uni_id: int):
    """
    Сохраняет код верификации для заявки.
    Фильтрация по university_id обязательна (SaaS-compliance).
    """
    sql = "UPDATE coffee_requests SET verification_code = %s WHERE request_id = %s AND university_id = %s;"
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (code, request_id, uni_id))
                conn.commit()
    except Exception as e:
        print(f"ERROR in save_verification_code: {e}")


def get_meetings_for_reminder(uni_id: int) -> list:
    sql = """
    UPDATE coffee_requests
    SET is_reminder_sent = TRUE
    WHERE request_id IN (
        SELECT request_id
        FROM coffee_requests
        WHERE
            status = 'matched'
            AND is_reminder_sent = FALSE
            AND is_confirmed_by_creator = TRUE
            AND is_confirmed_by_partner = TRUE
            AND meet_time BETWEEN NOW() AND NOW() + INTERVAL '20 minutes'
            AND university_id = %s
        FOR UPDATE SKIP LOCKED
    )
    RETURNING
        request_id,
        creator_user_id,
        (SELECT username FROM users WHERE user_id = creator_user_id) AS creator_username,
        (SELECT first_name FROM users WHERE user_id = creator_user_id) AS creator_first_name,
        partner_user_id,
        (SELECT username FROM users WHERE user_id = partner_user_id) AS partner_username,
        (SELECT first_name FROM users WHERE user_id = partner_user_id) AS partner_first_name,
        (SELECT name FROM coffee_shops WHERE shop_id = coffee_requests.shop_id) AS shop_name,
        meet_time;
    """

    meetings = []
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (uni_id,))
                meetings = cur.fetchall()
                conn.commit()
    except Exception as e:
        print(f"ERROR in get_meetings_for_reminder(): {e}")

    return meetings


def mark_reminder_as_sent(request_id: int, uni_id: int) -> bool:
    sql = """
    UPDATE
        coffee_requests
    SET
        is_reminder_sent = TRUE
    WHERE
        request_id = %s
        AND university_id = %s;
    """
    success = False

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (request_id, uni_id))
                if cur.rowcount == 1:
                    conn.commit()
                    success = True
    except Exception as e:
        print(f"ERROR in mark_reminder_as_sent(): {e}")

    return success


def get_meetings_to_confirm(uni_id: int) -> list:
    sql = """
    UPDATE coffee_requests
    SET is_confirmation_sent = TRUE
    WHERE request_id IN (
        SELECT request_id
        FROM coffee_requests
        WHERE
            status = 'matched'
            AND is_confirmation_sent = FALSE
            AND meet_time > NOW()
            AND meet_time < (NOW() + INTERVAL '130 minutes')
            AND university_id = %s
        FOR UPDATE SKIP LOCKED
    )
    RETURNING request_id, creator_user_id, partner_user_id, meet_time;
    """
    meetings = []
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (uni_id,))
                meetings = cur.fetchall()
                conn.commit()
    except Exception as e:
        print(f"ERROR in get_meetings_to_confirm(): {e}")
    return meetings


def confirm_meeting_participation(request_id: int, user_id: int, uni_id: int) -> bool:
    sql = """
    UPDATE coffee_requests
    SET 
        is_confirmed_by_creator = CASE WHEN creator_user_id = %s THEN TRUE ELSE is_confirmed_by_creator END,
        is_confirmed_by_partner = CASE WHEN partner_user_id = %s THEN TRUE ELSE is_confirmed_by_partner END
    WHERE request_id = %s  AND university_id = %s
    RETURNING is_confirmed_by_creator, is_confirmed_by_partner;
    """

    both_confirmed = False
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, user_id, request_id, uni_id))
                result = cur.fetchone()
                conn.commit()

                if result:
                    # result[0] - creator, result[1] - partner
                    if result[0] and result[1]:
                        both_confirmed = True
    except Exception as e:
        print(f"ERROR in confirm_meeting_participation: {e}")

    return both_confirmed


def increment_no_show_counter(user_id: int, uni_id: int) -> int:
    sql = """
    UPDATE users 
    SET no_show_count = no_show_count + 1 
    WHERE user_id = %s AND university_id = %s
    RETURNING no_show_count;
    """
    new_count = 0
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, uni_id))
                result = cur.fetchone()
                if result:
                    new_count = result[0]
                conn.commit()
    except Exception as e:
        print(f"ERROR in increment_no_show_counter: {e}")
    return new_count


def cancel_unconfirmed_matches(uni_id: int) -> list:
    sql = """
    UPDATE coffee_requests r
    SET status = 'cancelled'
    FROM coffee_shops s
    WHERE r.shop_id = s.shop_id
      AND r.status = 'matched'
      AND r.meet_time < (NOW() + INTERVAL '25 minutes')
      AND r.meet_time > (NOW() - INTERVAL '1 hour') 
      AND (r.is_confirmed_by_creator = FALSE OR r.is_confirmed_by_partner = FALSE)
      AND r.university_id = %s
    RETURNING
        r.request_id,
        r.creator_user_id,
        r.partner_user_id,
        s.name as shop_name,
        r.is_confirmed_by_creator,
        r.is_confirmed_by_partner;
    """
    cancelled_meetings = []
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (uni_id,))
                cancelled_meetings = cur.fetchall()
                conn.commit()
    except Exception as e:
        print(f"ERROR in cancel_unconfirmed_matches(): {e}")
    return cancelled_meetings


def expire_pending_requests(uni_id: int) -> list:
    sql = """
    UPDATE
        coffee_requests r
    SET
        status = 'expired',
        is_failure_notification_sent = TRUE
    FROM
        coffee_shops s
    WHERE
        r.shop_id = s.shop_id
        AND r.status = 'pending'
        AND r.meet_time < (NOW() + INTERVAL '10 minutes')
        AND r.is_failure_notification_sent = FALSE
        AND r.university_id = %s
    RETURNING
        r.request_id, r.creator_user_id, s.name as shop_name, r.meet_time;
    """

    expired_requests = []

    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (uni_id,))
                conn.commit()
                expired_requests = cur.fetchall()
    except Exception as e:
        print(f"ERROR in expire_pending_requests(): {e}")

    return expired_requests


def get_meetings_for_feedback(uni_id: int) -> list:
    sql = """
    SELECT
        r.request_id, r.creator_user_id, r.partner_user_id, s.name as shop_name, r.meet_time
    FROM coffee_requests r
    JOIN coffee_shops s ON r.shop_id = s.shop_id
    WHERE
        r.status = 'matched'
        AND r.is_feedback_requested = FALSE
        AND r.is_confirmed_by_creator = TRUE
        AND r.is_confirmed_by_partner = TRUE
        AND r.meet_time < (NOW() - INTERVAL '30 minutes')
        AND r.university_id = %s;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (uni_id,))
                return cur.fetchall()
    except Exception as e:
        print(f"ERROR in get_meetings_for_feedback(): {e}")
        return []


def mark_feedback_as_requested(request_id: int, uni_id: int) -> bool:
    sql = "UPDATE coffee_requests SET is_feedback_requested = TRUE WHERE request_id = %s AND university_id = %s;"
    success = False
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (request_id, uni_id))
                if cur.rowcount == 1:
                    conn.commit()
                    success = True
    except Exception as e:
        print(f"ERROR in mark_feedback_as_requested(): {e}")
    return success


def save_feedback_text(request_id: int, text: str, uni_id: int):
    sql = """
    UPDATE coffee_requests 
    SET feedback_text = COALESCE(feedback_text || '\n---\n', '') || %s 
    WHERE request_id = %s AND university_id = %s;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (text, request_id, uni_id))
                conn.commit()
    except Exception as e:
        print(f"ERROR in save_feedback_text(): {e}")


def save_meeting_outcome(request_id: int, outcome: str, uni_id: int) -> bool:
    sql = """
    UPDATE coffee_requests 
    SET meeting_outcome = %s 
    WHERE request_id = %s
        AND university_id = %s
        AND meeting_outcome IS NULL
    RETURNING request_id;
    """
    success = False
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (outcome, request_id, uni_id))
                if cur.fetchone():
                    conn.commit()
                    success = True
    except Exception as e:
        print(f"ERROR in save_meeting_outcome(): {e}")
    return success


def ban_user(user_id: int, uni_id: int):
    sql = (
        "UPDATE users SET is_active = FALSE WHERE user_id = %s AND university_id = %s;"
    )
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, uni_id))
                conn.commit()
    except Exception as e:
        print(f"ERROR in ban_user: {e}")


def is_user_active(user_id: int, uni_id: int) -> bool:
    """
    Проверяет, активен ли пользователь.
    uni_id обязателен (SaaS-compliance).
    """
    sql = "SELECT is_active FROM users WHERE user_id = %s AND university_id = %s;"

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, uni_id))
                result = cur.fetchone()
                if result:
                    return result[0]
                else:
                    return True
    except Exception as e:
        print(f"ERROR in is_user_active: {e}")
        return True


def get_users_without_embeddings(uni_id: int, limit: int = 30):
    """
    Получает пользователей с bio, но без embedding для векторизации.
    Строгая фильтрация по university_id (SaaS-compliance).

    Returns:
        list: [(user_id, bio, phystech_school, year_as_student), ...]
    """
    sql = """
        SELECT user_id, bio, phystech_school, year_as_student
        FROM users
        WHERE university_id = %s
          AND bio IS NOT NULL
          AND bio != ''
          AND embedding IS NULL
        ORDER BY created_at DESC
        LIMIT %s
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (uni_id, limit))
                return cur.fetchall()
    except Exception as e:
        print(f"ERROR in get_users_without_embeddings: {e}")
        return []


def update_user_embedding(user_id: int, embedding: list, uni_id: int):
    """
    Сохраняет сгенерированный эмбеддинг в БД.
    Проверка university_id для безопасности (SaaS-compliance).

    Args:
        user_id: ID пользователя
        embedding: Вектор размерности 384 (list of floats)
        uni_id: ID университета для проверки

    Returns:
        bool: True если успешно, False если ошибка
    """
    sql = """
        UPDATE users
        SET embedding = %s
        WHERE user_id = %s
          AND university_id = %s
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # Преобразуем список в строку для pgvector: '[0.1, 0.2, ...]'
                embedding_str = '[' + ','.join(map(str, embedding)) + ']'
                cur.execute(sql, (embedding_str, user_id, uni_id))
                conn.commit()
                return cur.rowcount > 0
    except Exception as e:
        print(f"ERROR in update_user_embedding for user {user_id}: {e}")
        return False


def get_pending_requests_for_matching(uni_id: int):
    """
    Получает pending заявки с эмбеддингами создателей для автоматического мэтчинга.

    Условия:
    - status = 'pending'
    - partner_user_id IS NULL (нет партнера)
    - meet_time > NOW() (встреча в будущем)
    - creator имеет embedding (не NULL)
    - Фильтрация по university_id (SaaS-compliance)

    Returns:
        list: [(request_id, creator_user_id, embedding, meet_time, shop_id), ...]
    """
    sql = """
        SELECT
            r.request_id,
            r.creator_user_id,
            u.embedding,
            r.meet_time,
            r.shop_id
        FROM coffee_requests r
        JOIN users u ON r.creator_user_id = u.user_id
        WHERE r.status = 'pending'
          AND r.partner_user_id IS NULL
          AND r.meet_time > NOW()
          AND r.university_id = %s
          AND u.embedding IS NOT NULL
        ORDER BY r.meet_time ASC;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (uni_id,))
                return cur.fetchall()
    except Exception as e:
        print(f"ERROR in get_pending_requests_for_matching: {e}")
        return []


def get_user_meeting_history(user_id: int, uni_id: int):
    """
    Получает список user_id, с которыми пользователь уже встречался.
    Это нужно для исключения повторных встреч при мэтчинге.

    Returns:
        set: Множество user_id партнеров из прошлых встреч
    """
    sql = """
        SELECT DISTINCT
            CASE
                WHEN creator_user_id = %s THEN partner_user_id
                ELSE creator_user_id
            END as partner_id
        FROM coffee_requests
        WHERE (creator_user_id = %s OR partner_user_id = %s)
          AND status = 'matched'
          AND university_id = %s
          AND partner_user_id IS NOT NULL;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, user_id, user_id, uni_id))
                results = cur.fetchall()
                return {row[0] for row in results if row[0] is not None}
    except Exception as e:
        print(f"ERROR in get_user_meeting_history: {e}")
        return set()


def get_interest_match_history(user_id: int, uni_id: int, cooldown_days: int = 30) -> set:
    """
    Возвращает user_id, с которыми пользователь уже имел interest_match
    (любой статус) в пределах cooldown-периода.
    После cooldown_days пара может быть замэтчена повторно.
    """
    sql = """
        SELECT DISTINCT
            CASE
                WHEN user_1_id = %s THEN user_2_id
                ELSE user_1_id
            END as partner_id
        FROM interest_matches
        WHERE (user_1_id = %s OR user_2_id = %s)
          AND university_id = %s
          AND created_at > NOW() - make_interval(days => %s);
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, user_id, user_id, uni_id, cooldown_days))
                results = cur.fetchall()
                return {row[0] for row in results if row[0] is not None}
    except Exception as e:
        print(f"ERROR in get_interest_match_history: {e}")
        return set()


def get_new_matches_for_notification(uni_id: int):
    """
    Получает matched заявки, для которых еще не было отправлено уведомление.
    Используется ботом для информирования пользователей о новых матчах от ML matcher.

    Returns:
        list: [(request_id, creator_user_id, partner_user_id, meet_time), ...]
    """
    sql = """
        UPDATE coffee_requests
        SET is_match_notification_sent = TRUE
        WHERE request_id IN (
            SELECT request_id
            FROM coffee_requests
            WHERE status = 'matched'
              AND is_match_notification_sent = FALSE
              AND partner_user_id IS NOT NULL
              AND university_id = %s
            FOR UPDATE SKIP LOCKED
        )
        RETURNING request_id, creator_user_id, partner_user_id, meet_time;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (uni_id,))
                matches = cur.fetchall()
                conn.commit()
                return matches
    except Exception as e:
        print(f"ERROR in get_new_matches_for_notification: {e}")
        return []


# ============================================================
# Режим "Мэтчинг по интересам" — функции для interest_matches
# ============================================================


def set_interest_search(user_id: int, uni_id: int, active: bool):
    """Включает/выключает режим поиска по интересам для пользователя."""
    sql = """
    UPDATE users
    SET is_searching_interest_match = %s
    WHERE user_id = %s AND university_id = %s;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (active, user_id, uni_id))
                conn.commit()
    except Exception as e:
        print(f"ERROR in set_interest_search: {e}")


def is_user_searching_interest(user_id: int, uni_id: int) -> bool:
    """Проверяет, находится ли пользователь в режиме поиска по интересам."""
    sql = """
    SELECT is_searching_interest_match
    FROM users
    WHERE user_id = %s AND university_id = %s;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, uni_id))
                result = cur.fetchone()
                return result[0] if result else False
    except Exception as e:
        print(f"ERROR in is_user_searching_interest: {e}")
        return False


def get_interest_search_count(uni_id: int) -> int:
    """Возвращает количество пользователей в режиме поиска по интересам."""
    sql = """
    SELECT COUNT(*)
    FROM users
    WHERE is_searching_interest_match = TRUE
      AND university_id = %s;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (uni_id,))
                result = cur.fetchone()
                return result[0] if result else 0
    except Exception as e:
        print(f"ERROR in get_interest_search_count: {e}")
        return 0


def count_searching_users_without_embeddings(uni_id: int) -> int:
    """Количество пользователей в режиме поиска, у которых ещё нет эмбеддинга."""
    sql = """
    SELECT COUNT(*)
    FROM users
    WHERE is_searching_interest_match = TRUE
      AND university_id = %s
      AND bio IS NOT NULL
      AND bio != ''
      AND embedding IS NULL;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (uni_id,))
                result = cur.fetchone()
                return result[0] if result else 0
    except Exception as e:
        print(f"ERROR in count_searching_users_without_embeddings: {e}")
        return 0


def get_interest_search_users(uni_id: int) -> list:
    """
    Возвращает пользователей в режиме поиска с готовыми эмбеддингами.
    Returns: [(user_id, embedding), ...]
    """
    sql = """
    SELECT user_id, embedding
    FROM users
    WHERE is_searching_interest_match = TRUE
      AND embedding IS NOT NULL
      AND university_id = %s;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (uni_id,))
                return cur.fetchall()
    except Exception as e:
        print(f"ERROR in get_interest_search_users: {e}")
        return []


def create_interest_match(user_1: int, user_2: int, similarity: float, uni_id: int) -> int | None:
    """
    Создает interest_match и снимает is_searching у обоих пользователей.
    Атомарная операция в одной транзакции.
    Проверяет, что ни у одного из пользователей нет активного мэтча.
    Returns: match_id или None при ошибке/пропуске.
    """
    check_sql = """
    SELECT COUNT(*) FROM interest_matches
    WHERE status IN ('proposed', 'negotiating')
      AND university_id = %s
      AND (user_1_id IN (%s, %s) OR user_2_id IN (%s, %s));
    """
    insert_sql = """
    INSERT INTO interest_matches (user_1_id, user_2_id, similarity_score, university_id)
    VALUES (%s, %s, %s, %s)
    RETURNING match_id;
    """
    reset_sql = """
    UPDATE users
    SET is_searching_interest_match = FALSE
    WHERE user_id IN (%s, %s) AND university_id = %s;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(check_sql, (uni_id, user_1, user_2, user_1, user_2))
                if cur.fetchone()[0] > 0:
                    logger.info(f"Skipping: user {user_1} or {user_2} already has active interest_match")
                    return None
                cur.execute(insert_sql, (user_1, user_2, float(similarity), uni_id))
                match_id = cur.fetchone()[0]
                cur.execute(reset_sql, (user_1, user_2, uni_id))
                conn.commit()
                return match_id
    except Exception as e:
        logger.error(f"ERROR in create_interest_match({user_1}, {user_2}): {e}")
        return None


def get_pending_interest_match(user_id: int, uni_id: int) -> dict | None:
    """
    Возвращает активный interest_match для пользователя (proposed или negotiating).
    Пользователь может быть user_1 или user_2.
    """
    sql = """
    SELECT
        im.match_id,
        im.user_1_id,
        im.user_2_id,
        im.similarity_score,
        im.status,
        im.proposed_shop_id,
        im.proposed_meet_time,
        im.proposed_by,
        im.negotiation_round,
        im.created_at,
        im.updated_at,
        u1.first_name as user_1_name,
        u2.first_name as user_2_name,
        u1.bio as user_1_bio,
        u2.bio as user_2_bio,
        s.name as shop_name
    FROM interest_matches im
    JOIN users u1 ON im.user_1_id = u1.user_id
    JOIN users u2 ON im.user_2_id = u2.user_id
    LEFT JOIN coffee_shops s ON im.proposed_shop_id = s.shop_id
    WHERE (im.user_1_id = %s OR im.user_2_id = %s)
      AND im.status IN ('proposed', 'negotiating')
      AND im.university_id = %s
    ORDER BY im.created_at DESC
    LIMIT 1;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (user_id, user_id, uni_id))
                result = cur.fetchone()
                return dict(result) if result else None
    except Exception as e:
        print(f"ERROR in get_pending_interest_match: {e}")
        return None


def get_new_interest_matches_for_notification(uni_id: int) -> list:
    """
    Получает interest_matches со статусом 'proposed', для которых еще не отправлено уведомление.
    Атомарно ставит is_notification_sent = TRUE (UPDATE...RETURNING + FOR UPDATE SKIP LOCKED).
    """
    sql = """
    UPDATE interest_matches
    SET is_notification_sent = TRUE
    WHERE match_id IN (
        SELECT match_id
        FROM interest_matches
        WHERE status = 'proposed'
          AND is_notification_sent = FALSE
          AND university_id = %s
        FOR UPDATE SKIP LOCKED
    )
    RETURNING
        match_id,
        user_1_id,
        user_2_id,
        similarity_score,
        (SELECT first_name FROM users WHERE user_id = user_1_id) as user_1_name,
        (SELECT first_name FROM users WHERE user_id = user_2_id) as user_2_name,
        (SELECT bio FROM users WHERE user_id = user_1_id) as user_1_bio,
        (SELECT bio FROM users WHERE user_id = user_2_id) as user_2_bio;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (uni_id,))
                matches = cur.fetchall()
                conn.commit()
                return matches
    except Exception as e:
        print(f"ERROR in get_new_interest_matches_for_notification: {e}")
        return []


def propose_meeting(match_id: int, shop_id: int, meet_time: datetime, proposed_by: int, uni_id: int) -> bool:
    """
    Устанавливает предложение встречи (кофейня + время) от одного из участников.
    Переводит статус в 'negotiating', увеличивает negotiation_round.
    """
    sql = """
    UPDATE interest_matches
    SET status = 'negotiating',
        proposed_shop_id = %s,
        proposed_meet_time = %s,
        proposed_by = %s,
        negotiation_round = negotiation_round + 1,
        updated_at = NOW()
    WHERE match_id = %s
      AND status IN ('proposed', 'negotiating')
      AND university_id = %s
      AND negotiation_round < 5;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (shop_id, meet_time, proposed_by, match_id, uni_id))
                success = cur.rowcount == 1
                conn.commit()
                return success
    except Exception as e:
        print(f"ERROR in propose_meeting: {e}")
        return False


def accept_meeting_proposal(match_id: int, uni_id: int) -> int | None:
    """
    Принимает предложение встречи: создает coffee_request со статусом 'matched',
    обновляет interest_match → 'accepted'. Атомарная транзакция.

    Спонтанные встречи (< 45 мин до meet_time): оба автоподтверждены.

    Returns: request_id созданной заявки или None при ошибке.
    """
    get_sql = """
    SELECT match_id, user_1_id, user_2_id, proposed_shop_id, proposed_meet_time
    FROM interest_matches
    WHERE match_id = %s
      AND status = 'negotiating'
      AND proposed_shop_id IS NOT NULL
      AND proposed_meet_time IS NOT NULL
      AND university_id = %s
    FOR UPDATE;
    """
    create_request_sql = """
    INSERT INTO coffee_requests (
        creator_user_id, partner_user_id, shop_id, meet_time,
        status, created_at, is_reminder_sent, is_failure_notification_sent,
        is_match_notification_sent,
        is_confirmed_by_creator, is_confirmed_by_partner, is_confirmation_sent,
        university_id
    ) VALUES (
        %s, %s, %s, %s,
        'matched', NOW(), FALSE, FALSE,
        TRUE,
        CASE WHEN %s < (NOW() + INTERVAL '45 minutes') THEN TRUE ELSE FALSE END,
        CASE WHEN %s < (NOW() + INTERVAL '45 minutes') THEN TRUE ELSE FALSE END,
        CASE WHEN %s < (NOW() + INTERVAL '45 minutes') THEN TRUE ELSE FALSE END,
        %s
    )
    RETURNING request_id;
    """
    update_match_sql = """
    UPDATE interest_matches
    SET status = 'accepted',
        coffee_request_id = %s,
        updated_at = NOW()
    WHERE match_id = %s AND university_id = %s;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(get_sql, (match_id, uni_id))
                match = cur.fetchone()
                if not match:
                    conn.rollback()
                    return None

                _, user_1, user_2, shop_id, meet_time = match

                cur.execute(create_request_sql, (
                    user_1, user_2, shop_id, meet_time,
                    meet_time, meet_time, meet_time,
                    uni_id
                ))
                request_id = cur.fetchone()[0]

                cur.execute(update_match_sql, (request_id, match_id, uni_id))
                conn.commit()
                return request_id
    except Exception as e:
        print(f"ERROR in accept_meeting_proposal: {e}")
        return None


def decline_interest_match(match_id: int, uni_id: int) -> dict | None:
    """
    Отклоняет interest_match. Возвращает данные обоих пользователей для уведомления.
    """
    sql = """
    UPDATE interest_matches
    SET status = 'declined', updated_at = NOW()
    WHERE match_id = %s
      AND status IN ('proposed', 'negotiating')
      AND university_id = %s
    RETURNING user_1_id, user_2_id;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (match_id, uni_id))
                result = cur.fetchone()
                conn.commit()
                if result:
                    return {"user_1_id": result[0], "user_2_id": result[1]}
                return None
    except Exception as e:
        print(f"ERROR in decline_interest_match: {e}")
        return None


def expire_interest_matches(uni_id: int) -> list:
    """
    Экспирирует interest_matches по таймаутам:
    - proposed: 24ч без реакции
    - negotiating: 12ч без ответа на предложение
    - negotiation_round >= 5: слишком много раундов

    Returns: [(match_id, user_1_id, user_2_id), ...]
    """
    sql = """
    UPDATE interest_matches
    SET status = 'expired', updated_at = NOW()
    WHERE match_id IN (
        SELECT match_id
        FROM interest_matches
        WHERE university_id = %s
          AND status IN ('proposed', 'negotiating')
          AND (
              (status = 'proposed' AND created_at < NOW() - INTERVAL '24 hours')
              OR (status = 'negotiating' AND updated_at < NOW() - INTERVAL '12 hours')
              OR (negotiation_round >= 5)
          )
        FOR UPDATE SKIP LOCKED
    )
    RETURNING match_id, user_1_id, user_2_id;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (uni_id,))
                expired = cur.fetchall()
                conn.commit()
                return expired
    except Exception as e:
        print(f"ERROR in expire_interest_matches: {e}")
        return []


def get_stale_interest_proposals(uni_id: int) -> list:
    """
    Находит negotiating interest_matches, где партнер не ответил >6 часов
    и напоминание ещё не отправлено.
    """
    sql = """
    SELECT
        im.match_id,
        im.user_1_id,
        im.user_2_id,
        im.proposed_by,
        im.proposed_meet_time,
        s.name as shop_name
    FROM interest_matches im
    LEFT JOIN coffee_shops s ON im.proposed_shop_id = s.shop_id
    WHERE im.status = 'negotiating'
      AND im.university_id = %s
      AND im.updated_at < NOW() - INTERVAL '6 hours'
      AND im.is_proposal_reminder_sent = FALSE
      AND im.proposed_by IS NOT NULL;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (uni_id,))
                return cur.fetchall()
    except Exception as e:
        print(f"ERROR in get_stale_interest_proposals: {e}")
        return []


def mark_proposal_reminder_sent(match_id: int, uni_id: int) -> bool:
    """Помечает, что напоминание о предложении было отправлено."""
    sql = """
    UPDATE interest_matches
    SET is_proposal_reminder_sent = TRUE
    WHERE match_id = %s AND university_id = %s;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (match_id, uni_id))
                success = cur.rowcount == 1
                conn.commit()
                return success
    except Exception as e:
        print(f"ERROR in mark_proposal_reminder_sent: {e}")
        return False


def get_interest_match_by_id(match_id: int, uni_id: int) -> dict | None:
    """Получает interest_match по ID."""
    sql = """
    SELECT
        im.match_id,
        im.user_1_id,
        im.user_2_id,
        im.similarity_score,
        im.status,
        im.proposed_shop_id,
        im.proposed_meet_time,
        im.proposed_by,
        im.negotiation_round,
        im.coffee_request_id,
        u1.first_name as user_1_name,
        u2.first_name as user_2_name,
        u1.bio as user_1_bio,
        u2.bio as user_2_bio,
        s.name as shop_name
    FROM interest_matches im
    JOIN users u1 ON im.user_1_id = u1.user_id
    JOIN users u2 ON im.user_2_id = u2.user_id
    LEFT JOIN coffee_shops s ON im.proposed_shop_id = s.shop_id
    WHERE im.match_id = %s AND im.university_id = %s;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (match_id, uni_id))
                result = cur.fetchone()
                return dict(result) if result else None
    except Exception as e:
        print(f"ERROR in get_interest_match_by_id: {e}")
        return None


def has_user_bio(user_id: int, uni_id: int) -> bool:
    """Проверяет, заполнено ли у пользователя поле bio."""
    sql = """
    SELECT bio FROM users
    WHERE user_id = %s AND university_id = %s;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, uni_id))
                result = cur.fetchone()
                return bool(result and result[0] and result[0].strip())
    except Exception as e:
        print(f"ERROR in has_user_bio: {e}")
        return False


def main():
    pass


if __name__ == "__main__":
    main()
