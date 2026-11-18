"""Deterministic pre-processing of the guest's message.

Small models (3B) are bad at turning "tối thứ 6 này" into a date and often skip
tools. So before the model sees a message we:
  1. resolve date expressions in code (thứ 6, mai, 20/11, cuối tuần, 2 đêm...)
  2. tell the model to ask for dates when none were given
  3. pre-fetch the most relevant policies (RAG-lite)
The hint is appended to the user turn sent to the LLM only (not stored in history).
The fine-tune dataset uses the same function so training matches production.
"""
import re
import sqlite3
from datetime import date, timedelta

from .prompts import WEEKDAYS_VI

STOPWORDS = set("""
co khong ko k la gi nao the cho minh em anh chi ban duoc khach san a ah vay oi shop ad voi va thi se cua o nhe nha
ok oke hoi toi muon can bao nhieu roi di ve tai trong mot nhu hay cac nhung nay do de khi neu sao mang theo nho
phong con gia dat check giup xin cam on ha hem nhi u uh dc j
the a an is are do does you have can i my any there what how much please hi hello
""".split())

_WD_WORDS = {"2": 0, "hai": 0, "3": 1, "ba": 1, "4": 2, "tu": 2, "5": 3, "nam": 3, "6": 4, "sau": 4, "7": 5, "bay": 5}
_EN_WD = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
_NUM_WORDS = {"mot": 1, "hai": 2, "ba": 3, "bon": 4, "nam": 5, "one": 1, "two": 2, "three": 3, "four": 4}


def _norm(text: str) -> str:
    from .tools import normalize
    return normalize(text)


def _next_weekday(today: date, wd: int, next_week: bool) -> date:
    if next_week:  # the given weekday inside next Monday-based week
        monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
        return monday + timedelta(days=wd)
    return today + timedelta(days=(wd - today.weekday()) % 7)


RECENT_PAST_DAYS = 90  # "12/11" asked on 17/11 = this year's (past) 12/11; "5/1" asked in November = next year


def _make_date(today: date, day: int, month: int, year: int | None) -> date | None:
    try:
        if year:
            return date(year + 2000 if year < 100 else year, month, day)
        d = date(today.year, month, day)
        if d >= today or (today - d).days <= RECENT_PAST_DAYS:
            return d  # a date a few days/weeks ago is a past date, not next year (guest must be told it passed)
        return date(today.year + 1, month, day)
    except ValueError:
        return None


