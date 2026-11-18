"""Receptionist agent: an OpenAI-compatible tool-calling loop.

Works with any self-hosted server exposing /v1/chat/completions with tools:
Ollama, LM Studio, llama.cpp server, vLLM.
"""
import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

import httpx

from .config import settings
from .booking_flow import pick_reply, picks_room, ready_to_book, try_hold
from .grounding import availability_answer, looks_english, money_numbers, ungrounded_amounts
from .hints import PHONE_RE, extract_dates, known_stay, with_hint
from .prompts import build_system_prompt
from .sessions import add_message, ensure_session
from .tools import TOOL_SPECS, ToolContext, execute_tool


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, base_url: str | None = None, model: str | None = None, api_key: str | None = None):
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        self.api_key = api_key or settings.llm_api_key
        self._http = httpx.Client(timeout=settings.llm_timeout)

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        payload = {"model": self.model, "messages": messages, "temperature": settings.llm_temperature, "stream": False}
        if tools:
            payload["tools"] = tools
        try:
            r = self._http.post(f"{self.base_url}/chat/completions", json=payload,
                                headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as e:
            raise LLMError(f"Không kết nối được LLM server tại {self.base_url} ({e}). Ollama đã chạy chưa?") from e
        if r.status_code != 200:
            raise LLMError(f"LLM server trả lỗi {r.status_code}: {r.text[:300]}")
        return r.json()["choices"][0]["message"]

    def ping(self) -> dict:
        try:
            r = self._http.get(f"{self.base_url}/models", timeout=5)
            models = [m["id"] for m in r.json().get("data", [])]
            return {"ok": True, "models": models, "model_ready": self.model in models}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}


