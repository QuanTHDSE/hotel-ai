"""Staff (PMS) API. Every query is scoped to the logged-in staff member's hotel."""
import json
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from . import auth
from .bookings import BookingError, apply_action, create_booking, expire_holds, get_booking, update_booking
from .deps import current_staff, get_conn, manager_only
from .pricing import BLOCKING_SQL, free_rooms, nightly_price
from .sessions import add_message, get_session, list_messages, set_mode

router = APIRouter(prefix="/api")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur]


# ------------------------------------------------------------------ auth

class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn, conn=Depends(get_conn)):
    token = auth.login(conn, body.username, body.password)
    if not token:
        raise HTTPException(401, "Sai tên đăng nhập hoặc mật khẩu")
    return {"token": token, "staff": dict(auth.staff_for_token(conn, token))}


@router.get("/auth/me")
def me(staff=Depends(current_staff)):
    return staff


@router.post("/auth/logout")
def logout(authorization: str | None = Header(default=None), conn=Depends(get_conn)):
    if authorization:
        auth.logout(conn, authorization.split(" ", 1)[-1])
    return {"ok": True}


# ------------------------------------------------------------------ dashboard

@router.get("/staff/dashboard")
def dashboard(staff=Depends(current_staff), conn=Depends(get_conn)):
    hid = staff["hotel_id"]
    expire_holds(conn)
    today = date.today().isoformat()
    now = _now()
    total_rooms = conn.execute("SELECT COUNT(*) FROM rooms WHERE hotel_id = ? AND status = 'active'", (hid,)).fetchone()[0]

    def occupied(d: str) -> int:
        return conn.execute(f"SELECT COUNT(DISTINCT b.room_id) FROM bookings b WHERE b.hotel_id = ? "
                            f"AND b.check_in <= ? AND b.check_out > ? AND {BLOCKING_SQL}",
                            (hid, d, d, now)).fetchone()[0]

    base = ("SELECT b.code, b.guest_name, b.phone, b.status, b.channel, b.check_in, b.check_out, b.total_price, "
            "b.hold_expires_at, r.number AS room_number, rt.name AS room_type_name FROM bookings b "
            "JOIN rooms r ON r.id = b.room_id JOIN room_types rt ON rt.id = r.room_type_id WHERE b.hotel_id = ? ")
    arrivals = _rows(conn.execute(base + "AND b.check_in = ? AND b.status IN ('confirmed','hold') ORDER BY r.number",
                                  (hid, today)))
    departures = _rows(conn.execute(base + "AND b.check_out = ? AND b.status = 'checked_in' ORDER BY r.number",
                                    (hid, today)))
    holds = _rows(conn.execute(base + "AND b.status = 'hold' ORDER BY b.created_at DESC", (hid,)))
    in_house = conn.execute("SELECT COUNT(*) FROM bookings WHERE hotel_id = ? AND status = 'checked_in'", (hid,)).fetchone()[0]
    dirty = conn.execute("SELECT COUNT(*) FROM rooms WHERE hotel_id = ? AND housekeeping = 'dirty'", (hid,)).fetchone()[0]

    week_ago = (date.today() - timedelta(days=7)).isoformat()
    ai = {
        "conversations_7d": conn.execute("SELECT COUNT(*) FROM chat_sessions WHERE hotel_id = ? AND created_at >= ?",
                                         (hid, week_ago)).fetchone()[0],
        "ai_replies_7d": conn.execute("SELECT COUNT(*) FROM messages WHERE hotel_id = ? AND role = 'assistant' "
                                      "AND created_at >= ?", (hid, week_ago)).fetchone()[0],
        "avg_latency_s": conn.execute("SELECT ROUND(AVG(latency_s), 1) FROM messages WHERE hotel_id = ? "
                                      "AND role = 'assistant' AND created_at >= ?", (hid, week_ago)).fetchone()[0],
        "ai_bookings_7d": conn.execute("SELECT COUNT(*) FROM bookings WHERE hotel_id = ? AND channel = 'ai-chat' "
                                       "AND created_at >= ?", (hid, week_ago)).fetchone()[0],
        "ai_confirmed_revenue_7d": conn.execute(
            "SELECT COALESCE(SUM(total_price), 0) FROM bookings WHERE hotel_id = ? AND channel = 'ai-chat' "
            "AND status IN ('confirmed','checked_in','checked_out') AND created_at >= ?", (hid, week_ago)).fetchone()[0],
        "waiting_staff": conn.execute("SELECT COUNT(*) FROM chat_sessions WHERE hotel_id = ? AND mode = 'staff'",
                                      (hid,)).fetchone()[0],
    }
    occupancy = []
    for i in range(14):
        d = (date.today() + timedelta(days=i)).isoformat()
        occupancy.append({"date": d, "booked": occupied(d), "total": total_rooms})
    return {"today": today, "total_rooms": total_rooms, "occupied_tonight": occupied(today), "in_house": in_house,
            "dirty_rooms": dirty, "arrivals": arrivals, "departures": departures, "holds": holds,
            "ai": ai, "occupancy": occupancy}


