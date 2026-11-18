import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { addDays, dm, todayISO, vnd, weekday } from "../format";
import { ErrorText, Field, Modal, useAction, useLoad } from "../ui";
import ChatWidget from "./ChatWidget";

const GRADIENTS = [
  "linear-gradient(135deg,#355c7d,#6c5b7b)", "linear-gradient(135deg,#0f766e,#14b8a6)",
  "linear-gradient(135deg,#9a3412,#f97316)", "linear-gradient(135deg,#1e3a8a,#3b82f6)",
];

export default function HotelSite() {
  const { slug = "" } = useParams();
  const { data, error } = useLoad(() => api(`/hotels/${slug}`), [slug]);
  const [q, setQ] = useState({ check_in: addDays(todayISO(), 1), check_out: addDays(todayISO(), 2), adults: 2, children: 0 });
  const [result, setResult] = useState<any>(null);
  const [booking, setBooking] = useState<any>(null);
  const search = useAction();

  if (error) return <div className="center"><div className="error-text">{error}</div></div>;
  if (!data) return <div className="center muted">Đang tải…</div>;
  const { hotel, room_types, policies } = data;

  const doSearch = () => search.run(async () => setResult(await api(`/hotels/${slug}/search`, { body: q })));

  return (
    <div className="site">
      <nav className="site-nav">
        <b>🏨 {hotel.name}</b>
        <span className="spacer" />
        <span className="small muted">{hotel.phone}</span>
        <Link className="small" to="/">← Danh sách khách sạn</Link>
      </nav>

      <header className="hero">
        <div className="hero-in">
          <div className="small" style={{ opacity: 0.85 }}>{"★".repeat(hotel.stars || 0)} {hotel.city}</div>
          <h1>{hotel.name}</h1>
          <p>{hotel.description}</p>
          <p className="small" style={{ opacity: 0.85 }}>📍 {hotel.address}, {hotel.city} · Nhận phòng {hotel.checkin_time} · Trả phòng {hotel.checkout_time}</p>
          <form className="search" onSubmit={(e) => { e.preventDefault(); doSearch(); }}>
            <Field label="Nhận phòng"><input type="date" value={q.check_in} min={todayISO()}
              onChange={(e) => setQ({ ...q, check_in: e.target.value, check_out: q.check_out <= e.target.value ? addDays(e.target.value, 1) : q.check_out })} /></Field>
            <Field label="Trả phòng"><input type="date" value={q.check_out} min={addDays(q.check_in, 1)}
              onChange={(e) => setQ({ ...q, check_out: e.target.value })} /></Field>
            <Field label="Người lớn"><input type="number" min={1} max={20} value={q.adults} onChange={(e) => setQ({ ...q, adults: +e.target.value })} /></Field>
            <Field label="Trẻ em"><input type="number" min={0} max={10} value={q.children} onChange={(e) => setQ({ ...q, children: +e.target.value })} /></Field>
            <button className="btn btn-primary" disabled={search.busy}>{search.busy ? "Đang tìm…" : "Tìm phòng"}</button>
          </form>
        </div>
      </header>

      {(result || search.error) && (
        <section className="section">
          <div className="section-in">
            <h2>Phòng trống {result && `${dm(result.check_in)} → ${dm(result.check_out)} · ${result.nights} đêm`}</h2>
            <ErrorText error={search.error} />
            <div className="grid">
              {result?.options.map((o: any) => (
                <div className="card result" key={o.room_type_code}>
                  <div>
                    <h3>{o.name}</h3>
                    <div className="small muted">
                      Tối đa {o.max_adults_per_room} người lớn{o.max_children_per_room ? `, ${o.max_children_per_room} trẻ em` : ""}/phòng
                      {o.breakfast_included && " · Gồm ăn sáng"}
                      {o.rooms_needed > 1 && ` · Cần ${o.rooms_needed} phòng`}
                    </div>
                    <div className="small muted" style={{ marginTop: 4 }}>
                      {o.nightly_prices.map((n: any) => `${weekday(n.date)} ${dm(n.date)}: ${vnd(n.price)}`).join(" · ")}
                    </div>
                  </div>
                  <div style={{ textAlign: "right" }}>
                    <div className="price">{vnd(o.total_for_party ?? o.total_per_room)}</div>
                    <div className="small muted">{o.available_rooms ? `Còn ${o.available_rooms} phòng` : "Hết phòng"}</div>
                    <button className="btn btn-primary btn-sm" style={{ marginTop: 6 }} disabled={!o.fits_party || o.rooms_needed > 1}
                      onClick={() => setBooking({ ...q, check_in: result.check_in, check_out: result.check_out, option: o })}>
                      {o.fits_party ? (o.rooms_needed > 1 ? "Chat để đặt nhiều phòng" : "Đặt phòng") : "Không phù hợp"}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      <section className="section">
        <div className="section-in">
          <h2>Hạng phòng</h2>
          <div className="rooms">
            {room_types.map((t: any, i: number) => (
              <div className="card room-card" key={t.code}>
                <div className="room-img" style={{ background: GRADIENTS[i % GRADIENTS.length] }}>{t.name}</div>
                <div className="room-body">
                  <div className="small muted">{t.bed} · {t.size_m2}m² · {t.view}</div>
                  <div className="small">{t.description}</div>
                  <div className="chips">{t.amenities.map((a: string) => <span className="chip" key={a}>{a}</span>)}</div>
                  <div style={{ marginTop: "auto" }}><span className="price">{vnd(t.base_price)}</span> <span className="small muted">/đêm ngày thường</span></div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="section">
        <div className="section-in faq">
          <h2>Chính sách & câu hỏi thường gặp</h2>
          {policies.map((p: any) => (
            <details key={p.title}><summary>{p.title}</summary><p className="small">{p.content}</p></details>
          ))}
        </div>
      </section>

      {booking && <BookingModal slug={slug} draft={booking} onClose={() => setBooking(null)} onDone={doSearch} />}
      <ChatWidget slug={slug} hotelName={hotel.name} pageSearch={q} />
    </div>
  );
}

function BookingModal({ slug, draft, onClose, onDone }: { slug: string; draft: any; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ guest_name: "", phone: "", email: "", note: "" });
  const [done, setDone] = useState<any>(null);
  const act = useAction();
  const o = draft.option;

  const submit = () => act.run(async () => {
    const r = await api(`/hotels/${slug}/bookings`, {
      body: { check_in: draft.check_in, check_out: draft.check_out, adults: draft.adults, children: draft.children,
              room_type_code: o.room_type_code, ...f },
    });
    setDone(r);
    onDone();
  });

  return (
    <Modal title={done ? "Đã giữ phòng" : `Đặt ${o.name}`} onClose={onClose}>
      {done ? (
        <div className="form">
          <div className="ok-text">Mã giữ chỗ <b>{done.code}</b> · {vnd(done.total_price)}</div>
          <p>Phòng được giữ đến <b>{done.hold_expires_at?.slice(11, 16)}</b>. Nhân viên sẽ gọi để gửi thông tin đặt cọc và xác nhận.</p>
          <button className="btn" onClick={onClose}>Đóng</button>
        </div>
      ) : (
        <form className="form" onSubmit={(e) => { e.preventDefault(); submit(); }}>
          <div className="small muted">{dm(draft.check_in)} → {dm(draft.check_out)} · {draft.adults} người lớn{draft.children ? `, ${draft.children} trẻ em` : ""} · <b>{vnd(o.total_per_room)}</b></div>
          <Field label="Họ tên"><input required value={f.guest_name} onChange={(e) => setF({ ...f, guest_name: e.target.value })} /></Field>
          <div className="form-2">
            <Field label="Số điện thoại"><input required value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Field>
            <Field label="Email (tùy chọn)"><input type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
          </div>
          <Field label="Yêu cầu thêm"><textarea value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
          <ErrorText error={act.error} />
          <button className="btn btn-primary" disabled={act.busy}>{act.busy ? "Đang gửi…" : "Giữ phòng"}</button>
        </form>
      )}
    </Modal>
  );
}
