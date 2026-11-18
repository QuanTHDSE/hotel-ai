"""Generate a synthetic fine-tuning dataset from the simulated hotel DB.

Each example is a full conversation in the exact runtime format:
system prompt (with a random "today") -> guest message -> tool call -> REAL
tool result from the DB -> receptionist answer built from that result.
Because answers are built from real tool output, the model learns to quote
tool numbers instead of inventing them.

Run:  python -m finetune.generate_dataset --n 1500
Output: finetune/data/train.jsonl, finetune/data/val.jsonl
"""
import argparse
import json
import random
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import connect  # noqa: E402
from app.grounding import availability_answer as answer_availability  # noqa: E402
from app.hints import with_hint  # noqa: E402
from app.pricing import format_vnd  # noqa: E402
from app.prompts import WEEKDAYS_VI, build_system_prompt  # noqa: E402
from app.seed import seed  # noqa: E402
from app.tools import ToolContext, execute_tool  # noqa: E402

R = random.Random(7)


def dm(d: date) -> str:
    return R.choice([d.strftime("%d/%m"), f"{d.day}/{d.month}", f"ngày {d.day} tháng {d.month}", d.strftime("%d-%m")])


def call(name: str, args: dict) -> dict:
    return {"id": f"call_{uuid.uuid4().hex[:8]}", "type": "function", "function": {"name": name, "arguments": args}}


def tool_msg(c: dict, result: dict) -> dict:
    return {"role": "tool", "tool_call_id": c["id"], "name": c["function"]["name"],
            "content": json.dumps(result, ensure_ascii=False)}


# ------------------------------------------------------------------ date phrases

def date_phrase(today: date) -> tuple[str, date, int]:
    """Return (vietnamese phrase, check_in, nights)."""
    kind = R.choice(["tomorrow", "today", "explicit", "explicit", "weekend", "weekday", "range"])
    nights = R.choice([1, 1, 2, 2, 3])
    if kind == "today":
        return R.choice(["tối nay", "hôm nay"]) + f" {nights} đêm", today, nights
    if kind == "tomorrow":
        return R.choice(["ngày mai", "mai", "tối mai"]) + f", ở {nights} đêm", today + timedelta(days=1), nights
    if kind == "weekend":
        sat = today + timedelta(days=(5 - today.weekday()) % 7)
        return R.choice(["tối thứ 7 tuần này", "thứ bảy này"]) + " 1 đêm", sat, 1
    if kind == "weekday":
        wd = R.randint(0, 6)
        d = today + timedelta(days=(wd - today.weekday()) % 7 or 7)
        name = ["thứ 2", "thứ 3", "thứ 4", "thứ 5", "thứ 6", "thứ 7", "chủ nhật"][wd]
        when = f"{name} tuần sau" if (d - today).days > 6 else f"{name} này"
        return f"{when}, {nights} đêm", d, nights
    ci = today + timedelta(days=R.randint(2, 60))
    if kind == "range":
        return f"từ {dm(ci)} đến {dm(ci + timedelta(days=nights))}", ci, nights
    return f"{dm(ci)}, {nights} đêm", ci, nights


# ------------------------------------------------------------------ answer writers

# ------------------------------------------------------------------ scenario builders

def ex_availability(ctx, today):
    phrase, ci, nights = date_phrase(today)
    adults = R.choice([1, 2, 2, 2, 3, 4])
    children = R.choice([0, 0, 0, 1, 2])
    lang = "en" if R.random() < 0.15 else "vi"
    if lang == "en":
        ask = f"Hi, do you have a room for {adults} adults" + (f" and {children} kids" if children else "") + \
              f" from {ci:%d/%m} for {nights} night(s)? How much?"
    else:
        who = f"{adults} người" if not children else f"{adults} người lớn {children} bé"
        ask = R.choice([f"Cho mình hỏi {phrase} còn phòng cho {who} không?",
                        f"{phrase.capitalize()} {who}, còn phòng ko ạ, giá bao nhiêu?",
                        f"Em ơi check giúp anh phòng {who} {phrase}",
                        f"Bên mình còn phòng {phrase} không shop, {who}"])
    args = {"check_in": ci.isoformat(), "check_out": (ci + timedelta(days=nights)).isoformat(), "adults": adults}
    if children:
        args["children"] = children
    c = call("check_availability", args)
    res = execute_tool(ctx, "check_availability", args)
    return [{"role": "user", "content": ask},
            {"role": "assistant", "content": "", "tool_calls": [c]}, tool_msg(c, res),
            {"role": "assistant", "content": answer_availability(res, lang)}]