# ------------------------------------------------------------------ calendar (tape chart)

@router.get("/staff/calendar")
def calendar(start: date | None = None, days: int = 14, staff=Depends(current_staff), conn=Depends(get_conn)):
    hid = staff["hotel_id"]
    expire_holds(conn)
    start = start or date.today()
    days = max(1, min(days, 31))
    end = start + timedelta(days=days)
    rooms = _rows(conn.execute(
        "SELECT r.id, r.number, r.floor, r.status, r.housekeeping, rt.code AS type_code, rt.name AS type_name "
        "FROM rooms r JOIN room_types rt ON rt.id = r.room_type_id WHERE r.hotel_id = ? ORDER BY rt.base_price, r.number",
        (hid,)))
    bookings = _rows(conn.execute(
        "SELECT code, room_id, guest_name, check_in, check_out, status, channel FROM bookings "
        "WHERE hotel_id = ? AND check_in < ? AND check_out > ? AND status NOT IN ('cancelled','expired') "
        "ORDER BY check_in", (hid, end.isoformat(), start.isoformat())))
    return {"start": start.isoformat(), "days": days, "rooms": rooms, "bookings": bookings}


# ------------------------------------------------------------------ bookings

@router.get("/staff/bookings")
def list_bookings(status: str | None = None, q: str | None = None, channel: str | None = None,
                  date_from: date | None = None, date_to: date | None = None,
                  staff=Depends(current_staff), conn=Depends(get_conn)):
    expire_holds(conn)
    sql = ("SELECT b.*, r.number AS room_number, rt.name AS room_type_name, rt.code AS room_type_code FROM bookings b "
           "JOIN rooms r ON r.id = b.room_id JOIN room_types rt ON rt.id = r.room_type_id WHERE b.hotel_id = ?")
    params: list = [staff["hotel_id"]]
    if status:
        sql += " AND b.status = ?"
        params.append(status)
    if channel:
        sql += " AND b.channel = ?"
        params.append(channel)
    if q:
        sql += " AND (b.guest_name LIKE ? OR b.phone LIKE ? OR b.code LIKE ?)"
        params += [f"%{q}%"] * 3
    if date_from:
        sql += " AND b.check_out > ?"
        params.append(date_from.isoformat())
    if date_to:
        sql += " AND b.check_in <= ?"
        params.append(date_to.isoformat())
    sql += " ORDER BY CASE b.status WHEN 'hold' THEN 0 ELSE 1 END, b.created_at DESC, b.check_in LIMIT 300"
    return _rows(conn.execute(sql, params))


@router.get("/staff/bookings/{code}")
def booking_detail(code: str, staff=Depends(current_staff), conn=Depends(get_conn)):
    hid = staff["hotel_id"]
    b = get_booking(conn, hid, code)
    if not b:
        raise HTTPException(404, "Không tìm thấy booking")
    ci, co = date.fromisoformat(b["check_in"]), date.fromisoformat(b["check_out"])
    nights = [{"date": (ci + timedelta(days=i)).isoformat(),
               "price": nightly_price(conn, hid, b["room_type_id"], ci + timedelta(days=i))}
              for i in range((co - ci).days)]
    movable = [{"id": r["id"], "number": r["number"], "type_name": r["type_name"]}
               for r in free_rooms(conn, hid, None, ci, co, exclude_booking_id=b["id"])]
    conversation = list_messages(conn, hid, b["session_id"]) if b["session_id"] else []
    return {"booking": dict(b), "nights": nights, "movable_rooms": movable, "conversation": conversation}


class BookingCreate(BaseModel):
    room_type_code: str
    room_id: int | None = None
    check_in: date
    check_out: date
    adults: int = Field(2, ge=1, le=20)
    children: int = Field(0, ge=0, le=10)
    guest_name: str = Field(min_length=2)
    phone: str = Field(min_length=6)
    email: str = ""
    note: str = ""
    channel: Literal["walk-in", "phone", "ota", "zalo", "website"] = "walk-in"