def extract_dates(text: str, today: date) -> list[tuple[str, date]]:
    """Return [(phrase, date)] in order of appearance."""
    t = _norm(text)
    found: list[tuple[int, str, date]] = []

    for m in re.finditer(r"\b(\d{1,2})\s*[/\-.]\s*(\d{1,2})(?:\s*[/\-.]\s*(\d{2,4}))?\b", t):
        d = _make_date(today, int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None)
        if d:
            found.append((m.start(), m.group(0), d))
    for m in re.finditer(r"\bngay\s+(\d{1,2})\s+thang\s+(\d{1,2})\b", t):
        d = _make_date(today, int(m.group(1)), int(m.group(2)), None)
        if d:
            found.append((m.start(), m.group(0), d))
    for pat, delta in ((r"\b(hom nay|toi nay|dem nay|today|tonight)\b", 0),
                       (r"\b(ngay mai|toi mai|sang mai|tomorrow)\b|(?<!ngay )\bmai\b", 1),
                       (r"\b(ngay kia|ngay mot|toi mot)\b", 2)):
        for m in re.finditer(pat, t):
            found.append((m.start(), m.group(0), today + timedelta(days=delta)))
    for m in re.finditer(r"\b(?:thu\s*(2|3|4|5|6|7|hai|ba|tu|nam|sau|bay)\b|(chu nhat|cn)\b)(\s*(?:tuan\s*)?(sau|toi|nay))?", t):
        wd = 6 if m.group(2) else _WD_WORDS[m.group(1)]
        found.append((m.start(), m.group(0).strip(), _next_weekday(today, wd, m.group(4) in ("sau", "toi"))))
    for m in re.finditer(r"\b(next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t):
        found.append((m.start(), m.group(0), _next_weekday(today, _EN_WD[m.group(2)], bool(m.group(1)))))
    if not found:  # "cuối tuần" only when no explicit weekday was given
        for m in re.finditer(r"\b(cuoi tuan( nay| sau)?|this weekend|weekend)\b", t):
            found.append((m.start(), m.group(0), _next_weekday(today, 5, "sau" in m.group(0))))

    found.sort(key=lambda x: x[0])
    out, seen = [], set()
    for _, phrase, d in found:
        if (phrase, d) not in seen:
            seen.add((phrase, d))
            out.append((phrase, d))
    return out


def extract_nights(text: str) -> int | None:
    t = _norm(text)
    m = re.search(r"\b(\d{1,2}|mot|hai|ba|bon|nam|one|two|three|four)\s*(dem|night|nights|ngay dem)\b", t)
    if not m:
        return None
    v = m.group(1)
    return int(v) if v.isdigit() else _NUM_WORDS.get(v)


_NUM = r"(\d{1,2}|mot|hai|ba|bon|nam|sau|one|two|three|four|five|six)"
_NUM_MORE = {**_NUM_WORDS, "sau": 6, "five": 5, "six": 6}
_SOLO_RE = re.compile(r"\b(mot minh|1 minh|(chi|chi co|co) minh (toi|em|anh|chi)|minh (toi|em) thoi|"
                      r"1 nguoi|mot nguoi|alone|just me|only me|myself)\b")
_ADULTS_RE = re.compile(_NUM + r"\s*(nguoi lon|nguoi|khach|adults?|people|persons?|guests?)\b")
_CHILDREN_RE = re.compile(_NUM + r"\s*(tre em|tre|be|chau|kids?|children|child)\b")


def _num(v: str) -> int:
    return int(v) if v.isdigit() else _NUM_MORE[v]


def extract_party(text: str) -> tuple[int, int] | None:
    """(adults, children) if the message says how many guests, else None."""
    t = _norm(text)
    adults = children = None
    if m := _ADULTS_RE.search(t):
        adults = _num(m.group(1))
    elif _SOLO_RE.search(t):
        adults = 1
    if m := _CHILDREN_RE.search(t):
        children = _num(m.group(1))
    if adults is None and children is None:
        return None
    return adults or 1, children or 0


def known_stay(text: str, history: list[dict], today: date, context: dict | None = None) -> dict | None:
    """Stay the guest already gave somewhere in the conversation (latest wins), as check_availability args."""
    user_msgs = [text] + [m["content"] for m in reversed(history) if m["role"] == "user"]
    stay: dict | None = None
    for msg in user_msgs:
        dates = extract_dates(msg, today)
        if dates:
            check_in = dates[0][1]
            nights = extract_nights(msg)
            if len(dates) > 1 and dates[1][1] > check_in:
                check_out = dates[1][1]
            else:
                check_out = check_in + timedelta(days=nights or 1)
            stay = {"check_in": check_in.isoformat(), "check_out": check_out.isoformat()}
            break
    if stay is None and context and context.get("check_in"):
        stay = {k: context[k] for k in ("check_in", "check_out") if context.get(k)}
    if stay is None:
        return None
    party = next((p for p in map(extract_party, user_msgs) if p), None)
    if party:
        stay["adults"], stay["children"] = party
    elif context and context.get("adults"):
        stay["adults"], stay["children"] = context["adults"], context.get("children") or 0
    if context and context.get("room_type_code"):
        stay["room_type_code"] = context["room_type_code"]
    return stay


# Guest asks to see/book rooms or prices (as opposed to a question about the hotel, its rules or services).
ROOM_ASK_RE = re.compile(r"\b(con|het)\s+(phong|cho|trong)\b|\bphong\s+(nao\s+)?(trong|con)\b|\b(dat|book|giu)\s+phong\b"
                         r"|\bgia phong\b|\bbao nhieu tien\b|\bavailab|\bany rooms?\b|\bbook a room\b")
ROOM_INTENT_RE = re.compile(r"\b(phong|dat|book|room|available|vacan|het cho|con cho)")


def related_policies(conn: sqlite3.Connection, hotel_id: int, text: str, limit: int = 2) -> list[dict]:
    tokens = [x for x in re.findall(r"[a-z0-9]+", _norm(text)) if len(x) > 1 and x not in STOPWORDS]
    if not tokens:
        return []
    scored = []
    for p in conn.execute("SELECT title, content, keywords, category FROM policies WHERE hotel_id = ?", (hotel_id,)):
        words = set(re.findall(r"[a-z0-9]+", " ".join([_norm(p["title"]), _norm(p["keywords"] or ""), p["category"]])))
        score = sum(1 for tok in tokens if tok in words)
        if score:
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    return [{"title": p["title"], "content": p["content"]} for _, p in scored[:limit]]


def _fmt(d: date) -> str:
    return f"{WEEKDAYS_VI[d.weekday()]} {d.strftime('%d/%m/%Y')} = {d.isoformat()}"


PHONE_RE = re.compile(r"(?:\+?84|0)(?:[\s.\-]?\d){9}\b")
BOOK_INTENT_RE = re.compile(r"\b(dat|giu|chot|lay|book|reserve|ok|dong y|yes)\b")


def booking_hint(text: str, context: dict | None) -> list[str]:
    """When the guest gives a phone number (usually 'book it, name X, phone Y'), spell out the hold call."""
    if not PHONE_RE.search(text):
        return []
    lines = ["Khách vừa cung cấp số điện thoại → có vẻ muốn ĐẶT/GIỮ PHÒNG."]
    if context:
        args = ", ".join(f"{k}={v}" for k, v in context.items() if v not in (None, ""))
        lines.append(f"Lần tra phòng gần nhất trong hội thoại: {args}. Nếu khách đã chọn loại phòng và có tên, "
                     "hãy GỌI create_booking_hold với các thông số này + guest_name + phone.")
    lines.append("CHỈ được nói 'đã giữ phòng' sau khi create_booking_hold trả về hold_code, và phải báo mã đó cho khách. "
                 "Thiếu tên/loại phòng/ngày thì hỏi lại.")
    return lines


def build_hint(text: str, today: date, history_has_dates: bool = False,
               conn: sqlite3.Connection | None = None, hotel_id: int | None = None,
               context: dict | None = None, stay: dict | None = None) -> str:
    dates = extract_dates(text, today)
    nights = extract_nights(text)
    lines = booking_hint(text, context)
    party = extract_party(text)
    if party:
        lines.append(f"Số khách: {party[0]} người lớn, {party[1]} trẻ em → adults={party[0]}, children={party[1]}.")
    if dates:
        parts = [f"'{p}' → {_fmt(d)}" for p, d in dates]
        lines.append("Ngày trong tin nhắn (hệ thống đã tính sẵn, dùng đúng ngày này): " + "; ".join(parts) + ".")
        if len(dates) == 1 and nights:
            lines.append(f"Ở {nights} đêm → check_in={dates[0][1].isoformat()}, "
                         f"check_out={(dates[0][1] + timedelta(days=nights)).isoformat()}.")
        elif len(dates) == 1 and not nights:
            lines.append("Khách chưa nói số đêm: nếu khách hỏi phòng, mặc định 1 đêm hoặc hỏi lại.")
        past = [(p, d) for p, d in dates if d < today]
        if past:
            lines.append("NGÀY ĐÃ QUA: " + ", ".join(f"'{p}' = {d:%d/%m/%Y}" for p, d in past)
                         + f" (hôm nay {today:%d/%m/%Y}). Không thể đặt hay tra phòng ngày đã qua: báo khách và hỏi ngày khác.")
        else:
            lines.append("Nếu khách hỏi phòng trống/giá: BẮT BUỘC gọi check_availability với các ngày trên.")
    elif stay:
        args = ", ".join(f"{k}={v}" for k, v in stay.items())
        lines.append(f"Khách ĐÃ nói ngày ở tin nhắn trước: {args}. Nếu khách hỏi còn phòng/giá/muốn đặt: "
                     "BẮT BUỘC gọi check_availability với các thông số này, KHÔNG hỏi lại ngày, "
                     "KHÔNG tự nói còn hay hết phòng khi chưa có kết quả công cụ.")
    pols = (related_policies(conn, hotel_id, text)
            if conn is not None and hotel_id is not None and not dates and not PHONE_RE.search(text) else [])
    info_question = bool(pols) and not ROOM_ASK_RE.search(_norm(text))
    if not dates and not stay and not history_has_dates and not context and not info_question:
        lines.append("Tin nhắn KHÔNG có ngày cụ thể. Nếu khách hỏi còn phòng/giá: hãy hỏi lại ngày nhận, "
                     "ngày trả và số người — KHÔNG tự chọn ngày, KHÔNG gọi check_availability.")
    if conn is not None and hotel_id is not None and not dates and not PHONE_RE.search(text):
        if info_question:
            # Small models follow the first line: put the answer first and don't mention dates at all.
            lines.insert(0, "Khách hỏi THÔNG TIN khách sạn, không phải tra phòng. Trả lời TRỰC TIẾP, ngắn gọn "
                            "đúng câu hỏi dựa trên dữ liệu sau, KHÔNG hỏi ngày, KHÔNG liệt kê thông tin không được hỏi: "
                            + " | ".join(f"{p['title']}: {p['content']}" for p in pols))
        elif pols:
            lines.append("Chính sách có thể liên quan (chỉ dùng nếu đúng câu hỏi, có thể gọi search_policies để xem thêm): "
                         + " | ".join(f"{p['title']}: {p['content']}" for p in pols))
        elif not (stay and ROOM_INTENT_RE.search(_norm(text))):  # a room question is not a missing policy
            lines.append("Không có chính sách nào khớp câu hỏi này. Nếu khách hỏi tiện ích/dịch vụ không có trong dữ liệu: "
                         "nói là chưa có thông tin, không liệt kê dịch vụ khác, đề nghị chuyển nhân viên.")
    return "\n\n[Ghi chú hệ thống — khách không thấy]\n" + "\n".join(f"- {x}" for x in lines)


def with_hint(text: str, today: date, history: list[dict],
              conn: sqlite3.Connection | None = None, hotel_id: int | None = None,
              context: dict | None = None) -> str:
    has_dates = any(m["role"] == "user" and extract_dates(m["content"], today) for m in history)
    stay = known_stay(text, history, today, context)
    return text + build_hint(text, today, has_dates, conn, hotel_id, context, stay)
