"""Tools the AI may call. The model never touches SQL directly: it can only
call these functions, and hotel_id comes from the server, not from the model,
so one hotel's bot can never read another hotel's data."""
import json
import math
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from .bookings import BookingError, create_booking
from .pricing import format_vnd, free_rooms, nightly_price, stay_nights

MAX_NIGHTS = 30


@dataclass
class ToolContext:
    conn: sqlite3.Connection
    hotel_id: int
    session_id: str = ""
    today: date = field(default_factory=date.today)
    now: datetime = field(default_factory=datetime.now)
    # False when no date appears anywhere in the conversation: blocks the model from inventing one.
    guest_gave_dates: bool = True
    channel: str = "ai-chat"
    # Last successful check_availability in this conversation: fills args the model leaves out.
    search_context: dict | None = None


class ToolError(Exception):
    """Returned to the model as {"error": ...} so it can correct itself or ask the guest."""


# ---------------------------------------------------------------- helpers

def normalize(text: str) -> str:
    """Lowercase and strip Vietnamese accents: 'Hủy phòng' -> 'huy phong'."""
    text = text.lower().replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _parse_date(value: Any, name: str) -> date:
    if not value:
        raise ToolError(f"Thiếu {name}. Hãy hỏi khách ngày cụ thể.")
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ToolError(f"{name}='{s}' không đúng định dạng YYYY-MM-DD.")


def _parse_stay(ctx: ToolContext, args: dict) -> tuple[date, date]:
    if not ctx.guest_gave_dates:
        raise ToolError("Khách CHƯA nói ngày. Không được tự chọn ngày. Hãy hỏi khách ngày nhận phòng, "
                        "ngày trả phòng (vd 20/11 – 22/11) và số người.")
    check_in = _parse_date(args.get("check_in"), "check_in")
    if args.get("check_out"):
        check_out = _parse_date(args.get("check_out"), "check_out")
    elif args.get("nights"):
        check_out = check_in + timedelta(days=int(args["nights"]))
    else:
        check_out = check_in + timedelta(days=1)
    if check_in < ctx.today:
        raise ToolError(f"Ngày nhận phòng {check_in:%d/%m/%Y} đã qua (hôm nay là {ctx.today:%d/%m/%Y}).")
    if check_out <= check_in:
        raise ToolError("Ngày trả phòng phải sau ngày nhận phòng.")
    if (check_out - check_in).days > MAX_NIGHTS:
        raise ToolError(f"Chỉ tra cứu tối đa {MAX_NIGHTS} đêm. Với lưu trú dài ngày hãy chuyển nhân viên.")
    if check_in > ctx.today + timedelta(days=365):
        raise ToolError("Chỉ nhận đặt trong vòng 12 tháng tới.")
    return check_in, check_out


def _int(args: dict, key: str, default: int, lo: int = 0, hi: int = 50) -> int:
    try:
        v = int(args.get(key) if args.get(key) is not None else default)
    except (TypeError, ValueError):
        raise ToolError(f"{key} phải là số nguyên.")
    if not lo <= v <= hi:
        raise ToolError(f"{key} phải trong khoảng {lo}–{hi}.")
    return v


def _room_type(ctx: ToolContext, code: str) -> sqlite3.Row:
    row = ctx.conn.execute("SELECT * FROM room_types WHERE hotel_id = ? AND upper(code) = upper(?)",
                           (ctx.hotel_id, str(code or "").strip())).fetchone()
    if not row:
        codes = [r["code"] for r in ctx.conn.execute("SELECT code FROM room_types WHERE hotel_id = ?", (ctx.hotel_id,))]
        raise ToolError(f"Không có loại phòng mã '{code}'. Các mã hợp lệ: {', '.join(codes)}.")
    return row


def _room_type_summary(rt: sqlite3.Row) -> dict:
    return {
        "room_type_code": rt["code"],
        "name": rt["name"],
        "bed": rt["bed"],
        "max_adults": rt["max_adults"],
        "max_children": rt["max_children"],
        "size_m2": rt["size_m2"],
        "view": rt["view"],
        "breakfast_included": bool(rt["breakfast_included"]),
        "from_price_per_night": rt["base_price"],
        "from_price_text": format_vnd(rt["base_price"]) + "/đêm (ngày thường)",
    }


