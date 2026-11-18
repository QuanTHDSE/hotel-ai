"""Chat sessions and messages (shared by the AI agent, the guest widget and the staff inbox)."""
import json
import sqlite3


def ensure_session(conn: sqlite3.Connection, hotel_id: int, session_id: str,
                   channel: str = "web", guest_name: str | None = None) -> sqlite3.Row:
    conn.execute("INSERT OR IGNORE INTO chat_sessions (hotel_id, session_id, channel, guest_name) VALUES (?,?,?,?)",
                 (hotel_id, session_id, channel, guest_name))
    if guest_name:
        conn.execute("UPDATE chat_sessions SET guest_name = ? WHERE hotel_id = ? AND session_id = ?",
                     (guest_name, hotel_id, session_id))
    conn.commit()
    return get_session(conn, hotel_id, session_id)


def get_session(conn: sqlite3.Connection, hotel_id: int, session_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM chat_sessions WHERE hotel_id = ? AND session_id = ?",
                        (hotel_id, session_id)).fetchone()


def set_mode(conn: sqlite3.Connection, hotel_id: int, session_id: str, mode: str) -> None:
    conn.execute("UPDATE chat_sessions SET mode = ? WHERE hotel_id = ? AND session_id = ?", (mode, hotel_id, session_id))
    if mode == "ai":
        conn.execute("UPDATE handoffs SET status = 'done' WHERE hotel_id = ? AND session_id = ? AND status = 'open'",
                     (hotel_id, session_id))
    conn.commit()


def add_message(conn: sqlite3.Connection, hotel_id: int, session_id: str, role: str, content: str,
                trace: list | None = None, latency_s: float | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO messages (hotel_id, session_id, role, content, trace, latency_s) VALUES (?,?,?,?,?,?)",
        (hotel_id, session_id, role, content, json.dumps(trace, ensure_ascii=False, default=str) if trace else None,
         latency_s))
    conn.execute("UPDATE chat_sessions SET last_message_at = datetime('now','localtime') "
                 "WHERE hotel_id = ? AND session_id = ?", (hotel_id, session_id))
    conn.commit()
    return cur.lastrowid


def list_messages(conn: sqlite3.Connection, hotel_id: int, session_id: str, after_id: int = 0,
                  with_trace: bool = False) -> list[dict]:
    rows = conn.execute("SELECT * FROM messages WHERE hotel_id = ? AND session_id = ? AND id > ? ORDER BY id",
                        (hotel_id, session_id, after_id)).fetchall()
    out = []
    for r in rows:
        m = {"id": r["id"], "role": r["role"], "content": r["content"], "created_at": r["created_at"]}
        if with_trace:
            m["trace"] = json.loads(r["trace"]) if r["trace"] else []
            m["latency_s"] = r["latency_s"]
        out.append(m)
    return out
