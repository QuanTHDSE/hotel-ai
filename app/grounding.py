"""Check that money amounts in a reply really come from tool results, and build
template answers from tool data when the model can't be trusted."""
import re
from datetime import date

from .prompts import WEEKDAYS_VI

_PHONE_LIKE = re.compile(r"^(0|84)\d{8,10}$")


def money_numbers(text: str) -> set[int]:
    """Money-like numbers in text: 1.320.000đ, 1,320,000, 1320000, 650k, 1.3 triệu. Phone numbers excluded."""
    out = set()
    for m in re.finditer(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})+|\d{5,})(?![\d])", text):
        raw = m.group(1)
        digits = re.sub(r"[.,]", "", raw)
        if _PHONE_LIKE.match(digits) and "." not in raw and "," not in raw:
            continue
        out.add(int(digits))
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*(k|K|tr|triệu|trieu)\b", text):
        v = float(m.group(1).replace(",", "."))
        out.add(int(round(v * (1000 if m.group(2).lower() == "k" else 1_000_000))))
    return {n for n in out if n >= 50_000}


def tool_numbers(trace: list[dict]) -> set[int]:
    nums: set[int] = set()

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, bool):
            pass
        elif isinstance(x, int):
            nums.add(x)
        elif isinstance(x, str):
            nums.update(money_numbers(x))

    for t in trace:
        if t.get("tool", "").startswith("system_"):
            continue  # our own guard notes quote the bad amounts: they are not data
        walk(t.get("result"))
    return nums


def ungrounded_amounts(reply: str, trace: list[dict], user_text: str = "") -> set[int]:
    """Amounts in the reply that are not in any tool result (multiples ×2..4 allowed for multi-room totals)."""
    allowed = tool_numbers(trace) | money_numbers(user_text)
    return {n for n in money_numbers(reply)
            if n not in allowed and not any(n == a * k for a in allowed for k in (2, 3, 4))}


def availability_answer(res: dict, lang: str = "vi", today: date | None = None) -> str:
    """Deterministic, always-correct answer built from a check_availability result."""
    opts = [o for o in res["options"] if o.get("fits_party")]
    ci, co = date.fromisoformat(res["check_in"]), date.fromisoformat(res["check_out"])
    this_year = (today or date.today()).year
    fmt = "%d/%m" if ci.year == co.year == this_year else "%d/%m/%Y"  # show the year when it isn't this year
    if lang == "en":
        if not opts:
            return (f"Sorry, we are fully booked from {ci:{fmt}} to {co:{fmt}} for {res['adults']} adults. "
                    "Would you like me to check other dates?")
        lines = [f"For {res['nights']} night(s) {ci:{fmt}} – {co:{fmt}}, we have:"]
        for o in opts[:4]:
            n = o["rooms_needed"]
            lines.append(f"• {o['name']}: {o['available_rooms']} left, total {o['total_for_party_text']}"
                         + (f" for {n} rooms" if n > 1 else "") + (" incl. breakfast" if o["breakfast_included"] else ""))
        lines.append("Which room would you like to book?")
        return "\n".join(lines)
    party = f"{res['adults']} người lớn" + (f", {res['children']} trẻ em" if res["children"] else "")
    if not opts:
        return (f"Dạ rất tiếc, từ {ci:{fmt}} đến {co:{fmt}} khách sạn đã hết phòng phù hợp cho {party} ạ. "
                "Anh/chị có muốn em kiểm tra ngày khác không ạ?")
    lines = [f"Dạ {res['nights']} đêm từ {WEEKDAYS_VI[ci.weekday()]} {ci:{fmt}} đến {co:{fmt}} cho {party}, bên em còn:"]
    for o in opts[:4]:
        n = o["rooms_needed"]
        lines.append(f"• {o['name']}: còn {o['available_rooms']} phòng, tổng {o['total_for_party_text']}"
                     + (f" cho {n} phòng" if n > 1 else "") + (" (gồm ăn sáng)" if o["breakfast_included"] else ""))
    full = [o["name"] for o in res["options"] if not o.get("fits_party")]
    if full:
        lines.append(f"({', '.join(full)} đã hết hoặc không phù hợp.)")
    lines.append("Anh/chị muốn em giữ phòng nào ạ?")
    return "\n".join(lines)


def looks_english(text: str) -> bool:
    return text.isascii() and bool(re.search(r"\b(the|room|do|you|have|for|night|please|hi|hello)\b", text.lower()))