# ---------------------------------------------------------------- tools

def get_hotel_info(ctx: ToolContext, args: dict) -> dict:
    h = ctx.conn.execute("SELECT * FROM hotels WHERE id = ?", (ctx.hotel_id,)).fetchone()
    return {k: h[k] for k in ("name", "stars", "address", "city", "phone", "email",
                              "checkin_time", "checkout_time", "description")}


def list_room_types(ctx: ToolContext, args: dict) -> dict:
    rows = ctx.conn.execute("SELECT * FROM room_types WHERE hotel_id = ? ORDER BY base_price", (ctx.hotel_id,))
    return {"room_types": [_room_type_summary(r) for r in rows],
            "note": "Giá trên là giá ngày thường. Cuối tuần/lễ có phụ thu. Muốn giá chính xác phải gọi check_availability."}


def get_room_type_details(ctx: ToolContext, args: dict) -> dict:
    rt = _room_type(ctx, args.get("room_type_code"))
    out = _room_type_summary(rt)
    out["description"] = rt["description"]
    out["amenities"] = json.loads(rt["amenities"] or "[]")
    return out


def check_availability(ctx: ToolContext, args: dict) -> dict:
    check_in, check_out = _parse_stay(ctx, args)
    adults = _int(args, "adults", 2, 1, 50)
    children = _int(args, "children", 0, 0, 20)
    wanted = args.get("room_type_code")
    sql = "SELECT * FROM room_types WHERE hotel_id = ?"
    params: list = [ctx.hotel_id]
    if wanted:
        _room_type(ctx, wanted)  # validate
        sql += " AND upper(code) = upper(?)"
        params.append(wanted)
    nights = stay_nights(check_in, check_out)

    options = []
    for rt in ctx.conn.execute(sql + " ORDER BY base_price", params).fetchall():
        free = free_rooms(ctx.conn, ctx.hotel_id, rt["id"], check_in, check_out, ctx.now)
        rooms_needed = max(math.ceil(adults / rt["max_adults"]),
                           math.ceil(children / rt["max_children"]) if children and rt["max_children"] else 1,
                           1)
        if children and rt["max_children"] == 0:
            rooms_needed = None  # children not allowed in this type
        nightly = [{"date": n.isoformat(), "weekday": ["T2", "T3", "T4", "T5", "T6", "T7", "CN"][n.weekday()],
                    "price": nightly_price(ctx.conn, ctx.hotel_id, rt["id"], n)} for n in nights]
        per_room_total = sum(x["price"] for x in nightly)
        opt = {
            "room_type_code": rt["code"],
            "name": rt["name"],
            "available_rooms": len(free),
            "max_adults_per_room": rt["max_adults"],
            "max_children_per_room": rt["max_children"],
            "breakfast_included": bool(rt["breakfast_included"]),
            "nightly_prices": nightly,
            "total_per_room": per_room_total,
            "total_per_room_text": format_vnd(per_room_total),
        }
        if rooms_needed is None:
            opt["fits_party"] = False
            opt["note"] = "Loại phòng này không nhận trẻ em."
        else:
            opt["rooms_needed"] = rooms_needed
            opt["fits_party"] = len(free) >= rooms_needed
            opt["total_for_party"] = per_room_total * rooms_needed
            opt["total_for_party_text"] = format_vnd(per_room_total * rooms_needed)
        options.append(opt)

    return {
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
        "nights": len(nights),
        "adults": adults,
        "children": children,
        "options": options,
        "any_available": any(o.get("fits_party") for o in options),
        "currency": "VND",
    }


def search_policies(ctx: ToolContext, args: dict) -> dict:
    from .hints import STOPWORDS
    query = normalize(str(args.get("query", "")))
    tokens = [t for t in re.findall(r"\w+", query) if len(t) > 1 and t not in STOPWORDS]
    if not tokens:
        return {"results": [], "note": "Không tìm thấy thông tin. Đừng tự đoán — đề nghị chuyển nhân viên."}
    scored = []
    for p in ctx.conn.execute("SELECT * FROM policies WHERE hotel_id = ?", (ctx.hotel_id,)):
        title = normalize(p["title"])
        haystack = " ".join([title, normalize(p["content"]), normalize(p["keywords"] or ""), p["category"] or ""])
        words = set(re.findall(r"\w+", haystack))  # whole words: 'san' must not match 'sang'
        score = sum(2 if t in title.split() else 1 for t in tokens if t in words)
        if score:
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    results = [{"title": p["title"], "content": p["content"]} for _, p in scored[:3]]
    if not results:
        return {"results": [], "note": "Không tìm thấy thông tin. Đừng tự đoán — đề nghị chuyển nhân viên."}
    return {"results": results}


