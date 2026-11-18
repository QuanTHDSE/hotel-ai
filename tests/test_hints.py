from datetime import date

from app.db import connect, get_hotel
from app.hints import build_hint, extract_dates, extract_nights, related_policies
from app.seed import seed

SUN = date(2026, 11, 15)  # Sunday
WED = date(2026, 11, 18)


def d(text, today=SUN):
    return [x[1] for x in extract_dates(text, today)]


def test_relative_words():
    assert d("tối nay còn phòng ko") == [SUN]
    assert d("Ngày mai còn phòng không em") == [date(2026, 11, 16)]
    assert d("ngày kia") == [date(2026, 11, 17)]


def test_weekdays():
    assert d("Tối thứ 7 tuần này", SUN) == [date(2026, 11, 21)]
    assert d("ở tối thứ 6, 1 đêm", SUN) == [date(2026, 11, 20)]
    assert d("thứ sáu này", WED) == [date(2026, 11, 20)]
    assert d("thứ 2 tuần sau", WED) == [date(2026, 11, 23)]
    assert d("chủ nhật", WED) == [date(2026, 11, 22)]
    assert d("cuối tuần này", WED) == [date(2026, 11, 21)]
    assert d("next friday", WED) == [date(2026, 11, 27)]


def test_explicit_dates_and_year_rollover():
    assert d("từ 25/11 đến 28/11") == [date(2026, 11, 25), date(2026, 11, 28)]
    assert d("ngày 3 tháng 1") == [date(2027, 1, 3)]
    assert d("10/11") == [date(2026, 11, 10)]  # a few days ago -> a past date, not next year
    assert d("10/7") == [date(2027, 7, 10)]    # long past this year -> next year
    assert d("31/2") == []


def test_nights():
    assert extract_nights("ở 2 đêm") == 2
    assert extract_nights("ở hai đêm nhé") == 2
    assert extract_nights("for 3 nights") == 3
    assert extract_nights("không biết") is None


def test_hint_messages():
    assert "KHÔNG tự chọn ngày" in build_hint("Còn phòng không shop?", SUN)
    h = build_hint("từ 20/11, 2 đêm", SUN)
    assert "check_in=2026-11-20, check_out=2026-11-22" in h


def test_related_policies():
    c = connect(":memory:")
    seed(c, today=SUN)
    hid = get_hotel(c, "doi-thong")["id"]
    assert related_policies(c, hid, "Mình mang theo bé cún nhỏ 5kg được không?")[0]["title"] == "Thú cưng"
    assert related_policies(c, get_hotel(c, "song-han")["id"], "Khách sạn có sân tennis không?") == []


def test_party_and_known_stay():
    from app.hints import extract_party, known_stay
    assert extract_party("Chỉ có mình tôi thôi") == (1, 0)
    assert extract_party("2 người lớn 1 trẻ em") == (2, 1)
    assert extract_party("ba người") == (3, 0)
    assert extract_party("còn phòng không") is None
    hist = [{"role": "user", "content": "từ 20/11 ở 2 đêm"}, {"role": "assistant", "content": "Dạ"},
            {"role": "user", "content": "đi 2 người lớn"}]
    assert known_stay("còn phòng không", hist, SUN) == {
        "check_in": "2026-11-20", "check_out": "2026-11-22", "adults": 2, "children": 0}
    assert known_stay("còn phòng không", [], SUN) is None


def test_policy_hint_kept_for_policy_question_mentioning_room():
    from app.hints import with_hint
    conn = connect(":memory:")
    seed(conn, today=SUN)
    hid = get_hotel(conn, "song-han")["id"]
    hist = [{"role": "user", "content": "đặt phòng 20/11"}]
    h = with_hint("Chính sách hủy phòng thế nào?", SUN, hist, conn, hid)
    assert "Chính sách hủy phòng" in h and "Không có chính sách nào khớp" not in h


def test_info_question_puts_answer_first_and_does_not_ask_dates():
    conn = connect(":memory:")
    seed(conn, today=SUN)
    hid = get_hotel(conn, "song-han")["id"]
    h = build_hint("Chào bạn, khách sạn có hồ bơi không?", SUN, conn=conn, hotel_id=hid)
    first = h.split("\n- ")[1]
    assert first.startswith("Khách hỏi THÔNG TIN") and "Hồ bơi" in first and "KHÔNG có ngày cụ thể" not in h
    h2 = build_hint("Còn phòng không, có hồ bơi không?", SUN, conn=conn, hotel_id=hid)  # room question first
    assert "KHÔNG có ngày cụ thể" in h2 and "Khách hỏi THÔNG TIN" not in h2
