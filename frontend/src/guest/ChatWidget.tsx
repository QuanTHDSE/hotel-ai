import { useEffect, useRef, useState } from "react";
import { api } from "../api";

type Msg = { id?: number; role: "user" | "assistant" | "staff" | "system"; content: string; trace?: any[]; latency?: number };

const SUGGEST = [
  "Cuối tuần này còn phòng cho 2 người không?",
  "Gia đình 2 lớn 2 bé, ở 2 đêm từ ngày mai",
  "Chính sách hủy phòng thế nào?",
  "Có đưa đón sân bay không?",
];

function loadSession(slug: string): string {
  const key = `hotel-ai-chat-${slug}`;
  try {
    const s = localStorage.getItem(key);
    if (s) return s;
    const n = "web-" + Math.random().toString(36).slice(2, 10);
    localStorage.setItem(key, n);
    return n;
  } catch {
    return "web-" + Math.random().toString(36).slice(2, 10);
  }
}

function newSession(slug: string): string {
  const n = "web-" + Math.random().toString(36).slice(2, 10);
  try {
    localStorage.setItem(`hotel-ai-chat-${slug}`, n);
  } catch {
    /* ignore */
  }
  return n;
}

type PageSearch = { check_in: string; check_out: string; adults: number; children: number };

export default function ChatWidget({ slug, hotelName, pageSearch }: { slug: string; hotelName: string; pageSearch?: PageSearch }) {
  const [open, setOpen] = useState(false);
  const [session, setSession] = useState(() => loadSession(slug));
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [mode, setMode] = useState<"ai" | "staff">("ai");
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [debug, setDebug] = useState(false);
  const lastId = useRef(0);
  const sendingRef = useRef(false);
  const body = useRef<HTMLDivElement>(null);

  // Load existing conversation (survives page reloads).
  useEffect(() => {
    lastId.current = 0;
    api(`/chat/${slug}/${session}/messages`).then((r) => {
      setMode(r.mode);
      setMsgs(r.messages.map((m: any) => ({ id: m.id, role: m.role, content: m.content })));
      lastId.current = r.messages.length ? r.messages[r.messages.length - 1].id : 0;
    }).catch(() => {});
  }, [slug, session]);

  // Poll for replies typed by staff (or mode changes).
  useEffect(() => {
    if (!open) return;
    const id = window.setInterval(async () => {
      if (sendingRef.current) return;
      try {
        const r = await api(`/chat/${slug}/${session}/messages?after=${lastId.current}`);
        setMode(r.mode);
        const fresh = r.messages.filter((m: any) => m.role !== "user");
        if (r.messages.length) lastId.current = r.messages[r.messages.length - 1].id;
        if (fresh.length) setMsgs((prev) => [...prev, ...fresh.map((m: any) => ({ id: m.id, role: m.role, content: m.content }))]);
      } catch {
        /* offline: try again next tick */
      }
    }, 3000);
    return () => window.clearInterval(id);
  }, [open, slug, session]);

  useEffect(() => {
    body.current?.scrollTo({ top: 1e9 });
  }, [msgs, open, sending]);

  async function send(t: string) {
    t = t.trim();
    if (!t || sending) return;
    setText("");
    setMsgs((m) => [...m, { role: "user", content: t }]);
    setSending(true);
    sendingRef.current = true;
    try {
      const r = await api("/chat", { body: { hotel: slug, session_id: session, message: t, page_search: pageSearch } });
      setMode(r.mode);
      lastId.current = r.last_id;
      if (r.reply) setMsgs((m) => [...m, { role: "assistant", content: r.reply, trace: r.trace, latency: r.latency_s }]);
    } catch (e: any) {
      setMsgs((m) => [...m, { role: "system", content: "⚠ " + e.message }]);
    } finally {
      setSending(false);
      sendingRef.current = false;
    }
  }

  async function reset() {
    await api("/chat/reset", { body: { hotel: slug, session_id: session } }).catch(() => {});
    setMsgs([]);
    setMode("ai");
    setSession(newSession(slug));
  }

  if (!open) return <button className="fab" onClick={() => setOpen(true)}>💬 Chat với lễ tân</button>;

  return (
    <div className="widget">
      <div className="widget-head">
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600 }}>{hotelName}</div>
          <div className="small" style={{ opacity: 0.85 }}>{mode === "staff" ? "Nhân viên đang hỗ trợ" : "Lễ tân AI · trả lời 24/7"}</div>
        </div>
        <button className="icon-btn" title="Hiện công cụ AI đã gọi" onClick={() => setDebug(!debug)}>{debug ? "🔍" : "🛈"}</button>
        <button className="icon-btn" title="Cuộc trò chuyện mới" onClick={reset}>↻</button>
        <button className="icon-btn" onClick={() => setOpen(false)} aria-label="Thu nhỏ">—</button>
      </div>
      {mode === "staff" && <div className="banner">Cuộc trò chuyện đã được chuyển cho nhân viên. Họ sẽ trả lời tại đây.</div>}
      <div className="widget-body" ref={body}>
        {!msgs.length && (
          <div className="msg msg-assistant">Xin chào! Em là lễ tân ảo của {hotelName}. Anh/chị cần em hỗ trợ gì ạ?</div>
        )}
        {msgs.map((m, i) => (
          <div key={m.id ?? `l${i}`} className={`msg msg-${m.role === "system" ? "assistant" : m.role}`}>
            {m.role === "staff" && <div className="msg-who">Nhân viên</div>}
            {m.content}
            {debug && m.trace && (
              <div className="trace">
                {m.trace.length ? m.trace.map((t, j) => (
                  <div key={j}><b>{t.tool}</b>({JSON.stringify(t.args)}){"\n"}→ {JSON.stringify(t.result).slice(0, 600)}</div>
                )) : "(không gọi công cụ)"}
                {"\n"}⏱ {m.latency}s
              </div>
            )}
          </div>
        ))}
        {sending && <div className="typing">{mode === "staff" ? "Đã gửi" : "Lễ tân đang trả lời…"}</div>}
      </div>
      {!msgs.length && (
        <div className="suggest">{SUGGEST.map((s) => <button key={s} onClick={() => send(s)}>{s}</button>)}</div>
      )}
      <form className="widget-foot" onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Nhập tin nhắn…" autoFocus />
        <button className="btn btn-primary" disabled={sending || !text.trim()}>Gửi</button>
      </form>
    </div>
  );
}
