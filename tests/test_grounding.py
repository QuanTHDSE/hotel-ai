from app.grounding import availability_answer, money_numbers, ungrounded_amounts


def test_money_numbers():
    assert money_numbers("Giá 1.320.000đ, phụ thu 300k, combo 1,5 triệu") == {1_320_000, 300_000, 1_500_000}
    assert money_numbers("SĐT 0905123456, hotline 0236 3888 168, ngày 20/11/2026") == set()


def test_ungrounded():
    trace = [{"tool": "check_availability", "result": {"total_per_room": 1_320_000, "total_per_room_text": "1.320.000đ"}}]
    assert ungrounded_amounts("Tổng 1.320.000đ", trace) == set()
    assert ungrounded_amounts("2 phòng tổng 2.640.000đ", trace) == set()   # multiple rooms allowed
    assert ungrounded_amounts("2 đêm 1.960.000đ", trace) == {1_960_000}


def test_template_answer_uses_tool_numbers():
    res = {"check_in": "2026-11-20", "check_out": "2026-11-21", "nights": 1, "adults": 2, "children": 0,
           "options": [{"name": "Deluxe", "fits_party": True, "available_rooms": 2, "rooms_needed": 1,
                        "total_for_party_text": "1.320.000đ", "breakfast_included": True}]}
    a = availability_answer(res)
    assert "1.320.000đ" in a and "Thứ Sáu 20/11" in a
