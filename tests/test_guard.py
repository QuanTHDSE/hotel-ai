"""The AI must never claim a booking it did not make."""
import json
from datetime import date, datetime

from app.agent import ReceptionistAgent, claims_hold
from app.db import connect, get_hotel
from app.hints import build_hint
from app.seed import seed
from app.sessions import ensure_session

TODAY = date(2026, 11, 16)


class FakeLLM:
    model = "fake"

    def __init__(self, script):
        self.script, self.calls = list(script), []

    def chat(self, messages, tools=None):
        self.calls.append(json.loads(json.dumps(messages)))
        return self.script.pop(0)


def test_claim_detection():
    assert claims_hold("Ưhm, tôi sẽ giữ chỗ tạm thời cho phòng Deluxe cho anh.")
    assert claims_hold("Dạ em đã giữ phòng cho anh/chị.")
    assert claims_hold("I have reserved the room for you.")
    assert not claims_hold("Anh/chị muốn em giữ phòng nào ạ?")
    assert not claims_hold("Dạ còn 2 phòng Deluxe, tổng 1.320.000đ. Anh/chị có muốn đặt không ạ?")


def test_hint_includes_last_search_when_phone_given():
    h = build_hint("Ok đặt giúp mình, tên An, sđt 0905123456", TODAY, True,
                   context={"check_in": "2026-11-20", "check_out": "2026-11-21", "adults": 2, "room_type_code": "DLX"})
    assert "create_booking_hold" in h and "room_type_code=DLX" in h and "check_in=2026-11-20" in h


def _setup():
    conn = connect(":memory:")
    seed(conn, today=TODAY)
    hid = get_hotel(conn, "song-han")["id"]
    ensure_session(conn, hid, "s")
    return conn, hid


def test_false_claim_is_corrected_then_hold_created():
    conn, hid = _setup()
    hold_call = {"role": "assistant", "content": "", "tool_calls": [{"id": "h", "type": "function", "function": {
        "name": "create_booking_hold", "arguments": json.dumps({
            "room_type_code": "SUP", "check_in": "2026-11-24", "check_out": "2026-11-25",
            "guest_name": "Nguyễn Test", "phone": "0905123456"})}}]}
    llm = FakeLLM([
        {"role": "assistant", "content": "Dạ em đã giữ phòng cho anh rồi ạ."},   # lie
        hold_call,                                                               # after correction
        {"role": "assistant", "content": "Dạ em đã giữ phòng thành công ạ."},     # forgets the code
    ])
    agent = ReceptionistAgent(conn, llm)
    agent._set_context(hid, "s", {"check_in": "2026-11-24", "check_out": "2026-11-25", "adults": 2}, True)
    res = agent.run(hid, "s", "Đặt giúp mình, Nguyễn Test 0905123456", today=TODAY, now=datetime(2026, 11, 16, 9))
    assert "check_in=2026-11-24" in llm.calls[0][-1]["content"]  # last search passed to the model
    assert [t["tool"] for t in res.trace] == ["system_guard", "create_booking_hold"]
    code = res.trace[1]["result"]["hold_code"]
    assert code in res.reply  # code appended automatically
    assert conn.execute("SELECT status FROM bookings WHERE code = ?", (code,)).fetchone()["status"] == "hold"


def test_repeated_false_claim_asks_for_missing_slots():
    conn, hid = _setup()
    lie = {"role": "assistant", "content": "Em đã giữ phòng cho anh."}
    res = ReceptionistAgent(conn, FakeLLM([lie, lie])).run(hid, "s", "Đặt giúp mình 0905123456", today=TODAY)
    assert "cho em xin thêm" in res.reply and "họ tên" in res.reply and "ngày" in res.reply
    assert not conn.execute("SELECT COUNT(*) FROM bookings WHERE channel = 'ai-chat'").fetchone()[0]


def test_repeated_false_claim_booked_by_code():
    conn, hid = _setup()
    lie = {"role": "assistant", "content": "Em đã giữ phòng cho anh."}
    agent = ReceptionistAgent(conn, FakeLLM([lie, lie]))
    agent._set_context(hid, "s", {"check_in": "2026-11-24", "check_out": "2026-11-25", "adults": 2}, True)
    res = agent.run(hid, "s", "Ok đặt giúp mình phòng Superior, tên Trần Test, sđt 0905111222", today=TODAY,
                    now=datetime(2026, 11, 16, 9))
    hold = next(t for t in res.trace if t["tool"] == "create_booking_hold")["result"]
    assert hold["room_type"] == "Superior Twin" and hold["hold_code"] in res.reply
    b = conn.execute("SELECT guest_name, phone, check_in FROM bookings WHERE code = ?", (hold["hold_code"],)).fetchone()
    assert (b["guest_name"], b["phone"], b["check_in"]) == ("Trần Test", "0905111222", "2026-11-24")


