"""End-to-end API tests with a temp DB and a scripted fake LLM."""
import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import api_public
from app.db import connect
from app.deps import get_conn
from app.main import app
from app.seed import DEMO_PASSWORD, seed


class FakeLLM:
    model = "fake"

    def __init__(self):
        self.script = []

    def chat(self, messages, tools=None):
        return self.script.pop(0)

    def ping(self):
        return {"ok": True}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    c = connect(path)
    seed(c)
    c.close()

    def _conn():
        conn = connect(path)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[get_conn] = _conn
    fake = FakeLLM()
    monkeypatch.setattr(api_public, "llm", fake)
    tc = TestClient(app)
    tc.fake = fake
    yield tc
    app.dependency_overrides.clear()


def login(client, user="quanly.song-han"):
    r = client.post("/api/auth/login", json={"username": user, "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_auth_required_and_wrong_password(client):
    assert client.get("/api/staff/dashboard").status_code == 401
    assert client.post("/api/auth/login", json={"username": "quanly.song-han", "password": "x"}).status_code == 401


def test_dashboard_and_calendar(client):
    h = login(client)
    d = client.get("/api/staff/dashboard", headers=h).json()
    assert d["total_rooms"] == 29 and len(d["occupancy"]) == 14
    cal = client.get("/api/staff/calendar", headers=h).json()
    assert len(cal["rooms"]) == 30


def test_website_booking_then_staff_confirm_checkin_checkout(client):
    ci = date.today() + timedelta(days=3)
    q = {"check_in": ci.isoformat(), "check_out": (ci + timedelta(days=1)).isoformat(), "adults": 2}
    res = client.post("/api/hotels/song-han/search", json=q).json()
    opt = next(o for o in res["options"] if o["available_rooms"] > 0)
    r = client.post("/api/hotels/song-han/bookings", json={**q, "room_type_code": opt["room_type_code"],
                                                           "guest_name": "Nguyễn Test", "phone": "0905123456"})
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    h = login(client, "letan.song-han")
    assert any(b["code"] == code for b in client.get("/api/staff/dashboard", headers=h).json()["holds"])
    assert client.post(f"/api/staff/bookings/{code}/confirm", headers=h).json()["status"] == "confirmed"
    # check-in before arrival date is refused
    assert client.post(f"/api/staff/bookings/{code}/check-in", headers=h).status_code == 409
    detail = client.get(f"/api/staff/bookings/{code}", headers=h).json()
    assert detail["booking"]["total_price"] == sum(n["price"] for n in detail["nights"])


def test_staff_walkin_today_and_room_move(client):
    h = login(client)
    today = date.today()
    q = f"check_in={today}&check_out={today + timedelta(days=1)}"
    free = client.get(f"/api/staff/availability?{q}", headers=h).json()
    if len(free) < 2:
        pytest.skip("not enough free rooms tonight")
    b = client.post("/api/staff/bookings", headers=h, json={
        "room_type_code": free[0]["type_code"], "room_id": free[0]["id"], "check_in": str(today),
        "check_out": str(today + timedelta(days=1)), "guest_name": "Walk In", "phone": "0905000000"}).json()
    assert b["status"] == "confirmed"
    assert client.post(f"/api/staff/bookings/{b['code']}/check-in", headers=h).json()["status"] == "checked_in"
    moved = client.patch(f"/api/staff/bookings/{b['code']}", headers=h, json={"room_id": free[1]["id"]})
    assert moved.status_code == 200 and moved.json()["room_id"] == free[1]["id"]
    # the same room can't be double booked
    again = client.post("/api/staff/bookings", headers=h, json={
        "room_type_code": free[1]["type_code"], "room_id": free[1]["id"], "check_in": str(today),
        "check_out": str(today + timedelta(days=1)), "guest_name": "Dup", "phone": "0905000001"})
    assert again.status_code == 409
    out = client.post(f"/api/staff/bookings/{b['code']}/check-out", headers=h).json()
    assert out["status"] == "checked_out"


def test_receptionist_cannot_change_config(client):
    h = login(client, "letan.song-han")
    assert client.post("/api/staff/policies", headers=h, json={"title": "X", "content": "Y"}).status_code == 403
    m = login(client)
    assert client.post("/api/staff/policies", headers=m, json={"title": "Sân tennis", "content": "Có sân tennis"}).status_code == 200


def test_hotel_isolation(client):
    h = login(client, "quanly.doi-thong")
    codes = {t["code"] for t in client.get("/api/staff/room-types", headers=h).json()}
    assert "DLX" not in codes
    assert client.get("/api/staff/bookings/BK1001", headers=h).status_code in (200, 404)
    for b in client.get("/api/staff/bookings", headers=h).json():
        assert not b["room_type_code"] in ("STD", "DLX", "FAM", "SUP")


def test_chat_handoff_staff_reply_and_back_to_ai(client):
    sid = "web-test1"
    client.fake.script = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {
            "name": "handoff_to_staff", "arguments": json.dumps({"reason": "khiếu nại"})}}]},
        {"role": "assistant", "content": "Em đã chuyển nhân viên ạ."},
    ]
    r = client.post("/api/chat", json={"hotel": "song-han", "session_id": sid, "message": "Tôi muốn khiếu nại"}).json()
    assert r["mode"] == "staff"
    # while staff mode, AI is not called
    r2 = client.post("/api/chat", json={"hotel": "song-han", "session_id": sid, "message": "Alo?"}).json()
    assert r2["reply"] is None
    h = login(client)
    convs = client.get("/api/staff/conversations", headers=h).json()
    assert convs[0]["session_id"] == sid and convs[0]["handoff_reason"] == "khiếu nại"
    detail = client.get(f"/api/staff/conversations/{sid}", headers=h).json()
    assert detail["messages"][1]["trace"][0]["tool"] == "handoff_to_staff"
    client.post(f"/api/staff/conversations/{sid}/reply", headers=h, json={"text": "Chào anh, em là lễ tân Lan"})
    msgs = client.get(f"/api/chat/song-han/{sid}/messages?after={r2['last_id']}").json()
    assert msgs["messages"][-1]["role"] == "staff"
    client.post(f"/api/staff/conversations/{sid}/mode", headers=h, json={"mode": "ai"})
    client.fake.script = [{"role": "assistant", "content": "AI quay lại"}]
    r3 = client.post("/api/chat", json={"hotel": "song-han", "session_id": sid, "message": "Cho mình hỏi thêm chút"}).json()
    assert r3["reply"] == "AI quay lại"


def test_policy_edit_changes_what_ai_sees(client):
    """Staff edits the knowledge base -> the AI tool result changes immediately."""
    m = login(client)
    client.post("/api/staff/policies", headers=m, json={"title": "Sân tennis", "content": "Có sân tennis tầng 5",
                                                        "keywords": "tennis"})
    client.fake.script = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {
            "name": "search_policies", "arguments": json.dumps({"query": "tennis"})}}]},
        {"role": "assistant", "content": "Có ạ"},
    ]
    r = client.post("/api/chat", json={"hotel": "song-han", "session_id": "t2", "message": "Có sân tennis không?"}).json()
    assert r["trace"][0]["result"]["results"][0]["title"] == "Sân tennis"