# Small models sometimes print the tool call as text instead of using tool_calls.
_TOOL_TAG = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def _fallback_tool_calls(content: str) -> list[dict]:
    candidates = _TOOL_TAG.findall(content or "")
    if not candidates:
        stripped = (content or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        if stripped.startswith("{") and '"name"' in stripped:
            candidates = [stripped]
    calls = []
    for c in candidates:
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "name" in obj:
            calls.append({"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function",
                          "function": {"name": obj["name"],
                                       "arguments": json.dumps(obj.get("arguments") or obj.get("parameters") or {},
                                                               ensure_ascii=False)}})
    return calls


@dataclass
class AgentResult:
    reply: str
    trace: list[dict] = field(default_factory=list)
    latency_s: float = 0.0


class ReceptionistAgent:
    def __init__(self, conn: sqlite3.Connection, llm: LLMClient | None = None):
        self.conn = conn
        self.llm = llm or LLMClient()
        self._mem_context: dict = {}  # search context when messages are not persisted (eval)

    def _history(self, hotel_id: int, session_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT role, content FROM messages WHERE hotel_id = ? AND session_id = ? ORDER BY id DESC LIMIT ?",
            (hotel_id, session_id, settings.history_messages)).fetchall()
        out = []
        for r in reversed(rows):
            if r["role"] == "staff":  # what a human receptionist said is part of the receptionist side
                out.append({"role": "assistant", "content": f"(Nhân viên) {r['content']}"})
            else:
                out.append({"role": r["role"], "content": r["content"]})
        return out

    def _get_context(self, hotel_id: int, session_id: str, persisted: bool) -> dict | None:
        if not persisted:
            return self._mem_context.get((hotel_id, session_id))
        row = self.conn.execute("SELECT context FROM chat_sessions WHERE hotel_id = ? AND session_id = ?",
                                (hotel_id, session_id)).fetchone()
        return json.loads(row["context"]) if row and row["context"] else None

    def _set_context(self, hotel_id: int, session_id: str, ctx: dict, persisted: bool) -> None:
        if not persisted:
            self._mem_context[(hotel_id, session_id)] = ctx
            return
        self.conn.execute("UPDATE chat_sessions SET context = ? WHERE hotel_id = ? AND session_id = ?",
                          (json.dumps(ctx), hotel_id, session_id))
        self.conn.commit()

    def _remember_search(self, hotel_id: int, session_id: str, ctx: ToolContext, result: dict, args: dict,
                         persisted: bool) -> dict:
        search_ctx = {k: result.get(k) for k in ("check_in", "check_out", "adults", "children")}
        search_ctx["room_type_code"] = args.get("room_type_code")
        self._set_context(hotel_id, session_id, search_ctx, persisted)
        ctx.search_context = search_ctx
        return search_ctx

    def _answer_from_page(self, hotel_id: int, session_id: str, ctx: ToolContext, page_stay: dict,
                          trace: list[dict], persisted: bool, english: bool) -> str:
        args = {k: page_stay.get(k) for k in ("check_in", "check_out", "adults", "children")}
        gave, ctx.guest_gave_dates = ctx.guest_gave_dates, True
        result = execute_tool(ctx, "check_availability", args)
        ctx.guest_gave_dates = gave
        trace.append({"tool": "check_availability", "args": {**args, "_by": "code", "_from": "page_search"},
                      "result": result})
        if "options" not in result:
            return ""  # e.g. a past date in the form: let the model ask the guest
        self._remember_search(hotel_id, session_id, ctx, result, args, persisted)
        ctx.guest_gave_dates = True
        lines = availability_answer(result, "en" if english else "vi", ctx.today).split("\n")
        note = ("(Checked for the dates selected on the page — tell me if you want other dates or guests.)"
                if english else
                "(Em tra theo ngày và số khách anh/chị đang chọn trên trang. Muốn đổi ngày hoặc số người, "
                "anh/chị nhắn em nhé.)")
        return "\n".join(lines[:-1] + [note, lines[-1]])

    def reset(self, hotel_id: int, session_id: str) -> None:
        self.conn.execute("DELETE FROM messages WHERE hotel_id = ? AND session_id = ?", (hotel_id, session_id))
        self.conn.execute("DELETE FROM chat_sessions WHERE hotel_id = ? AND session_id = ?", (hotel_id, session_id))
        self.conn.commit()

    def run(self, hotel_id: int, session_id: str, user_message: str,
            today: date | None = None, now: datetime | None = None, save: bool = True,
            channel: str = "ai-chat", page_stay: dict | None = None) -> AgentResult:
        started = time.perf_counter()
        today = today or date.today()
        if save:  # must exist before tools run (handoff_to_staff switches its mode)
            ensure_session(self.conn, hotel_id, session_id)
        history = self._history(hotel_id, session_id)
        gave_dates = bool(extract_dates(user_message, today)) or any(
            m["role"] == "user" and extract_dates(m["content"], today) for m in history)
        search_ctx = self._get_context(hotel_id, session_id, save)
        gave_dates = gave_dates or bool(search_ctx)  # dates already settled by an earlier search
        ctx = ToolContext(conn=self.conn, hotel_id=hotel_id, session_id=session_id, today=today,
                          now=now or datetime.now(), guest_gave_dates=gave_dates, channel=channel,
                          search_context=search_ctx)
        hinted = with_hint(user_message, today, history, self.conn, hotel_id, search_ctx)
        messages = [{"role": "system", "content": build_system_prompt(self.conn, hotel_id, today)}]
        messages += history
        messages.append({"role": "user", "content": hinted})
        # Amounts the model may legitimately repeat: earlier turns (already checked) + the hint (policies).
        known_text = hinted + "\n" + "\n".join(m["content"] for m in history)
        trace: list[dict] = []
        reply = ""
        corrected = False
        rounds = settings.max_tool_rounds + 2

        if wants_human(user_message):
            # Explicit request for a person: always honour it, never let the model talk the guest out of it.
            result = execute_tool(ctx, "handoff_to_staff", {"reason": "Khách muốn gặp nhân viên", "summary": user_message})
            trace.append({"tool": "handoff_to_staff", "args": {"reason": "Khách muốn gặp nhân viên", "_by": "code"},
                          "result": result})
            reply = ("Of course. I've passed your conversation to our staff; they'll reply here shortly. "
                     f"For anything urgent please call {result['hotline']}." if looks_english(user_message) else
                     "Dạ vâng, em đã chuyển cuộc trò chuyện cho nhân viên, anh/chị chờ chút nhân viên sẽ trả lời ngay "
                     f"tại đây ạ. Nếu cần gấp anh/chị gọi hotline {result['hotline']} nhé.")
            rounds = 0
        elif kind := small_talk(user_message):
            # Pure greeting / thanks / goodbye: a short human answer. Small models otherwise go fetch policies
            # and dump amenities on a guest who only said hello.
            reply = small_talk_reply(kind, _english_small_talk(user_message), self.conn, hotel_id)
            trace.append({"tool": "system_smalltalk", "args": {"kind": kind},
                          "result": {"note": "Khách chỉ chào/cảm ơn → hệ thống trả lời ngắn, không gọi AI"}})
            rounds = 0
        elif (past := [d for _, d in extract_dates(user_message, today) if d < today]) and asks_availability(user_message):
            # Rooms for a date that already passed: say so plainly (never search next year's date instead).
            reply = past_date_reply(past[0], today, looks_english(user_message))
            trace.append({"tool": "system_past_date", "args": {"date": past[0].isoformat()},
                          "result": {"note": "Khách hỏi phòng cho ngày đã qua → hệ thống báo và hỏi ngày khác"}})
            rounds = 0
        elif ready_to_book(ctx, user_message):
            # Booking step with all slots known: done in code — reliable and fast, no model call needed.
            reply, entry = try_hold(ctx, user_message, english=looks_english(user_message))
            trace.append({"tool": "system_booking", "args": {},
                          "result": {"note": "Khách đã đủ thông tin đặt phòng → hệ thống tự giữ phòng"}})
            trace.append(entry)
            rounds = 0  # skip the model loop
        elif code := picks_room(ctx, user_message):
            # Guest chose a room type after a search ("giữ phòng Deluxe"): confirm it from fresh data, remember
            # the choice, and ask for name + phone (the next message then books it in code).
            args = {k: search_ctx.get(k) for k in ("check_in", "check_out", "adults", "children")}
            result = execute_tool(ctx, "check_availability", args)
            trace.append({"tool": "check_availability", "args": {**args, "_by": "code"}, "result": result})
            if "options" in result:
                opt = next((o for o in result["options"] if o["room_type_code"] == code), None)
                self._remember_search(hotel_id, session_id, ctx, result,
                                      {"room_type_code": code if opt and opt.get("fits_party") else None}, save)
                reply = pick_reply(result, code, looks_english(user_message))
                trace.append({"tool": "system_pick_room", "args": {"room_type_code": code},
                              "result": {"note": "Khách chọn loại phòng → hệ thống xác nhận và xin tên/SĐT"}})
                rounds = 0
        elif search_ctx and wants_booking(user_message) and not PHONE_RE.search(user_message):
            # "Giúp mình đặt phòng" after a search but no room chosen yet: list what's free and ask for the choice
            # + name + phone (small models answer "em sẽ kiểm tra ngay" and then do nothing).
            args = {k: search_ctx.get(k) for k in ("check_in", "check_out", "adults", "children")}
            result = execute_tool(ctx, "check_availability", args)
            trace.append({"tool": "check_availability", "args": {**args, "_by": "code"}, "result": result})
            if "options" in result:
                english = looks_english(user_message)
                lines = availability_answer(result, "en" if english else "vi", today).split("\n")
                if any(o.get("fits_party") for o in result["options"]):
                    lines[-1] = ("Which room would you like? Please also send your full name and phone number."
                                 if english else
                                 "Anh/chị chọn loại phòng nào, kèm họ tên và số điện thoại để em giữ phòng nhé ạ?")
                reply = "\n".join(lines)
                trace.append({"tool": "system_booking_start", "args": {},
                              "result": {"note": "Khách muốn đặt nhưng chưa chọn phòng → liệt kê phòng trống, xin thông tin"}})
                rounds = 0
        elif (not PHONE_RE.search(user_message) and asks_free_rooms(user_message)
              and (stay_now := known_stay(user_message, history, today, search_ctx))
              and not any(d < today for _, d in extract_dates(user_message, today))):
            # "Còn phòng / lịch nào trống?" with dates known from the chat: always answer from a fresh search.
            # Small models otherwise reply "em sẽ kiểm tra ngay" (or leak our notes) without searching.
            result = execute_tool(ctx, "check_availability", stay_now)
            trace.append({"tool": "check_availability", "args": {**stay_now, "_by": "code"}, "result": result})
            if "options" in result:
                self._remember_search(hotel_id, session_id, ctx, result, stay_now, save)
                reply = availability_answer(result, "en" if looks_english(user_message) else "vi", today)
                trace.append({"tool": "system_free_rooms", "args": {},
                              "result": {"note": "Khách hỏi phòng trống, đã biết ngày → hệ thống tự tra và trả lời"}})
                rounds = 0
        elif (page_stay and asks_availability(user_message) and not PHONE_RE.search(user_message)
              and not known_stay(user_message, history, today, search_ctx)):
            # Website chat: the guest gave no dates in the chat but has dates selected in the page's search
            # form. Answer from those (saying so) instead of asking for dates over and over.
            reply = self._answer_from_page(hotel_id, session_id, ctx, page_stay, trace, save,
                                           english=looks_english(user_message))
            if reply:
                rounds = 0

        for _ in range(rounds):
            msg = self.llm.chat(messages, TOOL_SPECS)
            content = msg.get("content") or ""
            tool_calls = msg.get("tool_calls") or _fallback_tool_calls(content)
            if not tool_calls:
                reply = _strip_think(content)
                if _LEAK_RE.search(reply):
                    # The model echoed our hidden notes / tool arguments to the guest: never show those.
                    trace.append({"tool": "system_guard", "args": {"problem": "leak"},
                                  "result": {"note": "AI để lộ ghi chú nội bộ → thay bằng câu trả lời an toàn"}})
                    last = _last_availability(trace)
                    stay = known_stay(user_message, history, today, search_ctx)
                    if not last and stay:
                        res = execute_tool(ctx, "check_availability", stay)
                        trace.append({"tool": "check_availability", "args": {**stay, "_by": "code"}, "result": res})
                        last = res if "options" in res else None
                    english = looks_english(user_message)
                    reply = (availability_answer(last, "en" if english else "vi", today) if last else
                             "How can I help you? For room availability, please tell me your dates and number of guests."
                             if english else
                             "Dạ anh/chị cần em hỗ trợ gì thêm ạ? Nếu muốn xem phòng trống, anh/chị cho em xin ngày "
                             "nhận, ngày trả phòng và số người nhé.")
                    if held := _successful_hold(trace):
                        reply = (f"Dạ em đã giữ phòng {held['room_type']} cho anh/chị. Mã giữ chỗ: {held['hold_code']} · "
                                 f"Tổng {held['total_price_text']}. Nhân viên sẽ liên hệ để xác nhận và nhận cọc ạ.")
                    break
                hold = _successful_hold(trace)
                problem, guard = None, ""
                last_search = _last_availability(trace)
                stay = known_stay(user_message, history, today, search_ctx)
                # Same stay already searched earlier in the chat: the model may answer from it (prices are still
                # checked by the price guard below). Only an unsearched stay is re-checked here.
                searched = bool(search_ctx and stay and all(
                    str(search_ctx.get(k)) == str(stay.get(k)) for k in ("check_in", "check_out", "adults")
                    if stay.get(k) is not None))
                if (not hold and not last_search and stay and not searched and not PHONE_RE.search(user_message)
                        and (talks_availability(reply) or asks_availability(user_message)
                             or money_numbers(reply)
                             # guest typed a date and the model used no tool at all: a date is a room question
                             or (extract_dates(user_message, today)
                                 and not any(not t["tool"].startswith("system_") for t in trace)))):
                    # Guest asked about rooms (or the model claimed rooms are/aren't free) but the model never
                    # looked: small models often just copy their previous answer. Check in code, answer from data.
                    lang = "en" if looks_english(user_message) else "vi"
                    result = execute_tool(ctx, "check_availability", stay)
                    trace.append({"tool": "check_availability", "args": {**stay, "_by": "code"}, "result": result})
                    if "options" in result:
                        self._remember_search(hotel_id, session_id, ctx, result, stay, save)
                        reply = availability_answer(result, lang, today)
                    else:
                        reply = ("Could you tell me your check-in and check-out dates and number of guests?"
                                 if lang == "en" else
                                 f"Dạ {result.get('error', '')} Anh/chị cho em xin lại ngày nhận, ngày trả phòng "
                                 "và số người nhé ạ?")
                    trace.append({"tool": "system_guard", "args": {"problem": "availability-unchecked"},
                                  "result": {"note": "AI nói còn/hết phòng mà không tra cứu → hệ thống tự tra và trả lời"}})
                elif (last_search and not hold and not PHONE_RE.search(user_message)
                      and asks_availability(user_message) and not claims_hold(reply)):
                    # Availability question answered after a search this turn: the template is always complete and
                    # correct (small models drop room types, misstate dates, or invent "today").
                    reply = availability_answer(last_search, "en" if looks_english(user_message) else "vi", today)
                    trace.append({"tool": "system_template", "args": {},
                                  "result": {"note": "Trả lời phòng trống bằng mẫu từ kết quả tra cứu"}})
                elif claims_hold(reply) and not hold and not PHONE_RE.search(user_message) and last_search:
                    # Guest only asked about rooms; "I'll hold it" is an offer, but must not sound done.
                    # Re-prompting confuses small models, so answer from the search data directly.
                    reply = availability_answer(last_search, "en" if looks_english(user_message) else "vi", today)
                    trace.append({"tool": "system_guard", "args": {"problem": "claim-offer"},
                                  "result": {"note": "AI nói giữ phòng khi khách chưa cho thông tin → dùng câu trả lời mẫu"}})
                elif claims_hold(reply) and not hold:
                    problem, guard = "claim", _claim_guard(search_ctx, user_message)
                elif bad := ungrounded_amounts(reply, trace, known_text):
                    problem = "price"
                    guard = (f"[Ghi chú hệ thống — khách không thấy] Các số tiền {sorted(bad)} KHÔNG có trong kết quả công cụ. "
                             "Không tự tính hay ước lượng giá. Chỉ dùng đúng các số *_text trong kết quả công cụ "
                             "(vd total_per_room_text). Viết lại câu trả lời.")
                if problem and not corrected:
                    # First violation: tell the model and let it retry once.
                    corrected = True
                    trace.append({"tool": "system_guard", "args": {"problem": problem}, "result": {"note": guard}})
                    messages.append({"role": "assistant", "content": reply})
                    messages.append({"role": "user", "content": guard})
                    continue
                if problem == "claim":
                    # Model still can't call the tool: do the booking step in code (or ask for missing slots).
                    reply, entry = try_hold(ctx, user_message, english=looks_english(user_message))
                    trace.append({"tool": "system_guard", "args": {"problem": "claim"},
                                  "result": {"note": "AI không gọi được create_booking_hold → hệ thống tự xử lý bước giữ phòng"}})
                    if entry:
                        trace.append(entry)
                    hold = _successful_hold(trace)
                elif problem == "price":
                    last = _last_availability(trace)
                    reply = (availability_answer(last, "en" if looks_english(user_message) else "vi", today) if last else
                             "Dạ để báo giá chính xác, anh/chị cho em xin ngày nhận phòng, ngày trả phòng và số người ạ?")
                    trace.append({"tool": "system_guard", "args": {"problem": "price"},
                                  "result": {"note": "AI vẫn đưa số tiền sai → thay bằng câu trả lời mẫu từ dữ liệu"}})
                if hold and hold["hold_code"] not in reply:
                    reply += f"\nMã giữ chỗ: {hold['hold_code']} · Tổng {hold['total_price_text']}."
                break
            messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})
            for call in tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments") or "{}"
                args = raw_args if isinstance(raw_args, dict) else _safe_json(raw_args)
                result = execute_tool(ctx, name, args)
                trace.append({"tool": name, "args": args, "result": result})
                if name == "check_availability" and "error" not in result:
                    search_ctx = self._remember_search(hotel_id, session_id, ctx, result, args, save)
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "name": name,
                                 "content": json.dumps(result, ensure_ascii=False)})
        else:
            if rounds:  # loop ran out of rounds without a final answer
                reply = "Xin lỗi anh/chị, em đang gặp chút trục trặc. Em đã báo nhân viên hỗ trợ ngay ạ."
                execute_tool(ctx, "handoff_to_staff", {"reason": "AI vượt quá số lần gọi công cụ",
                                                       "summary": user_message})

        reply = reply or "Dạ anh/chị cần em hỗ trợ thêm gì ạ?"
        latency = round(time.perf_counter() - started, 2)
        if save:
            ensure_session(self.conn, hotel_id, session_id)
            add_message(self.conn, hotel_id, session_id, "user", user_message)
            add_message(self.conn, hotel_id, session_id, "assistant", reply, trace=trace, latency_s=latency)
        return AgentResult(reply=reply, trace=trace, latency_s=latency)


