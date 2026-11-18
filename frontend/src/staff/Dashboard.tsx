import { useState } from "react";
import { Link } from "react-router-dom";
import { sapi } from "../api";
import { dm, minutesLeft, vnd, weekday } from "../format";
import { ChannelBadge, Empty, ErrorText, useAction, useLoad } from "../ui";
import BookingDrawer from "./BookingDrawer";
import { useStaff } from "./Layout";

export default function Dashboard() {
  const { staff, refreshCounts } = useStaff();
  const { data: d, reload, error } = useLoad(() => sapi.get("/staff/dashboard"), [], 8000);
  const [open, setOpen] = useState<string | null>(null);
  const act = useAction();

  if (!d) return <div className="muted">{error || "Đang tải…"}</div>;
  const changed = () => { reload(); refreshCounts(); };
  const quick = (code: string, a: string) => act.run(async () => { await sapi.post(`/staff/bookings/${code}/${a}`); changed(); });
  const maxOcc = Math.max(1, d.total_rooms);

  return (
    <div className="grid">
      <div className="page-head">
        <h1>Tổng quan hôm nay</h1>
        <span className="muted">{weekday(d.today)} {dm(d.today)}</span>
        <span className="spacer" />
        <a className="btn" href={`/h/${staff.hotel_slug}`} target="_blank" rel="noreferrer">Thử chat như khách ↗</a>
      </div>
      <ErrorText error={act.error} />

      <div className="grid grid-stats">
        <div className="card stat"><div className="l">Công suất đêm nay</div><div className="v">{d.occupied_tonight}/{d.total_rooms}</div>
          <div className="small muted">{Math.round((d.occupied_tonight / maxOcc) * 100)}%</div></div>
        <div className="card stat"><div className="l">Khách đang ở</div><div className="v">{d.in_house}</div></div>
        <div className="card stat"><div className="l">Đến hôm nay</div><div className="v">{d.arrivals.length}</div></div>
        <div className="card stat"><div className="l">Đi hôm nay</div><div className="v">{d.departures.length}</div></div>
        <div className="card stat"><div className="l">Phòng cần dọn</div><div className="v">{d.dirty_rooms}</div></div>
      </div>

      <div className="card" style={{ borderColor: "var(--ai)" }}>
        <div className="card-head"><h2>✦ Lễ tân AI — 7 ngày qua</h2><Link to="/staff/inbox">Xem hội thoại →</Link></div>
        <div className="grid grid-stats">
          <div><div className="muted small">Cuộc trò chuyện</div><div className="price">{d.ai.conversations_7d}</div></div>
          <div><div className="muted small">Câu AI đã trả lời</div><div className="price">{d.ai.ai_replies_7d}</div></div>
          <div><div className="muted small">Thời gian trả lời TB</div><div className="price">{d.ai.avg_latency_s ?? "–"}s</div></div>
          <div><div className="muted small">Giữ chỗ do AI tạo</div><div className="price">{d.ai.ai_bookings_7d}</div></div>
          <div><div className="muted small">Doanh thu AI (đã xác nhận)</div><div className="price">{vnd(d.ai.ai_confirmed_revenue_7d)}</div></div>
          <div><div className="muted small">Đang chờ nhân viên</div>
            <div className="price" style={{ color: d.ai.waiting_staff ? "var(--accent)" : undefined }}>{d.ai.waiting_staff}</div></div>
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h2>Giữ chỗ chờ xác nhận</h2><span className="muted small">Từ AI chat và website. Tự hủy khi hết hạn.</span></div>
        {!d.holds.length ? <Empty>Không có giữ chỗ nào đang chờ.</Empty> : (
          <div className="table-wrap"><table>
            <thead><tr><th>Mã</th><th>Khách</th><th>Phòng</th><th>Ngày</th><th className="num">Tổng</th><th>Nguồn</th><th>Còn</th><th /></tr></thead>
            <tbody>{d.holds.map((b: any) => (
              <tr key={b.code} className="clickable" onClick={() => setOpen(b.code)}>
                <td>{b.code}</td><td>{b.guest_name}<div className="small muted">{b.phone}</div></td>
                <td>{b.room_number} · {b.room_type_name}</td><td>{dm(b.check_in)} → {dm(b.check_out)}</td>
                <td className="num">{vnd(b.total_price)}</td><td><ChannelBadge channel={b.channel} /></td>
                <td className="small">{minutesLeft(b.hold_expires_at)} phút</td>
                <td onClick={(e) => e.stopPropagation()}>
                  <div className="row">
                    <button className="btn btn-ok btn-sm" disabled={act.busy} onClick={() => quick(b.code, "confirm")}>Xác nhận</button>
                    <button className="btn btn-sm btn-danger" disabled={act.busy} onClick={() => quick(b.code, "cancel")}>Hủy</button>
                  </div>
                </td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </div>

      <div className="grid grid-2">
        <div className="card">
          <div className="card-head"><h2>Khách đến hôm nay</h2></div>
          {!d.arrivals.length ? <Empty>Không có.</Empty> : d.arrivals.map((b: any) => (
            <div className="row" key={b.code} style={{ padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
              <a onClick={() => setOpen(b.code)} style={{ cursor: "pointer" }}><b>{b.room_number}</b> · {b.guest_name}</a>
              <span className="small muted">{dm(b.check_out)}</span><span className="spacer" />
              <button className="btn btn-sm btn-primary" disabled={act.busy} onClick={() => quick(b.code, "check-in")}>Check-in</button>
            </div>
          ))}
        </div>
        <div className="card">
          <div className="card-head"><h2>Khách đi hôm nay</h2></div>
          {!d.departures.length ? <Empty>Không có.</Empty> : d.departures.map((b: any) => (
            <div className="row" key={b.code} style={{ padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
              <a onClick={() => setOpen(b.code)} style={{ cursor: "pointer" }}><b>{b.room_number}</b> · {b.guest_name}</a>
              <span className="spacer" />
              <button className="btn btn-sm btn-primary" disabled={act.busy} onClick={() => quick(b.code, "check-out")}>Check-out</button>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h2>Công suất 14 ngày tới</h2></div>
        <div className="bars">
          {d.occupancy.map((o: any) => {
            const pct = Math.round((o.booked / maxOcc) * 100);
            return (
              <div className="bar" key={o.date} title={`${o.date}: ${o.booked}/${o.total}`}>
                <span>{pct}%</span><i style={{ height: `${pct}%` }} /><span>{weekday(o.date)} {dm(o.date)}</span>
              </div>
            );
          })}
        </div>
      </div>
      {open && <BookingDrawer code={open} onClose={() => setOpen(null)} onChanged={changed} />}
    </div>
  );
}