def ex_missing_info(ctx, today):
    ask = R.choice(["Còn phòng không ạ?", "Shop ơi còn phòng ko", "Giá phòng bao nhiêu vậy?", "Mình muốn đặt phòng",
                    "Do you have rooms available?"])
    if ask.startswith("Do"):
        reply = "Hello! Could you tell me your check-in and check-out dates and the number of guests so I can check for you?"
    else:
        reply = R.choice(["Dạ anh/chị cho em xin ngày nhận phòng, ngày trả phòng và số người để em kiểm tra phòng trống và báo giá chính xác ạ?",
                          "Dạ em kiểm tra ngay ạ. Anh/chị dự định nhận phòng ngày nào, ở mấy đêm và đi mấy người ạ?"])
    return [{"role": "user", "content": ask}, {"role": "assistant", "content": reply}]


POLICY_Q = [
    # Vietnamese only: the policy texts are Vietnamese, and templated answers can't translate them.
    ("hủy phòng", ["Nếu hủy phòng thì sao?", "Chính sách hoàn hủy thế nào em?", "Hủy phòng có mất tiền không"]),
    ("đặt cọc thanh toán", ["Cọc bao nhiêu vậy?", "Thanh toán bằng thẻ được không?", "Có chuyển khoản được không em"]),
    ("bữa sáng", ["Có bao gồm ăn sáng không?", "Ăn sáng mấy giờ vậy?"]),
    ("đưa đón sân bay", ["Có xe đón sân bay không?", "Đưa đón sân bay giá sao em"]),
    ("thú cưng", ["Mang chó theo được không?", "Có nhận thú cưng không"]),
    ("gửi xe ô tô", ["Có chỗ đậu ô tô không?", "Gửi xe máy có mất phí không?"]),
    ("trẻ em giường phụ", ["Bé 4 tuổi có tính tiền không?", "Có giường phụ không em?"]),
    ("giờ nhận phòng trả phòng", ["Mấy giờ nhận phòng?", "Check out muộn được không?"]),
    ("tiện ích hồ bơi", ["Có hồ bơi không?", "Khách sạn có gym không?", "Có cho thuê xe máy không?"]),
    ("bbq", ["Có chỗ nướng BBQ không?", "Tối có lửa trại không?"]),
]


def ex_policy(ctx, today):
    query, questions = R.choice(POLICY_Q)
    ask = R.choice(questions)
    c = call("search_policies", {"query": query})
    res = execute_tool(ctx, "search_policies", {"query": query})
    if not res.get("results"):
        reply = "Dạ hiện em chưa có thông tin về vấn đề này ạ. Em chuyển nhân viên hỗ trợ anh/chị nhé?"
    else:
        top = res["results"][0]
        reply = f"Dạ về {top['title'].lower()}: {top['content']}\nAnh/chị cần em hỗ trợ thêm gì không ạ?"
    return [{"role": "user", "content": ask},
            {"role": "assistant", "content": "", "tool_calls": [c]}, tool_msg(c, res),
            {"role": "assistant", "content": reply}]


def ex_handoff(ctx, today):
    ask, reason, complaint = R.choice([
        ("Phòng hôm qua bẩn quá, tôi muốn gặp quản lý", "khiếu nại vệ sinh phòng", True),
        ("Máy lạnh phòng 305 không chạy, gọi lễ tân không ai nghe", "sự cố máy lạnh", True),
        ("Công ty mình cần đặt 20 phòng cho hội nghị", "đặt đoàn lớn", False),
        ("Có giảm giá nếu ở 1 tháng không em?", "xin giảm giá lưu trú dài", False),
        ("Cho mình nói chuyện với nhân viên thật", "khách muốn gặp người thật", False),
        ("I want a refund, the room was terrible", "complaint / refund", True),
    ])
    c = call("handoff_to_staff", {"reason": reason, "summary": ask})
    res = execute_tool(ctx, "handoff_to_staff", {"reason": reason, "summary": ask})
    if ask.isascii():
        reply = (f"I'm sorry about that. " if complaint else "") + \
                f"I have forwarded your request to our staff, they will contact you shortly. " \
                f"You can also call our hotline {res['hotline']}."
    else:
        reply = ("Dạ em rất xin lỗi anh/chị vì sự bất tiện này. " if complaint else "Dạ vâng ạ. ") + \
                "Em đã chuyển cho nhân viên phụ trách, mọi người sẽ liên hệ anh/chị trong ít phút. " \
                f"Nếu cần gấp anh/chị gọi hotline {res['hotline']} giúp em ạ."
    return [{"role": "user", "content": ask},
            {"role": "assistant", "content": "", "tool_calls": [c]}, tool_msg(c, res),
            {"role": "assistant", "content": reply}]


