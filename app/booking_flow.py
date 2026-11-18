"""Deterministic booking step (slot filling), used when the model fails to call create_booking_hold.

Slots: room type (from the message or the last search), dates (last search), guest name and phone (message).
Small models are unreliable at tool calls with several arguments; this keeps the booking step correct.
"""
import re
from datetime import date

from .hints import PHONE_RE
from .tools import ToolContext, execute_tool, normalize

_NAME_AFTER = re.compile(r"(?:tên|ten|name)\s*(?:là|la|:|is)?\s*([^\d,.;:\n]{2,40})", re.I)
_NOT_NAME = {"ok", "oke", "dạ", "vâng", "chốt", "đặt", "giúp", "mình", "em", "anh", "chị", "nhé", "phòng", "sđt", "số",
             "hi", "hello", "please", "book", "yes"}


def extract_phone(text: str) -> str | None:
    m = PHONE_RE.search(text)
    return re.sub(r"[^\d+]", "", m.group(0)) if m else None


def extract_name(text: str, room_words: set[str]) -> str | None:
    """'tên Trần Eval' → 'Trần Eval'; otherwise the longest run of 2–5 Capitalised words."""
    no_phone = PHONE_RE.sub(" ", text)
    m = _NAME_AFTER.search(no_phone)
    if m:
        cand = re.split(r"\b(?:sđt|sdt|số|so|phone|điện thoại)\b", m.group(1), flags=re.I)[0].strip(" -,")
        if len(cand.split()) >= 1 and len(cand) >= 2:
            return cand
    best: list[str] = []
    run: list[str] = []
    for w in re.findall(r"[^\W\d_]+", no_phone):
        if w[0].isupper() and w.lower() not in _NOT_NAME and normalize(w) not in room_words:
            run.append(w)
        else:
            if len(run) > len(best):
                best = run
            run = []
    if len(run) > len(best):
        best = run
    return " ".join(best[:5]) if len(best) >= 2 else None


def mentioned_room_type(ctx: ToolContext, text: str) -> str | None:
    """Room type named in the message itself ('Superior', 'bungalow', 'DLX')."""
    tokens = set(re.findall(r"[a-z0-9]+", normalize(text)))
    for r in ctx.conn.execute("SELECT code, name FROM room_types WHERE hotel_id = ?", (ctx.hotel_id,)):
        words = [w for w in normalize(r["name"]).split() if len(w) > 3 and w not in ("phong",)]
        if r["code"].lower() in tokens or any(w in tokens for w in words):
            return r["code"]
    return None


def resolve_room_type(ctx: ToolContext, text: str) -> str | None:
    """Room type mentioned in the message, else the one chosen/searched earlier in the conversation."""
    return mentioned_room_type(ctx, text) or (ctx.search_context or {}).get("room_type_code")


def room_words(ctx: ToolContext) -> set[str]:
    words = set()
    for r in ctx.conn.execute("SELECT code, name FROM room_types WHERE hotel_id = ?", (ctx.hotel_id,)):
        words |= set(normalize(r["name"]).split()) | {r["code"].lower()}
    return words


_BOOK_INTENT = re.compile(r"\b(dat|giu|chot|lay|book|reserve|ok|oke|dong y|yes)\b")


def ready_to_book(ctx: ToolContext, text: str) -> bool:
    """Every slot is known → no need to ask the model. The guest either says 'book' in this message or already
    picked a room type earlier (we then asked for name + phone, and this message is the answer)."""
    chosen = (ctx.search_context or {}).get("room_type_code")
    return bool(PHONE_RE.search(text) and ctx.search_context and (_BOOK_INTENT.search(normalize(text)) or chosen)
                and resolve_room_type(ctx, text) and extract_name(text, room_words(ctx)))


def picks_room(ctx: ToolContext, text: str) -> str | None:
    """Guest picks a room type to book after a search, without name/phone yet → that room type's code."""
    if not ctx.search_context or PHONE_RE.search(text) or not _BOOK_INTENT.search(normalize(text)):
        return None
    return mentioned_room_type(ctx, text)


