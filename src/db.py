import os
import psycopg2
import psycopg2.extras
from psycopg2 import pool
from datetime import datetime, timezone
from contextlib import contextmanager
from dotenv import load_dotenv
import json

load_dotenv()

DB_POOL = None


def init_db_pool():
    global DB_POOL
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
    except Exception as e:
        print(f"Error creating connection pool: {e}")
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
        u.coffee_streak
    FROM
        coffee_requests AS r
    JOIN
        coffee_shops AS s ON r.shop_id = s.shop_id
    JOIN
        users AS u ON r.creator_user_id = u.user_id
    WHERE
        r.status = 'pending'
        AND r.creator_user_id != %s
        AND r.meet_time > NOW()
        AND r.university_id = %s
    ORDER BY
        r.meet_time ASC;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, uni_id))
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


def get_request_details(request_id: int) -> dict:
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
        year_as_student,
        coffee_streak
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
                        "coffee_streak": result[4] if result[4] else 0,
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
    success = False
    sql = """
    UPDATE coffee_requests
    SET 
        partner_user_id = %s,
        status = 'matched',
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
                            "UPDATE users SET coffee_streak = 0 WHERE user_id = %s",
                            (creator_user_id,),
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
                            "UPDATE users SET coffee_streak = 0 WHERE user_id = %s",
                            (partner_user_id,),
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


def save_verification_code(request_id: int, code: str):
    sql = "UPDATE coffee_requests SET verification_code = %s WHERE request_id = %s;"
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (code, request_id))
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


def is_user_active(user_id: int, uni_id=None) -> bool:
    if uni_id:
        sql = "SELECT is_active FROM users WHERE user_id = %s AND university_id = %s;"
        params = (user_id, uni_id)
    else:
        sql = "SELECT is_active FROM users WHERE user_id = %s;"
        params = (user_id,)

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                result = cur.fetchone()
                if result:
                    return result[0]
                else:
                    return True
    except Exception as e:
        print(f"ERROR in is_user_active: {e}")
        return True


def main():
    pass


if __name__ == "__main__":
    main()