def test_complete_booking_message_skips_model():
    """Guest gives intent + room + name + phone after a search: booked in code, model never called."""
    conn, hid = _setup()
    llm = FakeLLM([])  # any model call would raise IndexError
    agent = ReceptionistAgent(conn, llm)
    agent._set_context(hid, "s", {"check_in": "2026-11-24", "check_out": "2026-11-25", "adults": 2,
                                  "room_type_code": "DLX"}, True)
    res = agent.run(hid, "s", "Ok đặt giúp mình phòng Deluxe nhé, tên Nguyễn Văn Test, sđt 0905123456",
                    today=TODAY, now=datetime(2026, 11, 16, 9))
    assert not llm.calls
    hold = res.trace[1]["result"]
    assert hold["room_type"] == "Deluxe River View" and hold["hold_code"] in res.reply


def test_offer_to_hold_without_guest_info_uses_template():
    conn, hid = _setup()
    search = {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "type": "function", "function": {
        "name": "check_availability", "arguments": json.dumps({"check_in": "2026-11-20", "check_out": "2026-11-21"})}}]}
    llm = FakeLLM([search, {"role": "assistant", "content": "Còn phòng ạ, em sẽ giữ phòng Deluxe cho anh nhé."}])
    res = ReceptionistAgent(conn, llm).run(hid, "s", "Thứ 6 này giá phòng thế nào?", today=TODAY)
    assert len(llm.calls) == 2                      # no confusing re-prompt
    assert res.reply.startswith("Dạ 1 đêm từ Thứ Sáu 20/11")
    assert res.reply.endswith("Anh/chị muốn em giữ phòng nào ạ?")


def test_wants_human_hands_off_without_model():
    from app.agent import wants_human
    assert wants_human("Cho mình gặp nhân viên để hỏi về xuất hóa đơn công ty")
    assert wants_human("I want to speak to a human")
    assert not wants_human("Nhân viên có nói tiếng Anh không?")
    conn, hid = _setup()
    llm = FakeLLM([])
    res = ReceptionistAgent(conn, llm).run(hid, "s", "Cho mình gặp nhân viên", today=TODAY)
    assert not llm.calls and "chuyển cuộc trò chuyện cho nhân viên" in res.reply
    assert conn.execute("SELECT mode FROM chat_sessions WHERE session_id = 's'").fetchone()["mode"] == "staff"


def test_name_extraction():
    from app.booking_flow import extract_name
    rw = {"superior", "twin", "standard", "double", "sup", "std"}
    assert extract_name("Ok đặt giúp mình phòng Superior, tên Trần Eval, sđt 0905111222", rw) == "Trần Eval"
    assert extract_name("Chốt Standard nhé. Lê Văn Eval, 0912345678", rw) == "Lê Văn Eval"
    assert extract_name("đặt luôn cho mình nhé", rw) is None


def test_availability_claim_without_search_is_checked_in_code():
    """Real bug: dates given in an earlier turn, model said 'Chưa có phòng trống' without calling the tool."""
    from app.sessions import add_message
    conn, hid = _setup()
    for role, text in [("user", "Tôi muốn ưu tiên ngày 17/11"), ("assistant", "Anh/chị đi mấy người ạ?"),
                       ("user", "Chỉ có mình tôi thôi"), ("assistant", "Dạ vâng ạ.")]:
        add_message(conn, hid, "s", role, text)
    agent_history = [{"role": r, "content": t} for r, t in [("user", "Tôi muốn ưu tiên ngày 17/11"),
                     ("assistant", "Anh/chị đi mấy người ạ?"), ("user", "Chỉ có mình tôi thôi"), ("assistant", "Dạ vâng ạ.")]]
    llm = FakeLLM([{"role": "assistant", "content": "Chưa có phòng trống cho ngày 17/11/2026. "
                                                   "Tôi sẽ kiểm tra tình trạng phòng vào ngày mai."}])
    res = ReceptionistAgent(conn, llm).run(hid, "s", "TÔi muốn đặt phòng mình còn phòng nào trống ko",
                                           today=TODAY, now=datetime(2026, 11, 16, 9))
    assert not llm.calls  # dates known from the chat → answered from a search in code, model not needed
    from app.hints import with_hint
    hint = with_hint("TÔi muốn đặt phòng mình còn phòng nào trống ko", TODAY, agent_history, conn, hid)
    assert "check_in=2026-11-17" in hint and "adults=1" in hint and "BẮT BUỘC gọi check_availability" in hint
    assert "Không có chính sách nào khớp" not in hint
    search = res.trace[0]
    assert search["tool"] == "check_availability" and search["args"]["_by"] == "code"
    assert (search["result"]["check_in"], search["result"]["adults"]) == ("2026-11-17", 1)
    assert res.reply.startswith("Dạ 1 đêm từ Thứ Ba 17/11 đến 18/11 cho 1 người lớn, bên em còn:")
    assert "Chưa có phòng trống" not in res.reply


