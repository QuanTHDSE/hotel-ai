"""Agent loop tests with a scripted fake LLM (no Ollama needed)."""
import json
from datetime import date, datetime

from app.agent import ReceptionistAgent, _fallback_tool_calls
from app.db import connect, get_hotel
from app.seed import seed

TODAY = date(2026, 11, 16)


class FakeLLM:
    """Replays scripted assistant messages and records what it was sent."""
    model = "fake"

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append(json.loads(json.dumps(messages)))
        return self.script.pop(0)


def _setup():
    conn = connect(":memory:")
    seed(conn, today=TODAY)
    return conn, get_hotel(conn, "song-han")["id"]


def test_tool_call_then_answer_and_history_saved():
    conn, hid = _setup()
    llm = FakeLLM([
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "check_availability",
            "arguments": json.dumps({"check_in": "2026-11-17", "check_out": "2026-11-18", "adults": 2})}}]},
        {"role": "assistant", "content": "Dạ còn phòng ạ."},
    ])
    res = ReceptionistAgent(conn, llm).run(hid, "s1", "Mai giá phòng bao nhiêu?", today=TODAY,
                                          now=datetime(2026, 11, 16, 9))
    assert res.reply.startswith("Dạ 1 đêm từ Thứ Ba 17/11")  # availability answered from the template
    assert res.trace[0]["tool"] == "check_availability" and "options" in res.trace[0]["result"]
    # the tool result was fed back to the model
    assert llm.calls[1][-1]["role"] == "tool"
    # system prompt contains the calendar
    assert "2026-11-17 (ngày mai)" in llm.calls[0][0]["content"]
    # second turn sees history
    llm.script.append({"role": "assistant", "content": "Ok"})
    ReceptionistAgent(conn, llm).run(hid, "s1", "Cho mình hỏi thêm chút", today=TODAY)
    roles = [m["role"] for m in llm.calls[-1]]
    assert roles == ["system", "user", "assistant", "user"]


def test_text_tool_call_fallback_and_think_stripped():
    calls = _fallback_tool_calls('<tool_call>{"name": "search_policies", "arguments": {"query": "hủy"}}</tool_call>')
    assert calls[0]["function"]["name"] == "search_policies"
    conn, hid = _setup()
    llm = FakeLLM([
        {"role": "assistant", "content": '{"name": "search_policies", "arguments": {"query": "huy phong"}}'},
        {"role": "assistant", "content": "<think>suy nghĩ</think>Hủy miễn phí trước 3 ngày ạ."},
    ])
    res = ReceptionistAgent(conn, llm).run(hid, "s2", "Hủy phòng sao?", today=TODAY)
    assert res.trace[0]["tool"] == "search_policies"
    assert res.reply == "Hủy miễn phí trước 3 ngày ạ."


def test_runaway_tool_loop_hands_off():
    conn, hid = _setup()
    loop = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "x", "type": "function", "function": {"name": "list_room_types", "arguments": "{}"}}]}
    res = ReceptionistAgent(conn, FakeLLM([loop] * 10)).run(hid, "s3", "?", today=TODAY)
    assert "nhân viên" in res.reply
    assert conn.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0] == 1