def ex_booking(ctx, today):
    """Multi-turn: availability -> pick room -> give name/phone -> hold."""
    msgs = ex_availability(ctx, today)
    res = json.loads(msgs[2]["content"])
    opts = [o for o in res["options"] if o.get("fits_party") and o["rooms_needed"] == 1]
    if not opts or res["children"]:
        return msgs
    o = R.choice(opts)
    msgs.append({"role": "user", "content": R.choice([f"Lấy phòng {o['name']} nhé", f"Ok đặt {o['name']} giúp mình"])})
    msgs.append({"role": "assistant", "content":
                 f"Dạ em xác nhận: {o['name']}, {res['check_in']} → {res['check_out']}, {res['adults']} khách, tổng "
                 f"{o['total_per_room_text']}. Anh/chị cho em xin họ tên và số điện thoại để giữ phòng ạ."})
    name = R.choice(["Nguyễn Minh Tuấn", "Lê Thu Hà", "Trần Quốc Bảo", "Phạm Ngọc Mai"])
    phone = f"09{R.randint(10_000_000, 99_999_999)}"
    msgs.append({"role": "user", "content": f"{name}, {phone}"})
    args = {"room_type_code": o["room_type_code"], "check_in": res["check_in"], "check_out": res["check_out"],
            "adults": res["adults"], "guest_name": name, "phone": phone}
    c = call("create_booking_hold", args)
    hold = execute_tool(ctx, "create_booking_hold", args)
    msgs += [{"role": "assistant", "content": "", "tool_calls": [c]}, tool_msg(c, hold)]
    if "error" in hold:
        msgs.append({"role": "assistant", "content": "Dạ em xin lỗi, phòng vừa hết. Em kiểm tra loại khác giúp anh/chị nhé?"})
    else:
        msgs.append({"role": "assistant", "content":
                     f"Dạ em đã giữ phòng cho anh/chị, mã giữ chỗ {hold['hold_code']}, tổng {hold['total_price_text']}. "
                     f"Giữ chỗ có hiệu lực đến {hold['hold_expires_at']}, nhân viên sẽ gọi số {phone} để gửi thông tin đặt cọc "
                     "và xác nhận ạ. Cảm ơn anh/chị!"})
    return msgs


SCENARIOS = [(ex_availability, 40), (ex_booking, 15), (ex_policy, 25), (ex_missing_info, 10), (ex_handoff, 10)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--val", type=float, default=0.05)
    args = ap.parse_args()

    out_dir = ROOT / "finetune" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    fns, weights = zip(*SCENARIOS)
    base_today = date.today()
    conn = None
    for i in range(args.n):
        if i % 100 == 0:  # fresh DB with a new "today" every 100 examples -> varied dates & occupancy
            if conn:
                conn.close()
            today = base_today + timedelta(days=R.randint(-200, 200))
            conn = connect(":memory:")
            seed(conn, today=today, rng_seed=R.randint(0, 10**6))
        hotel_id = R.choice([1, 1, 2])
        ctx = ToolContext(conn=conn, hotel_id=hotel_id, session_id="gen", today=today,
                          now=datetime.combine(today, datetime.min.time()) + timedelta(hours=9))
        fn = R.choices(fns, weights)[0]
        convo = fn(ctx, today)
        # Same hint as production (app/hints.py), computed from raw prior user turns.
        raw_history = []
        for m in convo:
            if m["role"] == "user":
                raw = m["content"]
                m["content"] = with_hint(raw, today, raw_history, conn, hotel_id)
                raw_history.append({"role": "user", "content": raw})
        rows.append({"messages": [{"role": "system", "content": build_system_prompt(conn, hotel_id, today)}] + convo})

    R.shuffle(rows)
    n_val = max(1, int(len(rows) * args.val))
    for name, part in (("val", rows[:n_val]), ("train", rows[n_val:])):
        with open(out_dir / f"{name}.jsonl", "w", encoding="utf-8") as f:
            for r in part:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Đã tạo {len(rows) - n_val} mẫu train, {n_val} mẫu val tại {out_dir}")


if __name__ == "__main__":
    main()
