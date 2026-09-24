import datetime
import os
import sqlite3
from contextlib import contextmanager

DATABASE_URL = os.environ.get("DATABASE_URL")
BACKEND = "postgres" if DATABASE_URL else "sqlite"
SQLITE_PATH = os.environ.get("SQLITE_PATH", "remidionak.local.db")

if BACKEND == "postgres":
    import psycopg2
    from psycopg2.extras import RealDictCursor
else:
    sqlite3.register_adapter(datetime.date, lambda d: d.isoformat())
    sqlite3.register_adapter(datetime.time, lambda t: t.isoformat())
    sqlite3.register_converter("date", lambda v: datetime.date.fromisoformat(v.decode()))
    sqlite3.register_converter("time", lambda v: datetime.time.fromisoformat(v.decode()))


class SQLiteCursor:
    """Wraps a sqlite3 cursor so it accepts psycopg2-style %s placeholders
    and returns plain dicts, like RealDictCursor does."""

    def __init__(self, cur):
        self._cur = cur

    def execute(self, query, params=()):
        self._cur.execute(query.replace("%s", "?"), params)
        return self

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        return [dict(row) for row in self._cur.fetchall()]

    @property
    def rowcount(self):
        return self._cur.rowcount


@contextmanager
def get_cursor(commit=False):
    if BACKEND == "postgres":
        conn = psycopg2.connect(DATABASE_URL)
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            yield cur
            if commit:
                conn.commit()
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(SQLITE_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            cur = SQLiteCursor(conn.cursor())
            yield cur
            if commit:
                conn.commit()
        finally:
            conn.close()


def init_db():
    with get_cursor(commit=True) as cur:
        if BACKEND == "postgres":
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    chat_id BIGINT PRIMARY KEY,
                    timezone TEXT NOT NULL DEFAULT 'Europe/Vilnius',
                    last_reminder_date DATE,
                    reminder_time TIME NOT NULL DEFAULT '22:00'
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER NOT NULL,
                    chat_id BIGINT NOT NULL REFERENCES users(chat_id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    event_date DATE NOT NULL,
                    event_time TIME,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (chat_id, id)
                )
                """
            )
        else:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    chat_id INTEGER PRIMARY KEY,
                    timezone TEXT NOT NULL DEFAULT 'Europe/Vilnius',
                    last_reminder_date DATE,
                    reminder_time TIME NOT NULL DEFAULT '22:00'
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL REFERENCES users(chat_id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    event_date DATE NOT NULL,
                    event_time TIME,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (chat_id, id)
                )
                """
            )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_chat_date ON events(chat_id, event_date)"
        )
        # Migration for databases created before reminder_time existed.
        if BACKEND == "postgres":
            cur.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "reminder_time TIME NOT NULL DEFAULT '22:00'"
            )
        else:
            cur.execute("PRAGMA table_info(users)")
            if "reminder_time" not in {row["name"] for row in cur.fetchall()}:
                cur.execute(
                    "ALTER TABLE users ADD COLUMN reminder_time TIME NOT NULL DEFAULT '22:00'"
                )
        _migrate_events_pk(cur)


def _migrate_events_pk(cur):
    """Databases created by the first version have events.id as a global
    SERIAL/AUTOINCREMENT primary key, but ids are now numbered per chat, so
    two chats both having event #1 violates it. Switch to (chat_id, id)."""
    if BACKEND == "postgres":
        cur.execute(
            "SELECT c.conname, array_agg(a.attname::text) AS cols "
            "FROM pg_constraint c JOIN pg_attribute a "
            "ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
            "WHERE c.conrelid = 'events'::regclass AND c.contype = 'p' "
            "GROUP BY c.conname"
        )
        row = cur.fetchone()
        if not row or row["cols"] != ["id"]:
            return
        cur.execute(f'ALTER TABLE events DROP CONSTRAINT "{row["conname"]}"')
        cur.execute("ALTER TABLE events ALTER COLUMN id DROP DEFAULT")
        cur.execute("ALTER TABLE events ADD PRIMARY KEY (chat_id, id)")
    else:
        cur.execute("PRAGMA table_info(events)")
        if [r["name"] for r in cur.fetchall() if r["pk"]] != ["id"]:
            return
        cur.execute("ALTER TABLE events RENAME TO events_old")
        cur.execute(
            """
            CREATE TABLE events (
                id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL REFERENCES users(chat_id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                event_date DATE NOT NULL,
                event_time TIME,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, id)
            )
            """
        )
        cur.execute(
            "INSERT INTO events (id, chat_id, title, event_date, event_time, created_at) "
            "SELECT id, chat_id, title, event_date, event_time, created_at FROM events_old"
        )
        cur.execute("DROP TABLE events_old")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_chat_date ON events(chat_id, event_date)"
        )
    # Old ids were global, so each chat's run has gaps; renumber to 1..N.
    cur.execute("SELECT DISTINCT chat_id FROM events")
    for row in cur.fetchall():
        _compact_ids(cur, row["chat_id"])


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


def set_reminder_time(chat_id, time_, last_reminder_date):
    with get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE users SET reminder_time = %s, last_reminder_date = %s WHERE chat_id = %s",
            (time_, last_reminder_date, chat_id),
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
            "SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM events WHERE chat_id = %s",
            (chat_id,),
        )
        next_id = cur.fetchone()["next_id"]
        cur.execute(
            "INSERT INTO events (id, chat_id, title, event_date, event_time) "
            "VALUES (%s, %s, %s, %s, %s)",
            (next_id, chat_id, title, event_date, event_time),
        )
        return next_id


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


def _compact_ids(cur, chat_id):
    """Renumbers this chat's events to a gapless 1..N run, in their existing
    id order. Processing lowest-id-first guarantees each new id is already
    free (it was vacated by the previous row, or never used), so no id ever
    collides with one that hasn't been reassigned yet."""
    cur.execute("SELECT id FROM events WHERE chat_id = %s ORDER BY id", (chat_id,))
    for new_id, row in enumerate(cur.fetchall(), start=1):
        old_id = row["id"]
        if old_id != new_id:
            cur.execute(
                "UPDATE events SET id = %s WHERE chat_id = %s AND id = %s",
                (new_id, chat_id, old_id),
            )


def delete_event(chat_id, event_id):
    with get_cursor(commit=True) as cur:
        cur.execute(
            "DELETE FROM events WHERE chat_id = %s AND id = %s",
            (chat_id, event_id),
        )
        deleted = cur.rowcount > 0
        if deleted:
            _compact_ids(cur, chat_id)
        return deleted


def delete_past_events(chat_id, today):
    """Deletes events before `today` for this chat and compacts the
    remaining ids. Returns how many were deleted."""
    with get_cursor(commit=True) as cur:
        cur.execute(
            "DELETE FROM events WHERE chat_id = %s AND event_date < %s",
            (chat_id, today),
        )
        deleted = cur.rowcount
        if deleted:
            _compact_ids(cur, chat_id)
        return deleted
