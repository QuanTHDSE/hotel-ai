"""Price and availability logic. Pure DB queries — no AI here."""
import json
import sqlite3
from datetime import date, datetime, timedelta


def _round_thousand(value: float) -> int:
    return int(round(value / 1000.0)) * 1000


def nightly_price(conn: sqlite3.Connection, hotel_id: int, room_type_id: int, night: date) -> int:
    """Price for the night starting on `night`."""
    base = conn.execute("SELECT base_price FROM room_types WHERE id = ? AND hotel_id = ?",
                        (room_type_id, hotel_id)).fetchone()["base_price"]
    iso = night.isoformat()
    season = conn.execute(
        "SELECT multiplier FROM rate_rules WHERE hotel_id = ? AND kind = 'season' "
        "AND (room_type_id IS NULL OR room_type_id = ?) AND start_date <= ? AND end_date >= ? "
        "ORDER BY priority DESC LIMIT 1", (hotel_id, room_type_id, iso, iso)).fetchone()
    mult = season["multiplier"] if season else 1.0
    for rule in conn.execute(
            "SELECT weekdays, multiplier FROM rate_rules WHERE hotel_id = ? AND kind = 'weekday' "
            "AND (room_type_id IS NULL OR room_type_id = ?)", (hotel_id, room_type_id)):
        if night.weekday() in json.loads(rule["weekdays"] or "[]"):
            mult *= rule["multiplier"]
    return _round_thousand(base * mult)


def stay_total(conn: sqlite3.Connection, hotel_id: int, room_type_id: int, check_in: date, check_out: date) -> int:
    return sum(nightly_price(conn, hotel_id, room_type_id, n) for n in stay_nights(check_in, check_out))


def _now_iso(now: datetime | None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


# A booking blocks a room if confirmed / in house, or a hold that has not expired yet.
BLOCKING_SQL = "(b.status IN ('confirmed','checked_in') OR (b.status = 'hold' AND b.hold_expires_at > ?))"


def free_rooms(conn: sqlite3.Connection, hotel_id: int, room_type_id: int | None,
               check_in: date, check_out: date, now: datetime | None = None,
               exclude_booking_id: int | None = None) -> list[sqlite3.Row]:
    """Active rooms (optionally of one type) with no overlapping blocking booking."""
    sql = f"""
        SELECT r.*, rt.code AS type_code, rt.name AS type_name FROM rooms r
        JOIN room_types rt ON rt.id = r.room_type_id
        WHERE r.hotel_id = ? AND r.status = 'active' {"AND r.room_type_id = ?" if room_type_id else ""}
          AND NOT EXISTS (
            SELECT 1 FROM bookings b
            WHERE b.room_id = r.id AND b.id IS NOT ?
              AND b.check_in < ? AND b.check_out > ?
              AND {BLOCKING_SQL}
          )
        ORDER BY r.number
    """
    params: list = [hotel_id] + ([room_type_id] if room_type_id else [])
    params += [exclude_booking_id, check_out.isoformat(), check_in.isoformat(), _now_iso(now)]
    return conn.execute(sql, params).fetchall()


def format_vnd(amount: int) -> str:
    return f"{amount:,.0f}".replace(",", ".") + "đ"


def stay_nights(check_in: date, check_out: date) -> list[date]:
    return [check_in + timedelta(days=i) for i in range((check_out - check_in).days)]
