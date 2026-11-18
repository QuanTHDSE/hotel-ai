"""Public API for the guest website and the AI chat widget (no login)."""
import json
import re
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .agent import LLMClient, LLMError, ReceptionistAgent
from .bookings import BookingError, create_booking, expire_holds
from .config import settings
from .deps import get_conn, hotel_or_404
from .sessions import add_message, ensure_session, get_session, list_messages
from .tools import ToolContext, check_availability, ToolError

router = APIRouter(prefix="/api")
llm = LLMClient()


@router.get("/health")
def health():
    return {"llm_base_url": settings.llm_base_url, "llm_model": settings.llm_model, "llm": llm.ping()}


@router.get("/hotels")
def hotels(conn=Depends(get_conn)):
    return [dict(r) for r in conn.execute("SELECT slug, name, city, stars, address, description FROM hotels ORDER BY id")]


@router.get("/hotels/{slug}")
def hotel_detail(slug: str, conn=Depends(get_conn)):
    h = hotel_or_404(conn, slug)
    types = []
    for r in conn.execute("SELECT * FROM room_types WHERE hotel_id = ? ORDER BY base_price", (h["id"],)):
        t = dict(r)
        t["amenities"] = json.loads(t["amenities"] or "[]")
        t["breakfast_included"] = bool(t["breakfast_included"])
        types.append(t)
    policies = [dict(r) for r in conn.execute(
        "SELECT title, content, category FROM policies WHERE hotel_id = ? ORDER BY id", (h["id"],))]
    hotel = {k: h[k] for k in h.keys() if k != "id"}
    return {"hotel": hotel, "room_types": types, "policies": policies}


class SearchIn(BaseModel):
    check_in: date
    check_out: date
    adults: int = Field(2, ge=1, le=20)
    children: int = Field(0, ge=0, le=10)


@router.post("/hotels/{slug}/search")
def search(slug: str, body: SearchIn, conn=Depends(get_conn)):
    h = hotel_or_404(conn, slug)
    expire_holds(conn)
    ctx = ToolContext(conn=conn, hotel_id=h["id"])
    try:
        return check_availability(ctx, body.model_dump(mode="json"))
    except ToolError as e:
        raise HTTPException(400, str(e))


class BookIn(SearchIn):
    room_type_code: str
    guest_name: str = Field(min_length=2, max_length=100)
    phone: str = Field(min_length=9, max_length=20)
    email: str = Field("", max_length=120)
    note: str = Field("", max_length=500)


@router.post("/hotels/{slug}/bookings")
def book(slug: str, body: BookIn, conn=Depends(get_conn)):
    """Direct booking from the website: creates a hold that staff confirm (same flow as AI)."""
    h = hotel_or_404(conn, slug)
    phone = re.sub(r"[^\d+]", "", body.phone)
    if not re.fullmatch(r"\+?\d{9,12}", phone):
        raise HTTPException(400, "Số điện thoại không hợp lệ")
    if body.check_in < date.today() or body.check_out <= body.check_in:
        raise HTTPException(400, "Ngày không hợp lệ")
    rt = conn.execute("SELECT * FROM room_types WHERE hotel_id = ? AND code = ?",
                      (h["id"], body.room_type_code)).fetchone()
    if not rt:
        raise HTTPException(404, "Loại phòng không tồn tại")
    if body.adults > rt["max_adults"] or body.children > rt["max_children"]:
        raise HTTPException(400, "Số khách vượt sức chứa của loại phòng")
    try:
        b = create_booking(conn, h["id"], room_type_id=rt["id"], check_in=body.check_in, check_out=body.check_out,
                           guest_name=body.guest_name.strip(), phone=phone, email=body.email.strip(),
                           adults=body.adults, children=body.children, status="hold", channel="website",
                           note=body.note)
    except BookingError as e:
        raise HTTPException(409, str(e))
    return {"code": b["code"], "status": b["status"], "room_type": rt["name"], "check_in": b["check_in"],
            "check_out": b["check_out"], "total_price": b["total_price"], "hold_expires_at": b["hold_expires_at"]}


# ------------------------------------------------------------------ chat

class ChatIn(BaseModel):
    hotel: str
    session_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2000)
    channel: str = Field("web", max_length=20)
    # Dates/party currently selected in the website's search form (fallback when the chat has none).
    page_search: SearchIn | None = None


def _last_id(conn, hotel_id: int, session_id: str) -> int:
    return conn.execute("SELECT COALESCE(MAX(id), 0) FROM messages WHERE hotel_id = ? AND session_id = ?",
                        (hotel_id, session_id)).fetchone()[0]


@router.post("/chat")
def chat(body: ChatIn, conn=Depends(get_conn)):
    h = hotel_or_404(conn, body.hotel)
    session = ensure_session(conn, h["id"], body.session_id, channel=body.channel)
    text = body.message.strip()
    if session["mode"] == "staff":
        # A human is handling this conversation: store the message, the AI stays quiet.
        add_message(conn, h["id"], body.session_id, "user", text)
        return {"reply": None, "mode": "staff", "trace": [], "latency_s": 0,
                "last_id": _last_id(conn, h["id"], body.session_id)}
    expire_holds(conn)
    try:
        page_stay = body.page_search.model_dump(mode="json") if body.page_search else None
        res = ReceptionistAgent(conn, llm).run(h["id"], body.session_id, text, channel="ai-chat", page_stay=page_stay)
    except LLMError as e:
        raise HTTPException(503, str(e))
    mode = get_session(conn, h["id"], body.session_id)["mode"]
    return {"reply": res.reply, "trace": res.trace, "latency_s": res.latency_s, "mode": mode,
            "last_id": _last_id(conn, h["id"], body.session_id)}


@router.get("/chat/{slug}/{session_id}/messages")
def chat_messages(slug: str, session_id: str, after: int = 0, conn=Depends(get_conn)):
    """Polled by the widget to receive staff replies."""
    h = hotel_or_404(conn, slug)
    s = get_session(conn, h["id"], session_id)
    return {"mode": s["mode"] if s else "ai", "messages": list_messages(conn, h["id"], session_id, after)}


@router.post("/chat/reset")
def chat_reset(body: dict, conn=Depends(get_conn)):
    h = hotel_or_404(conn, body.get("hotel", ""))
    ReceptionistAgent(conn, llm).reset(h["id"], str(body.get("session_id", "")))
    return {"ok": True}


@router.get("/time")
def server_time():
    return {"now": datetime.now().isoformat(timespec="seconds"), "today": date.today().isoformat()}