def pick_reply(result: dict, code: str, english: bool) -> str:
    """Summary of the chosen room (from a fresh search) + ask for name and phone; or say it is full."""
    opt = next((o for o in result.get("options", []) if o["room_type_code"] == code), None)
    ci, co = date.fromisoformat(result["check_in"]), date.fromisoformat(result["check_out"])
    party = f"{result['adults']} người lớn" + (f", {result['children']} trẻ em" if result["children"] else "")
    if not opt or not opt.get("fits_party"):
        name = opt["name"] if opt else code
        return (f"Sorry, the {name} is not available from {ci:%d/%m} to {co:%d/%m}. Would you like another room type?"
                if english else
                f"Dạ rất tiếc, {name} từ {ci:%d/%m} đến {co:%d/%m} đã hết phòng phù hợp cho {party} ạ. "
                "Anh/chị có muốn chọn loại phòng khác không ạ?")
    rooms = f" × {opt['rooms_needed']} phòng" if opt["rooms_needed"] > 1 else ""
    if english:
        return (f"Great choice: {opt['name']}{rooms}, {ci:%d/%m} – {co:%d/%m}, total {opt['total_for_party_text']}. "
                "To hold it for you, please send me your full name and phone number.")
    return (f"Dạ anh/chị chọn {opt['name']}{rooms}, {result['nights']} đêm từ {ci:%d/%m} đến {co:%d/%m} cho {party}, "
            f"tổng {opt['total_for_party_text']}. Để em giữ phòng, anh/chị cho em xin họ tên và số điện thoại nhé ạ.")


def try_hold(ctx: ToolContext, text: str, english: bool = False) -> tuple[str, dict | None]:
    """Create the hold if every slot is known; otherwise ask exactly for what is missing.
    Returns (reply, trace_entry or None)."""
    code = resolve_room_type(ctx, text)
    phone = extract_phone(text)
    name = extract_name(text, room_words(ctx))
    missing = []
    if not ctx.search_context:
        missing.append("ngày nhận phòng, ngày trả phòng và số người")
    if not code:
        missing.append("loại phòng muốn đặt")
    if not name:
        missing.append("họ tên người đặt")
    if not phone:
        missing.append("số điện thoại")
    if missing:
        if english:
            return "To hold the room for you, could you please send me: " + ", ".join(missing) + "?", None
        return "Dạ để giữ phòng, anh/chị cho em xin thêm: " + ", ".join(missing) + " ạ?", None

    args = {"room_type_code": code, "guest_name": name, "phone": phone}
    result = execute_tool(ctx, "create_booking_hold", args)
    entry = {"tool": "create_booking_hold", "args": {**args, "_by": "code"}, "result": result}
    if "error" in result:
        return f"Dạ {result['error'].split('. Hãy')[0]}. Anh/chị có muốn em kiểm tra loại phòng hoặc ngày khác không ạ?", entry
    ci, co = date.fromisoformat(result["check_in"]), date.fromisoformat(result["check_out"])
    if english:
        reply = (f"I have placed a hold on a {result['room_type']} for {name}, {ci:%d/%m} – {co:%d/%m}. "
                 f"Hold code: {result['hold_code']}, total {result['total_price_text']}. It is valid until "
                 f"{result['hold_expires_at']}; our staff will call {phone} to arrange the deposit and confirm.")
    else:
        reply = (f"Dạ em đã giữ phòng {result['room_type']} từ {ci:%d/%m} đến {co:%d/%m} cho anh/chị {name}. "
                 f"Mã giữ chỗ: {result['hold_code']}, tổng {result['total_price_text']}. Giữ chỗ có hiệu lực đến "
                 f"{result['hold_expires_at']}, nhân viên sẽ gọi số {phone} để gửi thông tin đặt cọc và xác nhận ạ.")
    return reply, entry
