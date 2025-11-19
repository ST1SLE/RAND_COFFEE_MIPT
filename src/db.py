import os
import psycopg2
import psycopg2.extras
from datetime import datetime, timezone
from contextlib import contextmanager
from dotenv import load_dotenv
import json

load_dotenv()


@contextmanager
def get_db_connection():
    conn = None
    try:
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST"),
            database=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"),
            port=os.getenv("DB_PORT"),
        )
        yield conn
    except psycopg2.OperationalError as e:
        print(f"Error connecting to database: {e}")
        raise
    finally:
        if conn:
            conn.close()


def add_or_update_user(user_id: int, username: str, first_name: str):
    sql = """
    INSERT into users (user_id, username, first_name, is_active, created_at, last_seen)
    VALUES (%s,%s,%s,TRUE,%s,%s)
    ON CONFLICT (user_id) DO UPDATE SET
        username = EXCLUDED.username,
        first_name = EXCLUDED.first_name,
        last_seen = EXCLUDED.last_seen;
    """

    now = datetime.now()

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, username, first_name, now, now))
                conn.commit()
    except Exception as e:
        print(f"DB ERROR in add_or_update_user: {e}")


def add_coffee_shop(shop_id: int, name: str, description: str, working_hours: str):
    sql = """
    INSERT into coffee_shops (shop_id, name, description, working_hours, is_active)
    VALUES (%s, %s, %s, %s, TRUE)
    on CONFLICT (shop_id) DO UPDATE SET
        name = EXCLUDED.name,
        description = EXCLUDED.description,
        working_hours = EXCLUDED.working_hours,
        is_active = TRUE;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (shop_id, name, description, working_hours))
                conn.commit()
    except Exception as e:
        print(f"DB ERROR in add_coffee_shop: {e}")


def get_active_coffee_shops() -> list:
    sql = "SELECT shop_id, name FROM coffee_shops WHERE is_active = TRUE ORDER BY name;"

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                return cur.fetchall()
    except Exception as e:
        print(f"error in get_active_coffee_shops(): {e}")
        return []


def get_shop_details(shop_id: int) -> dict:
    sql = "SELECT name, description FROM coffee_shops WHERE shop_id = %s;"
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (shop_id,))
                result = cur.fetchone()
                return result if result else {}
    except Exception as e:
        print(f"error in get_shop_details(): {e}")
        return {}


def get_shop_working_hours(shop_id: int) -> dict:
    sql = "SELECT working_hours FROM coffee_shops WHERE shop_id = %s;"
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (shop_id,))
                result = cur.fetchone()
                if result:
                    return result["working_hours"]
                return {}
    except Exception as e:
        print(f"error in get_shop_working_hours(): {e}")
        return {}


def create_coffee_request(creator_user_id: int, shop_id: int, meet_time: datetime):
    sql = """INSERT INTO coffee_requests (
        creator_user_id,
        shop_id,
        meet_time,
        status,
        created_at,
        is_reminder_sent,
        is_failure_notification_sent
    )
    VALUES (%s, %s, %s, 'pending', %s, FALSE, FALSE)
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                now_utc = datetime.now(timezone.utc)
                cur.execute(sql, (creator_user_id, shop_id, meet_time, now_utc))
                conn.commit()
                print(
                    f"SUCCESS in creating coffee request for user with id: {creator_user_id}"
                )
    except Exception as e:
        print(f"error in create_coffee_request(): {e}")


def get_pending_requests(user_id: int) -> list:
    sql = """
    SELECT
        r.request_id,
        s.name,
        r.meet_time
    FROM
        coffee_requests AS r
    JOIN
        coffee_shops AS s ON r.shop_id = s.shop_id
    WHERE
        r.status = 'pending'
        AND r.creator_user_id != %s
        AND r.meet_time > NOW()
    ORDER BY
        r.meet_time ASC;
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id,))
                return cur.fetchall()
    except Exception as e:
        print(f"ERROR in get_pending_requests(): {e}")
        return []


def get_request_details(request_id: int) -> dict:
    sql = """
    SELECT
        r.creator_user_id,
        r.partner_user_id,
        s.name as shop_name,
        r.meet_time
    FROM
        coffee_requests as r
    JOIN
        coffee_shops as s ON r.shop_id = s.shop_id
    WHERE
        r.request_id = %s;
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (request_id,))
                result = cur.fetchone()
                return result if result else {}
    except Exception as e:
        print(f"ERROR in get_request_details(): {e}")
        return {}


def get_user_details(user_id: int) -> dict:
    sql = """
    SELECT
        username,
        first_name,
        phystech_school,
        year_as_student
    FROM
        users
    WHERE
        user_id = %s;
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id,))
                result = cur.fetchone()
                if result:
                    return {
                        "username": result[0],
                        "first_name": result[1],
                        "phystech_school": result[2],
                        "year_as_student": result[3],
                    }
    except Exception as e:
        print(f"ERROR in get_user_details(): {e}")
        return {}


def update_user_profile(user_id: int, school: str, year: int | None):
    sql = """
    UPDATE users
    SET
        phystech_school = %s,
        year_as_student = %s
    WHERE
        user_id = %s;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (school, year, user_id))
                conn.commit()
    except Exception as e:
        print(f"ERROR in update_user_profile(): {e}")