def test_availability_talk_detection():
    from app.agent import talks_availability
    assert talks_availability("Chưa có phòng trống cho ngày 17/11.")
    assert talks_availability("Dạ bên em hết phòng rồi ạ.")
    assert talks_availability("Sorry, we are fully booked.")
    assert not talks_availability("Anh/chị muốn kiểm tra phòng trống ngày nào ạ?")
    assert not talks_availability("Dạ chào anh/chị, em có thể giúp gì ạ?")


def test_room_question_with_known_dates_is_checked_even_if_model_copies_old_reply():
    """Real bug: model repeated its previous answer instead of searching for 'liệt kê phòng còn trống'."""
    from app.agent import asks_availability
    from app.sessions import add_message
    assert asks_availability("Hãy liệt kê các loại phòng còn trống ngày hôm đó")
    assert asks_availability("Mình có loại phòng nào nhỉ")
    assert asks_availability("tôi muốn đặt phòng vào ngày 20/11")
    assert not asks_availability("Khách sạn có hồ bơi không?")
    conn, hid = _setup()
    add_message(conn, hid, "s", "user", "tôi muốn đặt phòng vào ngày 20/11")
    add_message(conn, hid, "s", "assistant", "Anh/chị muốn đặt loại phòng nào?")
    stale = "Ưu tiên anh/chị đặt loại phòng Standard Double (STD) từ 14h ngày 20/11 đến 12h ngày 21/11."
    res = ReceptionistAgent(conn, FakeLLM([{"role": "assistant", "content": stale}])).run(
        hid, "s", "Hãy liệt kê các loại phòng còn trống ngày hôm đó", today=TODAY, now=datetime(2026, 11, 16, 9))
    assert res.trace[0]["tool"] == "check_availability" and res.trace[0]["result"]["check_in"] == "2026-11-20"
    assert res.reply.startswith("Dạ 1 đêm từ Thứ Sáu 20/11 đến 21/11") and "Superior Twin" in res.reply


def test_booking_request_without_dates_uses_page_search_form():
    """Real bug: guest kept saying 'Tôi muốn đặt phòng' and the AI kept asking for dates that the page already had."""
    conn, hid = _setup()
    llm = FakeLLM([])  # answered in code
    page = {"check_in": "2026-11-17", "check_out": "2026-11-18", "adults": 2, "children": 0}
    agent = ReceptionistAgent(conn, llm)
    res = agent.run(hid, "s", "Tôi muốn đặt phòng bạn hãy hỗ trợ tôi", today=TODAY,
                    now=datetime(2026, 11, 16, 9), page_stay=page)
    assert not llm.calls
    assert res.trace[0]["args"]["_from"] == "page_search"
    assert res.reply.startswith("Dạ 1 đêm từ Thứ Ba 17/11 đến 18/11 cho 2 người lớn")
    assert "đang chọn trên trang" in res.reply and res.reply.endswith("Anh/chị muốn em giữ phòng nào ạ?")
    assert agent._get_context(hid, "s", True)["check_in"] == "2026-11-17"  # later turns keep these dates
    # Dates typed in the chat win over the page form: the model handles it.
    llm2 = FakeLLM([{"role": "assistant", "content": "Dạ anh/chị đi mấy người ạ?"}])
    ReceptionistAgent(conn, llm2).run(hid, "s2", "đặt phòng ngày 25/11", today=TODAY, page_stay=page)
    assert len(llm2.calls) == 1