_HUMAN_RE = re.compile(
    r"\b(gap|noi chuyen voi|lien he|chuyen|cho minh|cho toi|can)\s+(nhan vien|nguoi that|quan ly|le tan that|admin)\b"
    r"|\b(talk|speak|chat) (to|with) (a |an )?(human|person|staff|manager|real person)\b|\breal (human|person)\b")


def wants_human(text: str) -> bool:
    from .tools import normalize
    return bool(_HUMAN_RE.search(normalize(text)))


_GREET = {"chao", "xin", "hello", "hi", "hey", "alo", "alo", "helo", "hellu", "good", "morning", "afternoon",
          "evening", "shop", "ad", "admin", "le", "tan"}
_THANKS = {"cam", "on", "thanks", "thank", "thx", "tks", "camon", "nhieu", "much", "so", "very"}
_BYE = {"tam", "biet", "bye", "goodbye", "see", "later", "hen", "gap", "lai"}
_FILLER = {"ban", "em", "anh", "chi", "a", "ah", "nhe", "nha", "nhe", "oi", "you", "there", "khach", "san",
           "minh", "toi", "nhiu", "nhe", "ok", "oke", "okay", "vang", "da", "u", "uk", "uh", "nhieu"}


def small_talk(text: str) -> str | None:
    """'greet' / 'thanks' / 'bye' when the message is nothing but that (no question or request), else None."""
    from .tools import normalize
    words = re.findall(r"[a-z]+", normalize(text))
    if not words or len(words) > 8 or re.search(r"\d", text):
        return None
    allowed = _GREET | _THANKS | _BYE | _FILLER
    if any(w not in allowed for w in words):
        return None
    t = " ".join(words)
    if re.search(r"\b(cam on|camon|thanks?|thx|tks)\b", t):
        return "thanks"
    if re.search(r"\b(tam biet|bye|goodbye|see you|hen gap)\b", t):
        return "bye"
    if re.search(r"\b(chao|hello|hi|hey|alo|helo|hellu|good (morning|afternoon|evening))\b", t):
        return "greet"
    return None