@router.post("/staff/bookings")
def staff_create_booking(body: BookingCreate, staff=Depends(current_staff), conn=Depends(get_conn)):
    hid = staff["hotel_id"]
    rt = conn.execute("SELECT id FROM room_types WHERE hotel_id = ? AND code = ?", (hid, body.room_type_code)).fetchone()
    if not rt:
        raise HTTPException(404, "Loại phòng không tồn tại")
    if body.check_out <= body.check_in:
        raise HTTPException(400, "Ngày trả phòng phải sau ngày nhận phòng")
    try:
        b = create_booking(conn, hid, room_type_id=rt["id"], room_id=body.room_id, check_in=body.check_in,
                           check_out=body.check_out, guest_name=body.guest_name, phone=body.phone, email=body.email,
                           adults=body.adults, children=body.children, status="confirmed", channel=body.channel,
                           note=body.note)
    except BookingError as e:
        raise HTTPException(409, str(e))
    return dict(b)


class BookingPatch(BaseModel):
    guest_name: str | None = None
    phone: str | None = None
    email: str | None = None
    note: str | None = None
    adults: int | None = None
    children: int | None = None
    room_id: int | None = None
    check_in: str | None = None
    check_out: str | None = None


@router.patch("/staff/bookings/{code}")
def staff_update_booking(code: str, body: BookingPatch, staff=Depends(current_staff), conn=Depends(get_conn)):
    try:
        return dict(update_booking(conn, staff["hotel_id"], code, body.model_dump(exclude_none=True)))
    except BookingError as e:
        raise HTTPException(409, str(e))


@router.post("/staff/bookings/{code}/{action}")
def staff_booking_action(code: str, action: str, staff=Depends(current_staff), conn=Depends(get_conn)):
    try:
        return dict(apply_action(conn, staff["hotel_id"], code, action))
    except BookingError as e:
        raise HTTPException(409, str(e))


@router.get("/staff/availability")
def staff_availability(check_in: date, check_out: date, room_type_code: str | None = None,
                       staff=Depends(current_staff), conn=Depends(get_conn)):
    hid = staff["hotel_id"]
    type_id = None
    if room_type_code:
        rt = conn.execute("SELECT id FROM room_types WHERE hotel_id = ? AND code = ?", (hid, room_type_code)).fetchone()
        type_id = rt["id"] if rt else -1
    return [{"id": r["id"], "number": r["number"], "type_code": r["type_code"], "type_name": r["type_name"]}
            for r in free_rooms(conn, hid, type_id, check_in, check_out)]


# ------------------------------------------------------------------ hotel info

class HotelPatch(BaseModel):
    name: str | None = None
    address: str | None = None
    city: str | None = None
    phone: str | None = None
    email: str | None = None
    checkin_time: str | None = None
    checkout_time: str | None = None
    description: str | None = None


@router.get("/staff/hotel")
def get_hotel_info(staff=Depends(current_staff), conn=Depends(get_conn)):
    return dict(conn.execute("SELECT * FROM hotels WHERE id = ?", (staff["hotel_id"],)).fetchone())