def test_new_date_with_invented_prices_is_rechecked_for_that_date():
    """Real bug: after a search for 17/11, guest asked for 20/11; model invented 20–22/11 prices without searching,
    and the price guard let them through because its own note quoted those amounts."""
    from app.agent import asks_availability
    from app.grounding import ungrounded_amounts
    assert asks_availability("Còn ngày 20/11 thì sao")
    assert not asks_availability("Chính sách hủy thì sao?")
    assert ungrounded_amounts("tổng 550.000đ", [{"tool": "system_guard", "result": {"note": "[550000]"}}]) == {550000}
    conn, hid = _setup()
    agent = ReceptionistAgent(conn, FakeLLM([]))
    agent.run(hid, "s", "Tôi muốn đặt phòng", today=TODAY, now=datetime(2026, 11, 16, 9),
              page_stay={"check_in": "2026-11-17", "check_out": "2026-11-18", "adults": 2, "children": 0})
    fake = ("Dạ từ Thứ Sáu 20/11 đến Chủ Nhật 22/11 cho 2 người lớn. Bên em còn:\n"
            "• Standard Double: còn 4 phòng, tổng 550.000đ")
    agent.llm = FakeLLM([{"role": "assistant", "content": fake}])
    res = agent.run(hid, "s", "Tôi muốn biết trong ngày 20/11 thôi", today=TODAY, now=datetime(2026, 11, 16, 9))
    assert res.trace[0]["tool"] == "check_availability" and res.trace[0]["result"]["check_in"] == "2026-11-20"
    assert res.trace[0]["result"]["check_out"] == "2026-11-21"
    assert res.reply.startswith("Dạ 1 đêm từ Thứ Sáu 20/11 đến 21/11") and "550.000" not in res.reply


def test_date_in_message_without_any_tool_call_is_searched():
    conn, hid = _setup()
    llm = FakeLLM([{"role": "assistant", "content": "Dạ anh/chị muốn đặt loại phòng nào ạ?"}])
    res = ReceptionistAgent(conn, llm).run(hid, "s", "Tôi muốn biết trong ngày 20/11 thôi", today=TODAY,
                                           now=datetime(2026, 11, 16, 9))
    assert res.trace[0]["tool"] == "check_availability" and res.reply.startswith("Dạ 1 đêm từ Thứ Sáu 20/11")


def test_greeting_gets_short_reply_without_model():
    """Real bug: 'chào bạn' made the model fetch policies and list amenities + payment terms."""
    from app.agent import small_talk
    assert small_talk("chào bạn") == "greet" and small_talk("Xin chào shop ạ") == "greet"
    assert small_talk("Hello") == "greet" and small_talk("alo") == "greet"
    assert small_talk("cảm ơn bạn nhiều nhé") == "thanks" and small_talk("ok thanks") == "thanks"
    assert small_talk("tạm biệt") == "bye"
    assert small_talk("chào bạn, còn phòng không?") is None
    assert small_talk("hi, do you have a room for 20/11") is None
    assert small_talk("ok") is None and small_talk("Chào, có hồ bơi không") is None
    conn, hid = _setup()
    llm = FakeLLM([])
    agent = ReceptionistAgent(conn, llm)
    res = agent.run(hid, "s", "chào bạn", today=TODAY)
    assert not llm.calls and res.reply.startswith("Dạ em chào anh/chị") and "Hồ bơi" not in res.reply
    assert agent.run(hid, "s2", "hello", today=TODAY).reply.startswith("Hello!")
    assert agent.run(hid, "s3", "alo", today=TODAY).reply.startswith("Dạ em chào")


def test_past_date_is_refused_not_searched_next_year():
    """Real bug: on 17/11 the guest asked for 12/11; it was searched as 12/11/2027 and reported as available."""
    conn, hid = _setup()
    today = date(2026, 11, 17)
    llm = FakeLLM([])
    res = ReceptionistAgent(conn, llm).run(hid, "s", "Tôi mún bạn check xem ngày 12/11 còn phòng ko", today=today,
                                           now=datetime(2026, 11, 17, 9))
    assert not llm.calls and res.trace[0]["tool"] == "system_past_date"
    assert "12/11/2026 đã qua" in res.reply and "còn:" not in res.reply


def test_answer_shows_year_when_not_this_year():
    from app.grounding import availability_answer
    res = {"check_in": "2027-01-05", "check_out": "2027-01-06", "nights": 1, "adults": 2, "children": 0,
           "options": []}
    assert "05/01/2027" in availability_answer(res, "vi", date(2026, 11, 17))
    res.update(check_in="2026-11-20", check_out="2026-11-21")
    assert "20/11 đến 21/11 " in availability_answer(res, "vi", date(2026, 11, 17))