def past_date_reply(d: date, today: date, english: bool) -> str:
    from .prompts import WEEKDAYS_VI
    if english:
        return (f"Sorry, {d:%d/%m/%Y} has already passed (today is {today:%d/%m/%Y}), so it can't be booked. "
                "Which date would you like to check instead?")
    return (f"Dạ ngày {d:%d/%m/%Y} đã qua rồi ạ (hôm nay là {WEEKDAYS_VI[today.weekday()]} {today:%d/%m/%Y}), "
            "nên bên em không thể tra hay đặt phòng cho ngày này. Anh/chị muốn xem phòng ngày nào khác ạ?")


def _english_small_talk(text: str) -> bool:
    """English only when typed in ASCII with English words and no (unaccented) Vietnamese ones."""
    words = set(re.findall(r"[a-z]+", text.lower()))
    return (text.isascii() and bool(words & {"hello", "hi", "hey", "thanks", "thank", "thx", "bye", "goodbye", "good"})
            and not words & {"chao", "ban", "em", "anh", "chi", "cam", "on", "alo", "shop", "nhe", "nha", "a", "oi"})


def small_talk_reply(kind: str, english: bool, conn: sqlite3.Connection, hotel_id: int) -> str:
    name = conn.execute("SELECT name FROM hotels WHERE id = ?", (hotel_id,)).fetchone()["name"]
    if english:
        return {"greet": f"Hello! This is the virtual receptionist of {name}. How can I help you? "
                         "For room availability, just tell me your dates and number of guests.",
                "thanks": "You're welcome! Let me know if there's anything else I can help with.",
                "bye": "Thank you for contacting us. Have a nice day!"}[kind]
    return {"greet": f"Dạ em chào anh/chị, em là lễ tân ảo của {name}. Anh/chị cần em hỗ trợ gì ạ? "
                     "Nếu muốn xem phòng trống, anh/chị cho em xin ngày nhận, ngày trả phòng và số người nhé.",
            "thanks": "Dạ không có gì ạ. Anh/chị cần em hỗ trợ thêm gì cứ nhắn em nhé!",
            "bye": "Dạ em cảm ơn anh/chị đã liên hệ. Chúc anh/chị một ngày vui vẻ ạ!"}[kind]