def get_user_requests(user_id: int) -> list:
    sql = """
    SELECT
        r.request_id,
        r.status,
        r.meet_time,
        s.name as shop_name,
        r.creator_user_id,
        creator.username as creator_username,
        r.partner_user_id,
        partner.username as partner_username
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
        AND (
            -- 1. Показываем все будущие встречи (pending и matched)
            (r.status IN ('pending', 'matched') AND r.meet_time > NOW())
            OR
            -- 2. Показываем завершенные встречи (matched) за последние 2 дня
            (r.status = 'matched' AND r.meet_time BETWEEN NOW() - INTERVAL '2 days' AND NOW())
            OR
            -- 3. Показываем отмененные встречи (cancelled) за последний час
            (r.status = 'cancelled' AND r.created_at > NOW() - INTERVAL '1 hour')
        )
    ORDER BY
        r.meet_time DESC; -- Изменено на DESC для показа самых свежих встреч первыми
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (user_id, user_id))
                return cur.fetchall()
    except Exception as e:
        print(f"ERROR in get_user_requests(): {e}")
        return []


def pair_user_for_request(request_id: int, partner_user_id: int) -> bool:
    success = False
    sql = """
    UPDATE coffee_requests
    SET 
        partner_user_id = %s,
        status = 'matched'
    WHERE
        request_id = %s 
        AND status = 'pending' 
        AND partner_user_id IS NULL; -- Добавлено ключевое условие!
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql,
                    (
                        partner_user_id,
                        request_id,
                    ),
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


def cancel_request(request_id: int, user_id: int) -> bool:
    success = False
    sql = """
    UPDATE
        coffee_requests
    SET
        status = 'cancelled'
    WHERE
        request_id = %s
        AND creator_user_id = %s
        AND status = 'pending';
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (request_id, user_id))
                if cur.rowcount == 1:
                    conn.commit()
                    success = True
                else:
                    conn.rollback()
    except Exception as e:
        print(f"DB ERROR in cancel_request(): {e}")
        success = False

    return success


def cancel_request_by_creator(request_id: int, creator_user_id: int) -> int | None:
    sql = """
    UPDATE
        coffee_requests
    SET
        status = 'cancelled'
    WHERE
        request_id = %s
        AND creator_user_id = %s
        AND status = 'matched'
    RETURNING
        partner_user_id;
    """

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (request_id, creator_user_id))
                if cur.rowcount == 1:
                    conn.commit()
                    return cur.fetchone()[0]
                else:
                    conn.rollback()
                    return None
    except Exception as e:
        print(f"DB ERROR in cancel_request_by_creator(): {e}")
        return None


def unmatch_request(request_id: int, partner_user_id: int) -> int | None:
    sql = """
    UPDATE
        coffee_requests
    SET
        status = 'pending',
        partner_user_id = NULL  -- Эта строка критически важна!
    WHERE
        request_id = %s
        AND partner_user_id = %s
        AND status = 'matched'
    RETURNING
        creator_user_id;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (request_id, partner_user_id))
                if cur.rowcount == 1:
                    conn.commit()
                    return cur.fetchone()[0]
                else:
                    conn.rollback()
                    return None
    except Exception as e:
        print(f"DB ERROR in unmatch_request(): {e}")
        return None


def get_meetings_for_reminder() -> list:
    sql = """
    UPDATE coffee_requests
    SET is_reminder_sent = TRUE
    WHERE request_id IN (
        SELECT request_id
        FROM coffee_requests
        WHERE
            status = 'matched'
            AND is_reminder_sent = FALSE
            AND meet_time BETWEEN NOW() AND NOW() + INTERVAL '20 minutes'
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
                cur.execute(sql)
                meetings = cur.fetchall()
                conn.commit()
    except Exception as e:
        print(f"ERROR in get_meetings_for_reminder(): {e}")

    return meetings


def mark_reminder_as_sent(request_id: int) -> bool:
    sql = """
    UPDATE
        coffee_requests
    SET
        is_reminder_sent = TRUE
    WHERE
        request_id = %s;
    """
    success = False

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (request_id,))
                if cur.rowcount == 1:
                    conn.commit()
                    success = True
    except Exception as e:
        print(f"ERROR in mark_reminder_as_sent(): {e}")

    return success


def expire_pending_requests() -> list:
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
        AND r.meet_time < (NOW() + INTERVAL '5 minutes')
        AND r.is_failure_notification_sent = FALSE
    RETURNING
        r.request_id, r.creator_user_id, s.name as shop_name, r.meet_time;
    """

    expired_requests = []

    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql)
                conn.commit()
                expired_requests = cur.fetchall()
    except Exception as e:
        print(f"ERROR in expire_pending_requests(): {e}")

    return expired_requests


def get_meetings_for_feedback() -> list:
    sql = """
    SELECT
        r.request_id, r.creator_user_id, r.partner_user_id, s.name as shop_name, r.meet_time
    FROM coffee_requests r
    JOIN coffee_shops s ON r.shop_id = s.shop_id
    WHERE
        r.status = 'matched'
        AND r.is_feedback_requested = FALSE
        AND r.meet_time < (NOW() - INTERVAL '30 minutes');
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql)
                return cur.fetchall()
    except Exception as e:
        print(f"ERROR in get_meetings_for_feedback(): {e}")
        return []


def mark_feedback_as_requested(request_id: int) -> bool:
    sql = (
        "UPDATE coffee_requests SET is_feedback_requested = TRUE WHERE request_id = %s;"
    )
    success = False
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (request_id,))
                if cur.rowcount == 1:
                    conn.commit()
                    success = True
    except Exception as e:
        print(f"ERROR in mark_feedback_as_requested(): {e}")
    return success


def save_meeting_outcome(request_id: int, outcome: str) -> bool:
    sql = "UPDATE coffee_requests SET meeting_outcome = %s WHERE request_id = %s;"
    success = False
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (outcome, request_id))
                conn.commit()
                success = True
    except Exception as e:
        print(f"ERROR in save_meeting_outcome(): {e}")
    return success


def main():
    pass


if __name__ == "__main__":
    main()
