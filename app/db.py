"""SQLite schema. Every business table carries hotel_id (multi-tenant)."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .config import settings

# Booking statuses:
#   hold        – temporary, created by AI chat or website, expires at hold_expires_at
#   confirmed   – confirmed by staff (or created by staff)
#   checked_in  – guest is in house
#   checked_out – stay finished
#   cancelled / expired
BLOCKING_STATUSES = ("confirmed", "checked_in")  # + holds that have not expired

SCHEMA = """
CREATE TABLE IF NOT EXISTS hotels (
    id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    stars INTEGER,
    address TEXT,
    city TEXT,
    phone TEXT,
    email TEXT,
    checkin_time TEXT,
    checkout_time TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS room_types (
    id INTEGER PRIMARY KEY,
    hotel_id INTEGER NOT NULL REFERENCES hotels(id),
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    bed TEXT,
    max_adults INTEGER NOT NULL,
    max_children INTEGER NOT NULL DEFAULT 0,
    size_m2 INTEGER,
    view TEXT,
    amenities TEXT,              -- JSON list
    base_price INTEGER NOT NULL, -- VND per night
    breakfast_included INTEGER NOT NULL DEFAULT 0,
    UNIQUE (hotel_id, code)
);

CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY,
    hotel_id INTEGER NOT NULL REFERENCES hotels(id),
    room_type_id INTEGER NOT NULL REFERENCES room_types(id),
    number TEXT NOT NULL,
    floor INTEGER,
    status TEXT NOT NULL DEFAULT 'active',        -- active | maintenance
    housekeeping TEXT NOT NULL DEFAULT 'clean',   -- clean | dirty
    UNIQUE (hotel_id, number)
);

-- kind = 'season' (date range) or 'weekday' (nights of week, 0=Mon..6=Sun)
-- nightly price = base_price * best season multiplier * weekday multiplier
CREATE TABLE IF NOT EXISTS rate_rules (
    id INTEGER PRIMARY KEY,
    hotel_id INTEGER NOT NULL REFERENCES hotels(id),
    room_type_id INTEGER REFERENCES room_types(id), -- NULL = all room types
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,      -- inclusive
    weekdays TEXT,      -- JSON list for kind='weekday'
    multiplier REAL NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    hotel_id INTEGER NOT NULL REFERENCES hotels(id),
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    guest_name TEXT,
    phone TEXT,
    email TEXT,
    adults INTEGER,
    children INTEGER DEFAULT 0,
    check_in TEXT NOT NULL,
    check_out TEXT NOT NULL,   -- exclusive
    status TEXT NOT NULL,
    channel TEXT,              -- walk-in | phone | ota | website | ai-chat | zalo
    total_price INTEGER,
    note TEXT,
    session_id TEXT,           -- chat session that created it (AI bookings)
    hold_expires_at TEXT,
    checked_in_at TEXT,
    checked_out_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_bookings_room_dates ON bookings(room_id, check_in, check_out);
CREATE INDEX IF NOT EXISTS idx_bookings_hotel_status ON bookings(hotel_id, status);

CREATE TABLE IF NOT EXISTS policies (
    id INTEGER PRIMARY KEY,
    hotel_id INTEGER NOT NULL REFERENCES hotels(id),
    category TEXT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    keywords TEXT
);

-- One row per chat conversation. mode='ai': AI answers; mode='staff': a human took over.
CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY,
    hotel_id INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'web',
    guest_name TEXT,
    mode TEXT NOT NULL DEFAULT 'ai',
    context TEXT,              -- JSON: last successful availability search (dates, guests, room type)
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_message_at TEXT,
    UNIQUE (hotel_id, session_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    hotel_id INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,        -- user | assistant | staff
    content TEXT NOT NULL,
    trace TEXT,                -- JSON: tool calls the AI made for this answer
    latency_s REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(hotel_id, session_id, id);

CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY,
    hotel_id INTEGER NOT NULL,
    session_id TEXT,
    reason TEXT,
    summary TEXT,
    status TEXT NOT NULL DEFAULT 'open',  -- open | done
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS staff (
    id INTEGER PRIMARY KEY,
    hotel_id INTEGER NOT NULL REFERENCES hotels(id),
    username TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL,          -- manager | receptionist
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS staff_tokens (
    token TEXT PRIMARY KEY,
    staff_id INTEGER NOT NULL REFERENCES staff(id),
    expires_at TEXT NOT NULL
);
"""


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or settings.db_path
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def write_txn(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE: takes the write lock before the availability check,
    so two guests can't book the last room at the same moment."""
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def get_hotel(conn: sqlite3.Connection, slug: str):
    return conn.execute("SELECT * FROM hotels WHERE slug = ?", (slug,)).fetchone()