def _claim_guard(search_ctx: dict | None, user_message: str) -> str:
    text = ("[Ghi chú hệ thống — khách không thấy] Bạn vừa nói đã giữ/đặt phòng nhưng CHƯA gọi create_booking_hold, "
            "nên chưa có phòng nào được giữ.")
    phone = PHONE_RE.search(user_message)
    if phone:
        rt = (search_ctx or {}).get("room_type_code") or "<mã loại phòng khách chọn>"
        text += (f" Hãy gọi create_booking_hold(room_type_code=\"{rt}\", guest_name=\"<tên khách>\", "
                 f"phone=\"{re.sub(r'[^0-9+]', '', phone.group(0))}\") — ngày tự lấy từ lần tra gần nhất.")
    text += " Nếu thiếu tên hoặc loại phòng thì hỏi khách. Không được nói đã giữ phòng khi chưa có hold_code."
    return text


def _last_availability(trace: list[dict]) -> dict | None:
    for t in reversed(trace):
        if t["tool"] == "check_availability" and isinstance(t["result"], dict) and "options" in t["result"]:
            return t["result"]
    return None

_CLAIM_RE = re.compile(
    r"\b(da|se|vua)\s+(giu|dat)\s+(cho|phong|tam)|giu cho tam|ma giu cho|ma dat phong|dat phong thanh cong"
    r"|i (have|ve|will) (held|hold|reserved|reserve|booked|book)|reserved for you|booking (is )?confirmed")