@router.patch("/staff/hotel")
def patch_hotel(body: HotelPatch, staff=Depends(manager_only), conn=Depends(get_conn)):
    fields = body.model_dump(exclude_none=True)
    if fields:
        conn.execute(f"UPDATE hotels SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                     [*fields.values(), staff["hotel_id"]])
        conn.commit()
    return get_hotel_info(staff, conn)


# ------------------------------------------------------------------ room types & rooms

class RoomTypeIn(BaseModel):
    code: str = Field(min_length=2, max_length=10)
    name: str = Field(min_length=2)
    description: str = ""
    bed: str = ""
    max_adults: int = Field(2, ge=1, le=20)
    max_children: int = Field(0, ge=0, le=10)
    size_m2: int | None = None
    view: str = ""
    amenities: list[str] = []
    base_price: int = Field(ge=10_000)
    breakfast_included: bool = False


def _room_type_out(r) -> dict:
    d = dict(r)
    d["amenities"] = json.loads(d["amenities"] or "[]")
    d["breakfast_included"] = bool(d["breakfast_included"])
    return d


@router.get("/staff/room-types")
def list_room_types(staff=Depends(current_staff), conn=Depends(get_conn)):
    rows = conn.execute("SELECT rt.*, (SELECT COUNT(*) FROM rooms r WHERE r.room_type_id = rt.id) AS room_count "
                        "FROM room_types rt WHERE rt.hotel_id = ? ORDER BY base_price", (staff["hotel_id"],))
    return [_room_type_out(r) for r in rows]


def _room_type_values(body: RoomTypeIn) -> list:
    return [body.code.upper(), body.name, body.description, body.bed, body.max_adults, body.max_children,
            body.size_m2, body.view, json.dumps(body.amenities, ensure_ascii=False), body.base_price,
            int(body.breakfast_included)]


@router.post("/staff/room-types")
def create_room_type(body: RoomTypeIn, staff=Depends(manager_only), conn=Depends(get_conn)):
    try:
        conn.execute("INSERT INTO room_types (code,name,description,bed,max_adults,max_children,size_m2,view,amenities,"
                     "base_price,breakfast_included,hotel_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     _room_type_values(body) + [staff["hotel_id"]])
        conn.commit()
    except Exception:
        raise HTTPException(409, "Mã loại phòng đã tồn tại")
    return {"ok": True}


@router.put("/staff/room-types/{type_id}")
def update_room_type(type_id: int, body: RoomTypeIn, staff=Depends(manager_only), conn=Depends(get_conn)):
    cur = conn.execute("UPDATE room_types SET code=?, name=?, description=?, bed=?, max_adults=?, max_children=?, "
                       "size_m2=?, view=?, amenities=?, base_price=?, breakfast_included=? WHERE id = ? AND hotel_id = ?",
                       _room_type_values(body) + [type_id, staff["hotel_id"]])
    conn.commit()
    if not cur.rowcount:
        raise HTTPException(404, "Không tìm thấy loại phòng")
    return {"ok": True}


@router.get("/staff/rooms")
def list_rooms(staff=Depends(current_staff), conn=Depends(get_conn)):
    hid = staff["hotel_id"]
    today = date.today().isoformat()
    return _rows(conn.execute(
        f"""SELECT r.*, rt.code AS type_code, rt.name AS type_name,
            (SELECT b.guest_name FROM bookings b WHERE b.room_id = r.id AND b.status = 'checked_in' LIMIT 1) AS guest,
            (SELECT COUNT(*) FROM bookings b WHERE b.room_id = r.id AND b.check_in <= ? AND b.check_out > ?
               AND {BLOCKING_SQL}) AS occupied_tonight
            FROM rooms r JOIN room_types rt ON rt.id = r.room_type_id WHERE r.hotel_id = ? ORDER BY r.number""",
        (today, today, _now(), hid)))


class RoomIn(BaseModel):
    number: str = Field(min_length=1, max_length=10)
    floor: int | None = None
    room_type_id: int


class RoomPatch(BaseModel):
    status: Literal["active", "maintenance"] | None = None
    housekeeping: Literal["clean", "dirty"] | None = None
    room_type_id: int | None = None
    number: str | None = None
    floor: int | None = None


@router.post("/staff/rooms")
def create_room(body: RoomIn, staff=Depends(manager_only), conn=Depends(get_conn)):
    if not conn.execute("SELECT 1 FROM room_types WHERE id = ? AND hotel_id = ?",
                        (body.room_type_id, staff["hotel_id"])).fetchone():
        raise HTTPException(404, "Loại phòng không tồn tại")
    try:
        conn.execute("INSERT INTO rooms (hotel_id, room_type_id, number, floor) VALUES (?,?,?,?)",
                     (staff["hotel_id"], body.room_type_id, body.number, body.floor))
        conn.commit()
    except Exception:
        raise HTTPException(409, "Số phòng đã tồn tại")
    return {"ok": True}


@router.patch("/staff/rooms/{room_id}")
def patch_room(room_id: int, body: RoomPatch, staff=Depends(current_staff), conn=Depends(get_conn)):
    fields = body.model_dump(exclude_none=True)
    if set(fields) - {"housekeeping", "status"} and staff["role"] != "manager":
        raise HTTPException(403, "Chỉ quản lý mới đổi loại/số phòng")
    if "room_type_id" in fields and not conn.execute("SELECT 1 FROM room_types WHERE id = ? AND hotel_id = ?",
                                                      (fields["room_type_id"], staff["hotel_id"])).fetchone():
        raise HTTPException(404, "Loại phòng không tồn tại")
    if fields:
        cur = conn.execute(f"UPDATE rooms SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ? AND hotel_id = ?",
                           [*fields.values(), room_id, staff["hotel_id"]])
        conn.commit()
        if not cur.rowcount:
            raise HTTPException(404, "Không tìm thấy phòng")
    return {"ok": True}


# ------------------------------------------------------------------ rates

class RateRuleIn(BaseModel):
    kind: Literal["season", "weekday"]
    name: str = Field(min_length=2)
    start_date: date | None = None
    end_date: date | None = None
    weekdays: list[int] = []
    multiplier: float = Field(gt=0.1, lt=10)
    priority: int = 0
    room_type_id: int | None = None


def _rule_values(body: RateRuleIn) -> list:
    if body.kind == "season" and not (body.start_date and body.end_date and body.end_date >= body.start_date):
        raise HTTPException(400, "Mùa giá cần ngày bắt đầu và kết thúc hợp lệ")
    if body.kind == "weekday" and not body.weekdays:
        raise HTTPException(400, "Chọn ít nhất một ngày trong tuần")
    return [body.kind, body.name, body.start_date.isoformat() if body.start_date else None,
            body.end_date.isoformat() if body.end_date else None,
            json.dumps(sorted(set(body.weekdays))) if body.kind == "weekday" else None,
            body.multiplier, body.priority, body.room_type_id]


@router.get("/staff/rate-rules")
def list_rate_rules(staff=Depends(current_staff), conn=Depends(get_conn)):
    rows = _rows(conn.execute("SELECT rr.*, rt.name AS room_type_name FROM rate_rules rr "
                              "LEFT JOIN room_types rt ON rt.id = rr.room_type_id WHERE rr.hotel_id = ? "
                              "ORDER BY rr.kind, rr.start_date", (staff["hotel_id"],)))
    for r in rows:
        r["weekdays"] = json.loads(r["weekdays"]) if r["weekdays"] else []
    return rows


@router.post("/staff/rate-rules")
def create_rate_rule(body: RateRuleIn, staff=Depends(manager_only), conn=Depends(get_conn)):
    conn.execute("INSERT INTO rate_rules (kind,name,start_date,end_date,weekdays,multiplier,priority,room_type_id,"
                 "hotel_id) VALUES (?,?,?,?,?,?,?,?,?)", _rule_values(body) + [staff["hotel_id"]])
    conn.commit()
    return {"ok": True}


@router.put("/staff/rate-rules/{rule_id}")
def update_rate_rule(rule_id: int, body: RateRuleIn, staff=Depends(manager_only), conn=Depends(get_conn)):
    conn.execute("UPDATE rate_rules SET kind=?, name=?, start_date=?, end_date=?, weekdays=?, multiplier=?, priority=?, "
                 "room_type_id=? WHERE id = ? AND hotel_id = ?", _rule_values(body) + [rule_id, staff["hotel_id"]])
    conn.commit()
    return {"ok": True}


@router.delete("/staff/rate-rules/{rule_id}")
def delete_rate_rule(rule_id: int, staff=Depends(manager_only), conn=Depends(get_conn)):
    conn.execute("DELETE FROM rate_rules WHERE id = ? AND hotel_id = ?", (rule_id, staff["hotel_id"]))
    conn.commit()
    return {"ok": True}


@router.get("/staff/prices")
def price_matrix(start: date | None = None, days: int = 14, staff=Depends(current_staff), conn=Depends(get_conn)):
    """Nightly price per room type — exactly what the AI will quote."""
    hid = staff["hotel_id"]
    start = start or date.today()
    dates = [start + timedelta(days=i) for i in range(max(1, min(days, 31)))]
    out = []
    for rt in conn.execute("SELECT id, code, name, base_price FROM room_types WHERE hotel_id = ? ORDER BY base_price",
                           (hid,)):
        out.append({"code": rt["code"], "name": rt["name"], "base_price": rt["base_price"],
                    "prices": [nightly_price(conn, hid, rt["id"], d) for d in dates]})
    return {"dates": [d.isoformat() for d in dates], "room_types": out}


# ------------------------------------------------------------------ policies (AI knowledge base)

class PolicyIn(BaseModel):
    category: str = ""
    title: str = Field(min_length=2)
    content: str = Field(min_length=2)
    keywords: str = ""


@router.get("/staff/policies")
def list_policies(staff=Depends(current_staff), conn=Depends(get_conn)):
    return _rows(conn.execute("SELECT * FROM policies WHERE hotel_id = ? ORDER BY id", (staff["hotel_id"],)))


@router.post("/staff/policies")
def create_policy(body: PolicyIn, staff=Depends(manager_only), conn=Depends(get_conn)):
    conn.execute("INSERT INTO policies (hotel_id, category, title, content, keywords) VALUES (?,?,?,?,?)",
                 (staff["hotel_id"], body.category, body.title, body.content, body.keywords))
    conn.commit()
    return {"ok": True}


@router.put("/staff/policies/{pid}")
def update_policy(pid: int, body: PolicyIn, staff=Depends(manager_only), conn=Depends(get_conn)):
    conn.execute("UPDATE policies SET category=?, title=?, content=?, keywords=? WHERE id = ? AND hotel_id = ?",
                 (body.category, body.title, body.content, body.keywords, pid, staff["hotel_id"]))
    conn.commit()
    return {"ok": True}


@router.delete("/staff/policies/{pid}")
def delete_policy(pid: int, staff=Depends(manager_only), conn=Depends(get_conn)):
    conn.execute("DELETE FROM policies WHERE id = ? AND hotel_id = ?", (pid, staff["hotel_id"]))
    conn.commit()
    return {"ok": True}


# ------------------------------------------------------------------ AI conversations inbox

@router.get("/staff/conversations")
def conversations(staff=Depends(current_staff), conn=Depends(get_conn)):
    hid = staff["hotel_id"]
    return _rows(conn.execute(
        """SELECT s.session_id, s.channel, s.guest_name, s.mode, s.created_at, s.last_message_at,
             (SELECT content FROM messages m WHERE m.hotel_id = s.hotel_id AND m.session_id = s.session_id
                ORDER BY id DESC LIMIT 1) AS last_message,
             (SELECT role FROM messages m WHERE m.hotel_id = s.hotel_id AND m.session_id = s.session_id
                ORDER BY id DESC LIMIT 1) AS last_role,
             (SELECT COUNT(*) FROM messages m WHERE m.hotel_id = s.hotel_id AND m.session_id = s.session_id) AS n_messages,
             (SELECT reason FROM handoffs h WHERE h.hotel_id = s.hotel_id AND h.session_id = s.session_id
                AND h.status = 'open' ORDER BY id DESC LIMIT 1) AS handoff_reason,
             (SELECT COUNT(*) FROM bookings b WHERE b.hotel_id = s.hotel_id AND b.session_id = s.session_id) AS n_bookings
           FROM chat_sessions s WHERE s.hotel_id = ?
           ORDER BY CASE s.mode WHEN 'staff' THEN 0 ELSE 1 END, s.last_message_at DESC LIMIT 200""", (hid,)))


@router.get("/staff/conversations/{session_id}")
def conversation_detail(session_id: str, staff=Depends(current_staff), conn=Depends(get_conn)):
    hid = staff["hotel_id"]
    s = get_session(conn, hid, session_id)
    if not s:
        raise HTTPException(404, "Không tìm thấy hội thoại")
    handoffs = _rows(conn.execute("SELECT * FROM handoffs WHERE hotel_id = ? AND session_id = ? ORDER BY id",
                                  (hid, session_id)))
    bookings = _rows(conn.execute("SELECT code, status, check_in, check_out, total_price, guest_name FROM bookings "
                                  "WHERE hotel_id = ? AND session_id = ? ORDER BY id", (hid, session_id)))
    return {"session": dict(s), "messages": list_messages(conn, hid, session_id, with_trace=True),
            "handoffs": handoffs, "bookings": bookings}


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/staff/conversations/{session_id}/reply")
def staff_reply(session_id: str, body: ReplyIn, staff=Depends(current_staff), conn=Depends(get_conn)):
    hid = staff["hotel_id"]
    if not get_session(conn, hid, session_id):
        raise HTTPException(404, "Không tìm thấy hội thoại")
    set_mode(conn, hid, session_id, "staff")  # replying takes the conversation over
    add_message(conn, hid, session_id, "staff", body.text.strip())
    return {"ok": True}


class ModeIn(BaseModel):
    mode: Literal["ai", "staff"]


@router.post("/staff/conversations/{session_id}/mode")
def staff_set_mode(session_id: str, body: ModeIn, staff=Depends(current_staff), conn=Depends(get_conn)):
    hid = staff["hotel_id"]
    if not get_session(conn, hid, session_id):
        raise HTTPException(404, "Không tìm thấy hội thoại")
    set_mode(conn, hid, session_id, body.mode)
    return {"ok": True, "mode": body.mode}


# ------------------------------------------------------------------ demo reset

@router.post("/staff/reset-demo")
def reset_demo(staff=Depends(manager_only)):
    from .seed import reset_database
    reset_database()
    return {"ok": True}
