"""Unit tests for the DB tools (no LLM needed).  Run:  python -m pytest"""
from datetime import date, datetime, timedelta

import pytest

from app.db import connect, get_hotel
from app.pricing import nightly_price
from app.seed import seed
from app.tools import ToolContext, execute_tool, normalize

TODAY = date(2026, 11, 16)  # a Monday, outside any season


@pytest.fixture()
def conn():
    c = connect(":memory:")
    seed(c, today=TODAY)
    yield c
    c.close()


def ctx(conn, slug="song-han"):
    return ToolContext(conn=conn, hotel_id=get_hotel(conn, slug)["id"], session_id="t",
                       today=TODAY, now=datetime(2026, 11, 16, 9, 0))


def test_weekday_vs_weekend_price(conn):
    c = ctx(conn)
    std = conn.execute("SELECT id FROM room_types WHERE hotel_id=? AND code='STD'", (c.hotel_id,)).fetchone()["id"]
    assert nightly_price(conn, c.hotel_id, std, date(2026, 11, 17)) == 650_000      # Tue
    assert nightly_price(conn, c.hotel_id, std, date(2026, 11, 20)) == 780_000      # Fri x1.2
    assert nightly_price(conn, c.hotel_id, std, date(2026, 12, 24)) == 910_000      # Christmas x1.4
    assert nightly_price(conn, c.hotel_id, std, date(2026, 12, 25)) == 1_092_000    # Fri + Christmas


def test_availability_totals_match_nightly(conn):
    r = execute_tool(ctx(conn), "check_availability",
                     {"check_in": "2026-11-19", "check_out": "2026-11-22", "adults": 2})
    assert r["nights"] == 3
    for o in r["options"]:
        assert o["total_per_room"] == sum(n["price"] for n in o["nightly_prices"])


def test_party_needs_multiple_rooms(conn):
    r = execute_tool(ctx(conn), "check_availability",
                     {"check_in": "2026-11-18", "check_out": "2026-11-19", "adults": 4})
    std = next(o for o in r["options"] if o["room_type_code"] == "STD")
    fam = next(o for o in r["options"] if o["room_type_code"] == "FAM")
    assert std["rooms_needed"] == 2 and fam["rooms_needed"] == 1


def test_hold_reduces_availability_and_rejects_bad_phone(conn):
    c = ctx(conn)
    args = {"check_in": "2026-11-24", "check_out": "2026-11-25", "room_type_code": "FAM"}
    before = execute_tool(c, "check_availability", args)["options"][0]["available_rooms"]
    if before == 0:
        pytest.skip("random seed left no FAM room free")
    bad = execute_tool(c, "create_booking_hold", {**args, "guest_name": "An", "phone": "123"})
    assert "error" in bad
    ok = execute_tool(c, "create_booking_hold", {**args, "guest_name": "Nguyễn An", "phone": "0905 123 456"})
    assert ok["status"] == "hold"
    after = execute_tool(c, "check_availability", args)["options"][0]["available_rooms"]
    assert after == before - 1


def test_expired_hold_frees_room(conn):
    c = ctx(conn)
    args = {"check_in": "2026-11-24", "check_out": "2026-11-25", "room_type_code": "DLX"}
    before = execute_tool(c, "check_availability", args)["options"][0]["available_rooms"]
    if before == 0:
        pytest.skip("no DLX free")
    execute_tool(c, "create_booking_hold", {**args, "guest_name": "Bình", "phone": "0905123456"})
    later = ToolContext(conn=conn, hotel_id=c.hotel_id, today=TODAY, now=c.now + timedelta(hours=2))
    assert execute_tool(later, "check_availability", args)["options"][0]["available_rooms"] == before


def test_past_and_invalid_dates_return_errors(conn):
    c = ctx(conn)
    assert "error" in execute_tool(c, "check_availability", {"check_in": "2026-11-01", "check_out": "2026-11-02"})
    assert "error" in execute_tool(c, "check_availability", {"check_in": "2026-11-20", "check_out": "2026-11-19"})
    assert "error" in execute_tool(c, "check_availability", {"check_in": "mai", "check_out": "kia"})
    assert "error" in execute_tool(c, "no_such_tool", {})


def test_hotel_isolation(conn):
    """The homestay bot must not see the hotel's room codes."""
    r = execute_tool(ctx(conn, "doi-thong"), "get_room_type_details", {"room_type_code": "DLX"})
    assert "error" in r
    codes = {o["room_type_code"] for o in execute_tool(ctx(conn, "doi-thong"), "list_room_types", {})["room_types"]}
    assert codes == {"ATTIC", "BUNG", "GROUP"}


def test_policy_search_without_accents(conn):
    r = execute_tool(ctx(conn), "search_policies", {"query": "huy phong co mat tien khong"})
    assert r["results"][0]["title"] == "Chính sách hủy phòng"
    r = execute_tool(ctx(conn), "search_policies", {"query": "airport transfer"})
    assert "sân bay" in r["results"][0]["content"]
    assert normalize("Đưa đón") == "dua don"