def create_booking_hold(ctx: ToolContext, args: dict) -> dict:
    # Anything the model leaves out comes from the last availability search of this conversation.
    sc = ctx.search_context or {}
    args = {**{k: v for k, v in sc.items() if v not in (None, "")},
            **{k: v for k, v in args.items() if v not in (None, "")}}
    if not args.get("room_type_code"):
        raise ToolError("Chưa biết loại phòng. Hãy hỏi khách muốn loại phòng nào (vd STD, DLX).")
    check_in, check_out = _parse_stay(ctx, args)
    rt = _room_type(ctx, args.get("room_type_code"))
    name = str(args.get("guest_name") or "").strip()
    phone = re.sub(r"[^\d+]", "", str(args.get("phone") or ""))
    if len(name) < 2:
        raise ToolError("Thiếu tên khách. Hãy hỏi tên trước khi giữ phòng.")
    if not re.fullmatch(r"\+?\d{9,12}", phone):
        raise ToolError("Số điện thoại không hợp lệ. Hãy hỏi lại số điện thoại của khách.")
    adults = _int(args, "adults", 2, 1, rt["max_adults"])
    children = _int(args, "children", 0, 0, rt["max_children"])
    try:
        b = create_booking(ctx.conn, ctx.hotel_id, room_type_id=rt["id"], check_in=check_in, check_out=check_out,
                           guest_name=name, phone=phone, adults=adults, children=children, status="hold",
                           channel=ctx.channel, note=str(args.get("note") or ""), session_id=ctx.session_id or None,
                           now=ctx.now)
    except BookingError:
        raise ToolError("Loại phòng này đã hết trong thời gian đó. Hãy gọi check_availability để gợi ý loại khác.")
    expires = datetime.strptime(b["hold_expires_at"], "%Y-%m-%d %H:%M:%S")
    return {
        "hold_code": b["code"],
        "status": "hold",
        "room_type": rt["name"],
        "check_in": check_in.isoformat(),
        "check_out": check_out.isoformat(),
        "total_price": b["total_price"],
        "total_price_text": format_vnd(b["total_price"]),
        "hold_expires_at": expires.strftime("%H:%M %d/%m/%Y"),
        "next_step": "Nhân viên sẽ liên hệ qua số điện thoại để gửi thông tin đặt cọc và xác nhận. "
                     "Giữ chỗ tự hủy nếu chưa xác nhận trước giờ hết hạn.",
    }


def handoff_to_staff(ctx: ToolContext, args: dict) -> dict:
    reason = str(args.get("reason") or "khác")[:200]
    summary = str(args.get("summary") or "")[:1000]
    ctx.conn.execute("INSERT INTO handoffs (hotel_id, session_id, reason, summary) VALUES (?,?,?,?)",
                     (ctx.hotel_id, ctx.session_id, reason, summary))
    # AI stops answering this conversation until staff hands it back.
    ctx.conn.execute("UPDATE chat_sessions SET mode = 'staff' WHERE hotel_id = ? AND session_id = ?",
                     (ctx.hotel_id, ctx.session_id))
    ctx.conn.commit()
    h = ctx.conn.execute("SELECT phone FROM hotels WHERE id = ?", (ctx.hotel_id,)).fetchone()
    return {"ok": True, "message": "Đã báo nhân viên, họ sẽ phản hồi trong ít phút (giờ hành chính) hoặc sớm nhất có thể.",
            "hotline": h["phone"]}


# ---------------------------------------------------------------- registry

TOOL_FUNCTIONS: dict[str, Callable[[ToolContext, dict], dict]] = {
    "get_hotel_info": get_hotel_info,
    "list_room_types": list_room_types,
    "get_room_type_details": get_room_type_details,
    "check_availability": check_availability,
    "search_policies": search_policies,
    "create_booking_hold": create_booking_hold,
    "handoff_to_staff": handoff_to_staff,
}

