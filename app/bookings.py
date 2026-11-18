"""Booking service shared by the AI tools, the guest website and the staff PMS."""
import random
import sqlite3
import string
from datetime import date, datetime, timedelta

from .config import settings
from .db import write_txn
from .pricing import free_rooms, stay_total


class BookingError(Exception):
    pass


ACTIONS = {
    # action: (allowed current statuses, new status)
    "confirm": (("hold",), "confirmed"),
    "cancel": (("hold", "confirmed"), "cancelled"),
    "check-in": (("confirmed", "hold"), "checked_in"),
    "check-out": (("checked_in",), "checked_out"),
}


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def new_code(prefix: str) -> str:
    return prefix + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def expire_holds(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    cur = conn.execute("UPDATE bookings SET status = 'expired', updated_at = ? "
                       "WHERE status = 'hold' AND hold_expires_at <= ?", (_now_iso(now), _now_iso(now)))
    conn.commit()
    return cur.rowcount


def create_booking(conn: sqlite3.Connection, hotel_id: int, *, room_type_id: int, check_in: date, check_out: date,
                   guest_name: str, phone: str, email: str = "", adults: int = 2, children: int = 0,
                   status: str = "hold", channel: str = "ai-chat", note: str = "", room_id: int | None = None,
                   session_id: str | None = None, now: datetime | None = None) -> sqlite3.Row:
    """Atomically pick a free room (or verify the given one) and insert the booking."""
    now = now or datetime.now()
    with write_txn(conn):
        free = free_rooms(conn, hotel_id, room_type_id, check_in, check_out, now)
        if room_id is not None:
            free = [r for r in free if r["id"] == room_id]
            if not free:
                raise BookingError("Phòng đã chọn không trống trong thời gian này.")
        if not free:
            raise BookingError("Loại phòng này đã hết trong thời gian đó.")
        room = free[0]
        total = stay_total(conn, hotel_id, room_type_id, check_in, check_out)
        expires = _now_iso(now + timedelta(minutes=settings.hold_minutes)) if status == "hold" else None
        code = new_code("HD" if status == "hold" else "BK")
        conn.execute(
            "INSERT INTO bookings (code,hotel_id,room_id,guest_name,phone,email,adults,children,check_in,check_out,"
            "status,channel,total_price,note,session_id,hold_expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, hotel_id, room["id"], guest_name, phone, email, adults, children, check_in.isoformat(),
             check_out.isoformat(), status, channel, total, note[:500], session_id, expires))
    return get_booking(conn, hotel_id, code)


def get_booking(conn: sqlite3.Connection, hotel_id: int, code: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT b.*, r.number AS room_number, r.room_type_id, rt.code AS room_type_code, rt.name AS room_type_name "
        "FROM bookings b JOIN rooms r ON r.id = b.room_id JOIN room_types rt ON rt.id = r.room_type_id "
        "WHERE b.hotel_id = ? AND b.code = ?", (hotel_id, code)).fetchone()


def apply_action(conn: sqlite3.Connection, hotel_id: int, code: str, action: str, today: date | None = None) -> sqlite3.Row:
    b = get_booking(conn, hotel_id, code)
    if not b:
        raise BookingError("Không tìm thấy booking.")
    if action not in ACTIONS:
        raise BookingError(f"Thao tác không hợp lệ: {action}")
    allowed, new_status = ACTIONS[action]
    if b["status"] not in allowed:
        raise BookingError(f"Không thể '{action}' booking đang ở trạng thái '{b['status']}'.")
    today = today or date.today()
    if action == "check-in" and date.fromisoformat(b["check_in"]) > today:
        raise BookingError("Chưa tới ngày nhận phòng.")
    now = _now_iso()
    extra = {"check-in": ", checked_in_at = ?", "check-out": ", checked_out_at = ?"}.get(action, "")
    params = [new_status, now] + ([now] if extra else []) + [b["id"]]
    conn.execute(f"UPDATE bookings SET status = ?, hold_expires_at = NULL, updated_at = ?{extra} WHERE id = ?", params)
    if action == "check-out":
        conn.execute("UPDATE rooms SET housekeeping = 'dirty' WHERE id = ?", (b["room_id"],))
    conn.commit()
    return get_booking(conn, hotel_id, code)


def update_booking(conn: sqlite3.Connection, hotel_id: int, code: str, changes: dict) -> sqlite3.Row:
    """Edit guest info, move room and/or change dates (re-checked and re-priced)."""
    b = get_booking(conn, hotel_id, code)
    if not b:
        raise BookingError("Không tìm thấy booking.")
    if b["status"] in ("cancelled", "expired", "checked_out"):
        raise BookingError("Booking đã kết thúc, không sửa được.")
    with write_txn(conn):
        room_id = int(changes.get("room_id") or b["room_id"])
        ci = date.fromisoformat(changes.get("check_in") or b["check_in"])
        co = date.fromisoformat(changes.get("check_out") or b["check_out"])
        if co <= ci:
            raise BookingError("Ngày trả phòng phải sau ngày nhận phòng.")
        moved = room_id != b["room_id"] or ci.isoformat() != b["check_in"] or co.isoformat() != b["check_out"]
        total = b["total_price"]
        if moved:
            room = conn.execute("SELECT * FROM rooms WHERE id = ? AND hotel_id = ?", (room_id, hotel_id)).fetchone()
            if not room:
                raise BookingError("Phòng không tồn tại.")
            if not any(r["id"] == room_id for r in free_rooms(conn, hotel_id, room["room_type_id"], ci, co,
                                                                exclude_booking_id=b["id"])):
                raise BookingError(f"Phòng {room['number']} không trống trong thời gian này.")
            total = stay_total(conn, hotel_id, room["room_type_id"], ci, co)
        fields = {k: changes[k] for k in ("guest_name", "phone", "email", "note", "adults", "children") if k in changes}
        fields.update(room_id=room_id, check_in=ci.isoformat(), check_out=co.isoformat(), total_price=total,
                      updated_at=_now_iso())
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE bookings SET {sets} WHERE id = ?", [*fields.values(), b["id"]])
    return get_booking(conn, hotel_id, code)
