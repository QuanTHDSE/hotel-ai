import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { sapi } from "../api";
import { ago, dm, time, vnd } from "../format";
import { Badge, Empty, ErrorText, StatusBadge, useAction, useLoad } from "../ui";
import { useStaff } from "./Layout";

export default function Inbox() {
  const { sid } = useParams();
  const nav = useNavigate();
  const { refreshCounts } = useStaff();
  const list = useLoad(() => sapi.get<any[]>("/staff/conversations"), [], 4000);

  return (
    <div className="grid">
      <div className="page-head">
        <h1>Hội thoại AI</h1>
        <span className="muted small">Xem AI đã trả lời gì, đã tra dữ liệu gì. Nhân viên có thể tiếp quản bất kỳ lúc nào.</span>
      </div>
      <div className="inbox">
        <div className="card conv-list">
          {list.data && !list.data.length && <Empty>Chưa có cuộc trò chuyện nào. Mở website khách và chat thử.</Empty>}
          {list.data?.map((c) => (
            <div key={c.session_id} className={`conv ${c.session_id === sid ? "active" : ""}`} onClick={() => nav(`/staff/inbox/${c.session_id}`)}>
              <div className="row">
                <b>{c.guest_name || c.session_id}</b><span className="spacer" />
                <span className="small muted">{ago(c.last_message_at)}</span>
              </div>
              <div className="row">
                {c.mode === "staff" ? <Badge tone="warn">Chờ nhân viên</Badge> : <Badge tone="ai">AI đang trả lời</Badge>}
                {!!c.n_bookings && <Badge tone="ok">{c.n_bookings} giữ chỗ</Badge>}
                <span className="small muted">{c.channel} · {c.n_messages} tin</span>
              </div>
              {c.handoff_reason && <div className="small" style={{ color: "var(--warn)" }}>⚑ {c.handoff_reason}</div>}
              <div className="last">{c.last_role === "user" ? "Khách: " : c.last_role === "staff" ? "NV: " : "AI: "}{c.last_message}</div>
            </div>
          ))}
        </div>
        {sid ? <Thread sid={sid} onChanged={() => { list.reload(); refreshCounts(); }} />
          : <div className="card"><Empty>Chọn một cuộc trò chuyện.</Empty></div>}
      </div>
    </div>
  );
}

function Thread({ sid, onChanged }: { sid: string; onChanged: () => void }) {
  const { data, reload, error } = useLoad(() => sapi.get(`/staff/conversations/${sid}`), [sid], 3000);
  const [text, setText] = useState("");
  const act = useAction();
  const body = useRef<HTMLDivElement>(null);
  const count = data?.messages.length;
  useEffect(() => { body.current?.scrollTo({ top: 1e9 }); }, [count, sid]);

  if (error) return <div className="card"><ErrorText error={error} /></div>;
  if (!data) return <div className="card muted">Đang tải…</div>;
  const s = data.session;

  const setMode = (mode: string) => act.run(async () => { await sapi.post(`/staff/conversations/${sid}/mode`, { mode }); await reload(); onChanged(); });
  const reply = () => text.trim() && act.run(async () => {
    await sapi.post(`/staff/conversations/${sid}/reply`, { text });
    setText("");
    await reload();
    onChanged();
  });

  return (
    <div className="card thread">
      <div className="thread-head">
        <b>{s.guest_name || s.session_id}</b>
        {s.mode === "staff" ? <Badge tone="warn">Nhân viên đang xử lý — AI tạm dừng</Badge> : <Badge tone="ai">AI đang trả lời</Badge>}
        <span className="spacer" />
        {s.mode === "ai"
          ? <button className="btn btn-sm" disabled={act.busy} onClick={() => setMode("staff")}>Tiếp quản</button>
          : <button className="btn btn-sm" disabled={act.busy} onClick={() => setMode("ai")}>Trả lại cho AI</button>}
      </div>
      {(data.bookings.length > 0 || data.handoffs.length > 0) && (
        <div style={{ padding: "8px 16px", borderBottom: "1px solid var(--line)" }} className="small">
          {data.bookings.map((b: any) => (
            <div key={b.code} className="row">✓ Giữ chỗ <b>{b.code}</b> · {dm(b.check_in)}→{dm(b.check_out)} · {vnd(b.total_price)} <StatusBadge status={b.status} /></div>
          ))}
          {data.handoffs.map((h: any) => (
            <div key={h.id} style={{ color: h.status === "open" ? "var(--warn)" : "var(--muted)" }}>⚑ AI chuyển nhân viên: {h.reason} {h.summary && `— ${h.summary}`} ({h.status === "open" ? "đang mở" : "đã xử lý"})</div>
          ))}
        </div>
      )}
      <div className="thread-body" ref={body}>
        {data.messages.map((m: any) => (
          <div key={m.id} className={`msg ${m.role === "user" ? "msg-inbox-user" : m.role === "staff" ? "msg-staff msg-inbox-bot" : "msg-assistant msg-inbox-bot"}`}>
            <div className="msg-who">{m.role === "user" ? "Khách" : m.role === "staff" ? "Nhân viên" : "AI"} · {time(m.created_at)}{m.latency_s ? ` · ${m.latency_s}s` : ""}</div>
            {m.content}
            {m.role === "assistant" && (
              <details>
                <summary>{m.trace.length ? `AI đã gọi ${m.trace.length} công cụ` : "AI không tra dữ liệu"}</summary>
                {m.trace.map((t: any, i: number) => (
                  <div className="trace" key={i}><b>{t.tool}</b>({JSON.stringify(t.args)}){"\n"}→ {JSON.stringify(t.result, null, 1).slice(0, 2500)}</div>
                ))}
              </details>
            )}
          </div>
        ))}
      </div>
      <ErrorText error={act.error} />
      <form className="thread-foot" onSubmit={(e) => { e.preventDefault(); reply(); }}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Trả lời khách (sẽ tiếp quản cuộc trò chuyện)…" />
        <button className="btn btn-primary" disabled={act.busy || !text.trim()}>Gửi</button>
      </form>
    </div>
  );
}