def claims_hold(reply: str) -> bool:
    """True if a non-question sentence says a room was (or will be) held/booked."""
    from .tools import normalize
    sentences = re.split(r"(?<=[.!?\n])", normalize(reply))
    return any(_CLAIM_RE.search(s) for s in sentences if not s.strip().endswith("?"))


_AVAIL_RE = re.compile(
    r"\b(con|het|chua co|khong co|khong con|van con)\s+(phong|cho)\b|\bphong\s+(trong|con trong)\b|\bhet phong\b"
    r"|fully booked|sold out|\bno rooms?\b|\brooms? (are |is )?(available|left)\b|\bavailable rooms?\b|\bwe have\b")


def talks_availability(reply: str) -> bool:
    """True if a non-question sentence states whether rooms are free (or promises to check)."""
    from .tools import normalize
    sentences = re.split(r"(?<=[.!?\n])", normalize(reply))
    return any(_AVAIL_RE.search(s) for s in sentences if not s.strip().endswith("?"))


_ASK_AVAIL_RE = re.compile(
    r"\b(con|het)\s+(phong|cho|trong)\b|\bphong\s+(nao\s+)?(trong|con)\b|\bloai phong\b|\bphong nao\b"
    r"|\bcon\s+(ngay|hom|toi|dem|thu|cuoi tuan|tuan)\b"
    r"|\bliet ke\b|\b(dat|book|giu)\s+phong\b|\bmuon dat\b|\bgia phong\b|\bbao nhieu tien\b"
    r"|\bavailab|\bvacanc|\bany rooms?\b|\bwhat rooms?\b|\brooms? (left|free)\b|\bbook a room\b|\bhow much\b")