_DATE = {"type": "string", "description": "Ngày dạng YYYY-MM-DD"}

TOOL_SPECS: list[dict] = [
    {"type": "function", "function": {
        "name": "get_hotel_info",
        "description": "Thông tin chung của khách sạn: địa chỉ, hotline, giờ nhận/trả phòng, mô tả.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "list_room_types",
        "description": "Danh sách các loại phòng, sức chứa và giá tham khảo ngày thường. Không cho biết phòng trống.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "get_room_type_details",
        "description": "Chi tiết một loại phòng: diện tích, giường, view, tiện nghi.",
        "parameters": {"type": "object", "properties": {
            "room_type_code": {"type": "string", "description": "Mã loại phòng, vd STD, DLX"}},
            "required": ["room_type_code"]}}},
    {"type": "function", "function": {
        "name": "check_availability",
        "description": "Kiểm tra phòng trống và giá chính xác theo từng đêm cho khoảng ngày và số khách. "
                       "Luôn gọi hàm này trước khi báo giá hoặc nói còn/hết phòng.",
        "parameters": {"type": "object", "properties": {
            "check_in": _DATE,
            "check_out": {"type": "string", "description": "Ngày trả phòng YYYY-MM-DD (không tính đêm này)"},
            "adults": {"type": "integer", "description": "Số người lớn, mặc định 2"},
            "children": {"type": "integer", "description": "Số trẻ em, mặc định 0"},
            "room_type_code": {"type": "string", "description": "Tùy chọn: chỉ kiểm tra một loại phòng"}},
            "required": ["check_in", "check_out"]}}},
    {"type": "function", "function": {
        "name": "search_policies",
        "description": "Tìm chính sách/FAQ: hủy phòng, đặt cọc, bữa sáng, trẻ em, thú cưng, đưa đón, gửi xe, tiện ích, vị trí...",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Từ khóa, vd 'hủy phòng', 'đưa đón sân bay'"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "create_booking_hold",
        "description": "Giữ chỗ tạm thời (chưa xác nhận) sau khi khách đồng ý đặt và đã cho tên + số điện thoại. "
                       "Ngày và số khách tự lấy từ lần check_availability gần nhất nếu bỏ trống. "
                       "Nhân viên sẽ liên hệ xác nhận và nhận cọc.",
        "parameters": {"type": "object", "properties": {
            "room_type_code": {"type": "string", "description": "Mã loại phòng khách chọn"},
            "check_in": {"type": "string", "description": "YYYY-MM-DD, bỏ trống = dùng lần tra gần nhất"},
            "check_out": {"type": "string", "description": "YYYY-MM-DD, bỏ trống = dùng lần tra gần nhất"},
            "adults": {"type": "integer"},
            "children": {"type": "integer"},
            "guest_name": {"type": "string"},
            "phone": {"type": "string"},
            "note": {"type": "string", "description": "Yêu cầu thêm của khách nếu có"}},
            "required": ["room_type_code", "guest_name", "phone"]}}},
    {"type": "function", "function": {
        "name": "handoff_to_staff",
        "description": "Chuyển cuộc trò chuyện cho nhân viên thật: khiếu nại, yêu cầu đặc biệt, đặt đoàn, "
                       "khách muốn gặp người thật, hoặc câu hỏi không có thông tin.",
        "parameters": {"type": "object", "properties": {
            "reason": {"type": "string", "description": "Lý do ngắn gọn"},
            "summary": {"type": "string", "description": "Tóm tắt nhu cầu của khách cho nhân viên"}},
            "required": ["reason"]}}},
]


def execute_tool(ctx: ToolContext, name: str, args: dict | str | None) -> dict:
    """Run a tool and always return a JSON-serialisable dict (errors included)."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"error": f"Không có công cụ '{name}'. Công cụ hợp lệ: {', '.join(TOOL_FUNCTIONS)}"}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            return {"error": "Tham số không phải JSON hợp lệ."}
    try:
        return fn(ctx, args or {})
    except ToolError as e:
        return {"error": str(e)}
