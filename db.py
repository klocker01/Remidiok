import os
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ["DATABASE_URL"]


@contextmanager
def get_cursor(commit=False):
    conn = psycopg2.connect(DATABASE_URL)
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        yield cur
        if commit:
            conn.commit()
    finally:
        conn.close()


def init_db():
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                chat_id BIGINT PRIMARY KEY,
                timezone TEXT NOT NULL DEFAULT 'Europe/Vilnius',
                last_reminder_date DATE
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL REFERENCES users(chat_id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                event_date DATE NOT NULL,
                event_time TIME,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_chat_date ON events(chat_id, event_date)"
        )


def get_or_create_user(chat_id):
    with get_cursor(commit=True) as cur:
        cur.execute("SELECT * FROM users WHERE chat_id = %s", (chat_id,))
        row = cur.fetchone()
        if row:
            return row
        cur.execute(
            "INSERT INTO users (chat_id) VALUES (%s) RETURNING *",
            (chat_id,),
        )
        return cur.fetchone()


def get_user(chat_id):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE chat_id = %s", (chat_id,))
        return cur.fetchone()


def get_all_users():
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users")
        return cur.fetchall()


def set_timezone(chat_id, tz_name):
    with get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE users SET timezone = %s WHERE chat_id = %s",
            (tz_name, chat_id),
        )


def update_last_reminder(chat_id, date_):
    with get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE users SET last_reminder_date = %s WHERE chat_id = %s",
            (date_, chat_id),
        )


def add_event(chat_id, title, event_date, event_time=None):
    with get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO events (chat_id, title, event_date, event_time) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (chat_id, title, event_date, event_time),
        )
        return cur.fetchone()["id"]


def get_events_between(chat_id, start_date, end_date):
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM events WHERE chat_id = %s AND event_date BETWEEN %s AND %s "
            "ORDER BY event_date, event_time NULLS FIRST",
            (chat_id, start_date, end_date),
        )
        return cur.fetchall()


def get_events_from(chat_id, start_date):
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM events WHERE chat_id = %s AND event_date >= %s "
            "ORDER BY event_date, event_time NULLS FIRST",
            (chat_id, start_date),
        )
        return cur.fetchall()


def get_event(chat_id, event_id):
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM events WHERE chat_id = %s AND id = %s",
            (chat_id, event_id),
        )
        return cur.fetchone()


def delete_event(chat_id, event_id):
    with get_cursor(commit=True) as cur:
        cur.execute(
            "DELETE FROM events WHERE chat_id = %s AND id = %s",
            (chat_id, event_id),
        )
        return cur.rowcount > 0