_WANTS_BOOKING_RE = re.compile(r"\b(dat|giu|book|reserve)\s+(phong|cho|giup|dum|ho|a room|the room)\b"
                               r"|\b(muon|can|giup minh|giup toi)\s+(dat|giu|book)\b")


# Hidden hint text or raw tool arguments that must never reach the guest.
_LEAK_RE = re.compile(r"\b(check_in|check_out|room_type_code|adults|children|guest_name)\s*=|Ghi chú hệ thống"
                      r"|create_booking_hold|check_availability|search_policies|handoff_to_staff", re.I)

_FREE_ROOMS_RE = re.compile(
    r"\b(con|het)\s+(phong|cho|trong)\b|\bphong\s+(nao\s+)?(trong|con)\b|\b(lich|ngay|cho)\s+(nao\s+)?(trong|con trong)\b"
    r"|\bcon\s+(ngay|hom|toi|dem|thu|cuoi tuan|tuan)\b|\bco phong (nao|khong|ko)\b(?!.*\b(view|huong|giuong|ban cong|bon tam|rong)\b)"
    r"|\bany rooms?\b|\brooms? (left|free|available)\b|\bavailab")


def asks_free_rooms(text: str) -> bool:
    """Narrow: the guest asks which rooms are free (not room features, prices of a type, or booking)."""
    from .tools import normalize
    return bool(_FREE_ROOMS_RE.search(normalize(text)))


def wants_booking(text: str) -> bool:
    """Guest asks to book (without naming a room type: picks_room handles that case first)."""
    from .tools import normalize
    return bool(_WANTS_BOOKING_RE.search(normalize(text)))


def asks_availability(text: str) -> bool:
    """Guest asks which rooms are free / wants to book / asks the price."""
    from .tools import normalize
    return bool(_ASK_AVAIL_RE.search(normalize(text)))


def _successful_hold(trace: list[dict]) -> dict | None:
    for t in reversed(trace):
        if t["tool"] == "create_booking_hold" and isinstance(t["result"], dict) and "hold_code" in t["result"]:
            return t["result"]
    return None


def _safe_json(s: str) -> dict:
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _strip_think(text: str) -> str:
    """Remove <think>...</think> blocks emitted by reasoning models (e.g. Qwen3)."""
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
