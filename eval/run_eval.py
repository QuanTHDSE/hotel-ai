"""Measure how well the configured model behaves as a receptionist.

Checks per case: the right tool was called, dates were resolved correctly,
and — the important one — every price in the reply really came from a tool
result (no hallucinated prices).

Run:  python -m eval.run_eval                 (uses LLM_MODEL from .env)
      python -m eval.run_eval --model qwen2.5:3b
"""
import argparse
import json
import re
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent import LLMClient, LLMError, ReceptionistAgent  # noqa: E402
from app.db import connect, get_hotel  # noqa: E402
from app.grounding import money_numbers, ungrounded_amounts  # noqa: E402
from app.seed import reset_database  # noqa: E402


def _next_weekday(today: date, wd: int) -> date:
    return today + timedelta(days=(wd - today.weekday()) % 7)


def render(template, today: date):
    """Expand {d+N}, {d+N:dm}, {next_sat}, {next_fri+1} placeholders."""
    if isinstance(template, dict):
        return {k: render(v, today) for k, v in template.items()}
    if not isinstance(template, str):
        return template

    def repl(m):
        base, plus, fmt = m.group(1), int(m.group(2) or 0), m.group(3)
        d = {"d": today, "next_sat": _next_weekday(today, 5), "next_fri": _next_weekday(today, 4)}[base]
        d += timedelta(days=plus)
        return d.strftime("%d/%m") if fmt == "dm" else d.isoformat()

    return re.sub(r"\{(d|next_sat|next_fri)(?:\+(\d+))?(?::(dm))?\}", repl, template)


def check(case: dict, reply: str, trace: list[dict], today: date, all_trace: list[dict] | None = None) -> list[str]:
    fails = []
    called = [t["tool"] for t in trace]
    if "expect_tool" in case and case["expect_tool"] not in called:
        fails.append(f"không gọi {case['expect_tool']} (đã gọi: {called or 'không có'})")
    if "expect_tool_any" in case and not set(case["expect_tool_any"]) & set(called):
        fails.append(f"không gọi tool nào trong {case['expect_tool_any']}")
    succeeded = [t["tool"] for t in trace if "error" not in t["result"]]  # blocked attempts are harmless
    for t in case.get("expect_no_tool", []):
        if t in succeeded:
            fails.append(f"không nên gọi {t} lúc này")
    if "expect_args" in case:
        want = render(case["expect_args"], today)
        # args left out by the model are filled from context, so judge the values the tool actually used
        calls = [{**t["args"], **{k: v for k, v in t["result"].items() if k in want}}
                 for t in trace if t["tool"] == case.get("expect_tool") and isinstance(t["result"], dict)]
        if calls and not any(all(str(a.get(k)) == str(v) for k, v in want.items()) for a in calls):
            fails.append(f"sai tham số: muốn {want}, nhận {calls}")
    if "expect_contains_any" in case and not any(s.lower() in reply.lower() for s in case["expect_contains_any"]):
        fails.append(f"câu trả lời thiếu ý: {case['expect_contains_any']}")
    asks = "?" in reply or re.search(r"cho em (xin|biết)|vui lòng|cần biết|cho biết|could you|please (tell|let)", reply, re.I)
    if case.get("expect_question") and not asks:
        fails.append("đáng lẽ phải hỏi lại khách")
    # hallucination check
    reply_nums = money_numbers(reply)
    if case.get("expect_no_price") and reply_nums:
        fails.append(f"bịa giá: {sorted(reply_nums)}")
    bad = ungrounded_amounts(reply, all_trace or trace)
    if bad and not case.get("expect_no_price"):
        fails.append(f"giá không có trong dữ liệu (nghi bịa): {sorted(bad)}")
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="override LLM_MODEL")
    ap.add_argument("--base-url", help="override LLM_BASE_URL")
    ap.add_argument("--only", help="run a single case id")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    cases = json.loads((ROOT / "eval" / "cases.json").read_text(encoding="utf-8"))
    if args.only:
        cases = [c for c in cases if c["id"] == args.only]
    today = date.today()
    tmp = Path(tempfile.mkdtemp()) / "eval.db"  # fresh DB, never touches the demo data
    reset_database(str(tmp), today=today)
    conn = connect(str(tmp))
    llm = LLMClient(base_url=args.base_url, model=args.model)
    agent = ReceptionistAgent(conn, llm)

    print(f"Model: {llm.model} @ {llm.base_url}  ·  {len(cases)} case\n")
    results, passed, total_latency = [], 0, 0.0
    for case in cases:
        hotel = get_hotel(conn, case["hotel"])
        turns = [render(t, today) for t in case.get("turns", [case.get("message")])]
        msg = turns[-1]
        try:
            # multi-turn cases persist messages (temp DB) so later turns see the history
            all_trace = []
            for t in turns:
                res = agent.run(hotel["id"], f"eval-{case['id']}", t, today=today, now=datetime.now(),
                                save=len(turns) > 1)
                all_trace += res.trace
        except LLMError as e:
            print(f"LỖI LLM: {e}")
            sys.exit(2)
        fails = check(case, res.reply, res.trace, today, all_trace)
        ok = not fails
        passed += ok
        total_latency += res.latency_s
        print(f"{'PASS' if ok else 'FAIL'}  {case['id']:<22} {res.latency_s:>6.1f}s  " + ("; ".join(fails)))
        if args.verbose or not ok:
            print(f"      khách: {msg}\n      AI   : {res.reply[:400].replace(chr(10), ' / ')}")
            for t in res.trace:
                print(f"      tool : {t['tool']}({json.dumps(t['args'], ensure_ascii=False)})")
        results.append({"id": case["id"], "ok": ok, "fails": fails, "message": msg, "reply": res.reply,
                        "trace": res.trace, "latency_s": res.latency_s})

    print(f"\nKết quả: {passed}/{len(cases)} đạt ({passed / len(cases):.0%}) · "
          f"trung bình {total_latency / max(len(cases), 1):.1f}s/tin")
    out = ROOT / "eval" / "results" / f"{llm.model.replace(':', '_').replace('/', '_')}-{datetime.now():%Y%m%d-%H%M}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"Chi tiết: {out}")


if __name__ == "__main__":
    main()
