import { Fragment, useState } from "react";
import { sapi } from "../api";
import { addDays, diffDays, dm, parseISO, todayISO, weekday } from "../format";
import { useLoad } from "../ui";
import BookingDrawer from "./BookingDrawer";
import NewBooking from "./NewBooking";

const DAYS = 14;

export default function CalendarPage() {
  const [start, setStart] = useState(todayISO());
  const { data, reload } = useLoad(() => sapi.get(`/staff/calendar?start=${start}&days=${DAYS}`), [start], 15000);
  const [open, setOpen] = useState<string | null>(null);
  const [create, setCreate] = useState<any>(null);

  const dates = Array.from({ length: DAYS }, (_, i) => addDays(start, i));
  const today = todayISO();

  return (
    <div className="grid">
      <div className="page-head">
        <h1>Sơ đồ phòng</h1>
        <div className="row">
          <button className="btn btn-sm" onClick={() => setStart(addDays(start, -7))}>← 7 ngày</button>
          <button className="btn btn-sm" onClick={() => setStart(todayISO())}>Hôm nay</button>
          <button className="btn btn-sm" onClick={() => setStart(addDays(start, 7))}>7 ngày →</button>
        </div>
        <span className="spacer" />
        <div className="row small muted">
          <span className="badge badge-warn">Giữ chỗ</span><span className="badge badge-info">Đã xác nhận</span>
          <span className="badge badge-ok">Đang ở</span><span>✦ = do AI tạo</span>
          <span><i className="dot dot-dirty" /> cần dọn</span><span><i className="dot dot-maint" /> bảo trì</span>
        </div>
      </div>
      <p className="muted small" style={{ margin: 0 }}>Bấm vào ô trống để tạo booking, bấm vào thanh để xem chi tiết.</p>
      {!data ? <div className="muted">Đang tải…</div> : (
        <div className="tape">
          <div className="tape-grid" style={{ gridTemplateColumns: `140px repeat(${DAYS}, minmax(56px, 1fr))` }}>
            <div className="tape-cell tape-head tape-room">Phòng</div>
            {dates.map((d) => {
              const wd = parseISO(d).getDay();
              return <div key={d} className={`tape-cell tape-head ${wd === 5 || wd === 6 ? "weekend" : ""}`}>{weekday(d)}<br />{dm(d)}</div>;
            })}
            {data.rooms.map((r: any, ri: number) => {
              const row = ri + 2;
              const bookings = data.bookings.filter((b: any) => b.room_id === r.id);
              const busy = new Set<string>();
              bookings.forEach((b: any) => { for (let d = b.check_in; d < b.check_out; d = addDays(d, 1)) busy.add(d); });
              return (
                <Fragment key={r.id}>
                  <div className="tape-cell tape-room" style={{ gridRow: row, gridColumn: 1 }}>
                    {r.number}
                    <i className={`dot ${r.status === "maintenance" ? "dot-maint" : r.housekeeping === "dirty" ? "dot-dirty" : "dot-clean"}`}
                       title={r.status === "maintenance" ? "Bảo trì" : r.housekeeping === "dirty" ? "Cần dọn" : "Sạch"} />
                    <small>{r.type_code}</small>
                  </div>
                  {dates.map((d, di) => {
                    const wd = parseISO(d).getDay();
                    const free = !busy.has(d) && r.status === "active" && d >= today;
                    return (
                      <div key={d} style={{ gridRow: row, gridColumn: di + 2 }}
                        className={`tape-cell ${wd === 5 || wd === 6 ? "weekend" : ""} ${d === today ? "today" : ""} ${free ? "free" : ""}`}
                        onClick={() => free && setCreate({ room_id: r.id, type_code: r.type_code, check_in: d })} />
                    );
                  })}
                  {bookings.map((b: any) => {
                    const s = Math.max(0, diffDays(start, b.check_in));
                    const e = Math.min(DAYS, diffDays(start, b.check_out));
                    if (e <= 0 || s >= DAYS) return null;
                    return (
                      <div key={b.code} className={`tape-bar s-${b.status} ${b.channel === "ai-chat" ? "ai" : ""}`}
                        style={{ gridRow: row, gridColumn: `${s + 2} / ${e + 2}` }} title={`${b.code} · ${b.guest_name} · ${b.status}`}
                        onClick={() => setOpen(b.code)}>
                        {b.guest_name}
                      </div>
                    );
                  })}
                </Fragment>
              );
            })}
          </div>
        </div>
      )}
      {open && <BookingDrawer code={open} onClose={() => setOpen(null)} onChanged={reload} />}
      {create && <NewBooking initial={create} onClose={() => setCreate(null)} onCreated={(c) => { setCreate(null); reload(); setOpen(c); }} />}
    </div>
  );
}