def test_pick_room_then_name_phone_books_it():
    """Real bug: after a search, 'giữ phòng Deluxe River View' got the room list again instead of asking name/phone."""
    conn, hid = _setup()
    now = datetime(2026, 11, 16, 9)
    agent = ReceptionistAgent(conn, FakeLLM([]))  # whole flow runs in code
    agent._set_context(hid, "s", {"check_in": "2026-11-19", "check_out": "2026-11-20", "adults": 1, "children": 0,
                                  "room_type_code": None}, True)
    res = agent.run(hid, "s", "Tôi muốn bạn giúp mình giữ phòng  Deluxe River View ạ", today=TODAY, now=now)
    assert res.reply.startswith("Dạ anh/chị chọn Deluxe River View, 1 đêm từ 19/11 đến 20/11 cho 1 người lớn, tổng ")
    assert "họ tên và số điện thoại" in res.reply and "bên em còn:" not in res.reply
    assert agent._get_context(hid, "s", True)["room_type_code"] == "DLX"
    res = agent.run(hid, "s", "Nguyễn Văn Test 0905123456", today=TODAY, now=now)
    hold = next(t for t in res.trace if t["tool"] == "create_booking_hold")["result"]
    assert hold["room_type"] == "Deluxe River View" and hold["hold_code"] in res.reply


def test_booking_request_after_search_lists_rooms_and_asks_for_choice_and_contact():
    """'giúp mình đặt phòng' after a search: list what's free and ask room + name + phone (no model needed)."""
    conn, hid = _setup()
    llm = FakeLLM([])
    agent = ReceptionistAgent(conn, llm)
    agent._set_context(hid, "s", {"check_in": "2026-11-19", "check_out": "2026-11-20", "adults": 1, "children": 0}, True)
    res = agent.run(hid, "s", "bạn giúp mình đặt phòng được không", today=TODAY)
    assert not llm.calls and res.reply.startswith("Dạ 1 đêm từ Thứ Năm 19/11 đến 20/11 cho 1 người lớn")
    assert res.reply.endswith("Anh/chị chọn loại phòng nào, kèm họ tên và số điện thoại để em giữ phòng nhé ạ?")

def test_free_schedule_question_after_search_is_answered_from_data():
    """Real bug: 'Có lịch nào trống ?' after a search got 'anh/chị đã cho em biết check_in=2026-11-19 ...'."""
    from app.agent import asks_free_rooms
    assert asks_free_rooms("Có lịch nào trống ?") and asks_free_rooms("ngày 19/11 có phòng nào")
    assert not asks_free_rooms("Phòng nào có view sông?") and not asks_free_rooms("Có phòng nào view sông không")
    conn, hid = _setup()
    llm = FakeLLM([])
    agent = ReceptionistAgent(conn, llm)
    agent._set_context(hid, "s", {"check_in": "2026-11-19", "check_out": "2026-11-20", "adults": 2, "children": 0}, True)
    res = agent.run(hid, "s", "Có lịch nào trống ?", today=TODAY, now=datetime(2026, 11, 16, 9))
    assert not llm.calls and res.reply.startswith("Dạ 1 đêm từ Thứ Năm 19/11 đến 20/11 cho 2 người lớn, bên em còn:")


def test_leaked_hint_text_is_never_shown():
    conn, hid = _setup()
    leak = {"role": "assistant", "content": "Dạ, anh/chị đã cho em biết check_in=2026-11-19, check_out=2026-11-20."}
    agent = ReceptionistAgent(conn, FakeLLM([leak]))
    agent._set_context(hid, "s", {"check_in": "2026-11-19", "check_out": "2026-11-20", "adults": 2, "children": 0}, True)
    res = agent.run(hid, "s", "Cho mình hỏi thêm chút", today=TODAY, now=datetime(2026, 11, 16, 9))
    assert "check_in=" not in res.reply and res.reply.startswith("Dạ 1 đêm từ Thứ Năm 19/11")
    res = ReceptionistAgent(conn, FakeLLM([leak])).run(hid, "s2", "Cho mình hỏi thêm chút", today=TODAY)
    assert "check_in=" not in res.reply and res.reply.startswith("Dạ anh/chị cần em hỗ trợ gì thêm")
