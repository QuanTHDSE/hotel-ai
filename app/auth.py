"""Staff accounts: PBKDF2 password hashes + opaque bearer tokens stored in the DB."""
import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta

TOKEN_HOURS = 12
_ITERATIONS = 100_000


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS).hex()
    return digest, salt


def verify_password(password: str, digest: str, salt: str) -> bool:
    return hmac.compare_digest(hash_password(password, salt)[0], digest)


def create_staff(conn: sqlite3.Connection, hotel_id: int, username: str, name: str, role: str, password: str) -> int:
    digest, salt = hash_password(password)
    cur = conn.execute("INSERT INTO staff (hotel_id, username, name, role, password_hash, salt) VALUES (?,?,?,?,?,?)",
                       (hotel_id, username, name, role, digest, salt))
    return cur.lastrowid


def login(conn: sqlite3.Connection, username: str, password: str) -> str | None:
    row = conn.execute("SELECT * FROM staff WHERE username = ?", (username.strip().lower(),)).fetchone()
    if not row or not verify_password(password, row["password_hash"], row["salt"]):
        return None
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=TOKEN_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("DELETE FROM staff_tokens WHERE expires_at < datetime('now','localtime')")
    conn.execute("INSERT INTO staff_tokens (token, staff_id, expires_at) VALUES (?,?,?)", (token, row["id"], expires))
    conn.commit()
    return token


def staff_for_token(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT s.id, s.hotel_id, s.username, s.name, s.role, h.slug AS hotel_slug, h.name AS hotel_name "
        "FROM staff_tokens t JOIN staff s ON s.id = t.staff_id JOIN hotels h ON h.id = s.hotel_id "
        "WHERE t.token = ? AND t.expires_at > datetime('now','localtime')", (token,)).fetchone()


def logout(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM staff_tokens WHERE token = ?", (token,))
    conn.commit()
