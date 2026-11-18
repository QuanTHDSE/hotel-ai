import { useState } from "react";
import { sapi } from "../api";
import { CHANNEL, dm, STATUS, vnd } from "../format";
import { ChannelBadge, Empty, StatusBadge, useLoad } from "../ui";
import BookingDrawer from "./BookingDrawer";
import NewBooking from "./NewBooking";

export default function BookingsPage() {
  const [f, setF] = useState({ status: "", channel: "", q: "" });
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v) as any).toString();
  const { data, reload } = useLoad(() => sapi.get<any[]>(`/staff/bookings?${qs}`), [qs]);
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  return (
    <div className="grid">
      <div className="page-head">
        <h1>Đặt phòng</h1><span className="spacer" />
        <button className="btn btn-primary" onClick={() => setCreating(true)}>+ Tạo booking</button>
      </div>
      <div className="card row">
        <input style={{ maxWidth: 260 }} placeholder="Tìm tên, SĐT, mã…" value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} />
        <select style={{ maxWidth: 180 }} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
          <option value="">Mọi trạng thái</option>
          {Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <select style={{ maxWidth: 180 }} value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}>
          <option value="">Mọi nguồn</option>
          {Object.entries(CHANNEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <span className="muted small">{data?.length ?? 0} booking</span>
      </div>
      <div className="card table-wrap" style={{ padding: 0 }}>
        {data && !data.length ? <Empty>Không có booking phù hợp.</Empty> : (
          <table>
            <thead><tr><th>Mã</th><th>Khách</th><th>Phòng</th><th>Nhận</th><th>Trả</th><th className="num">Tổng</th><th>Nguồn</th><th>Trạng thái</th></tr></thead>
            <tbody>{data?.map((b) => (
              <tr key={b.code} className="clickable" onClick={() => setOpen(b.code)}>
                <td>{b.code}</td><td>{b.guest_name}<div className="small muted">{b.phone}</div></td>
                <td>{b.room_number} <span className="small muted">{b.room_type_code}</span></td>
                <td>{dm(b.check_in)}</td><td>{dm(b.check_out)}</td><td className="num">{vnd(b.total_price)}</td>
                <td><ChannelBadge channel={b.channel} /></td><td><StatusBadge status={b.status} /></td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>
      {open && <BookingDrawer code={open} onClose={() => setOpen(null)} onChanged={reload} />}
      {creating && <NewBooking onClose={() => setCreating(false)} onCreated={(c) => { setCreating(false); reload(); setOpen(c); }} />}
    </div>
  );
}
