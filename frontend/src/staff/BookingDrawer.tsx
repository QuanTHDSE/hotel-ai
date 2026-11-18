import { useState } from "react";
import { Link } from "react-router-dom";
import { sapi } from "../api";
import { dm, dmy, minutesLeft, todayISO, vnd, weekday } from "../format";
import { ChannelBadge, ErrorText, Field, Modal, StatusBadge, useAction, useLoad } from "../ui";

/** Booking detail with status actions, edit and room move. */
export default function BookingDrawer({ code, onClose, onChanged }: { code: string; onClose: () => void; onChanged: () => void }) {
  const { data, reload, error } = useLoad(() => sapi.get(`/staff/bookings/${code}`), [code]);
  const act = useAction();
  const [edit, setEdit] = useState<any>(null);

  const action = (a: string, confirmMsg?: string) => {
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    act.run(async () => {
      await sapi.post(`/staff/bookings/${code}/${a}`);
      await reload();
      onChanged();
    });
  };
  const save = () => act.run(async () => {
    await sapi.patch(`/staff/bookings/${code}`, edit);
    setEdit(null);
    await reload();
    onChanged();
  });

  const b = data?.booking;
  const left = b?.status === "hold" ? minutesLeft(b.hold_expires_at) : null;

  return (
    <Modal title={`Booking ${code}`} onClose={onClose} wide>
      <ErrorText error={error || act.error} />
      {!b ? <div className="muted">Đang tải…</div> : (
        <div className="grid grid-2">
          <div className="form">
            <div className="row">
              <StatusBadge status={b.status} /> <ChannelBadge channel={b.channel} />
              {left !== null && <span className="small" style={{ color: "var(--warn)" }}>hết hạn sau {left} phút</span>}
            </div>
            {!edit ? (
              <table>
                <tbody>
                  <tr><th>Khách</th><td>{b.guest_name}</td></tr>
                  <tr><th>Điện thoại</th><td>{b.phone}</td></tr>
                  {b.email && <tr><th>Email</th><td>{b.email}</td></tr>}
                  <tr><th>Phòng</th><td>{b.room_number} · {b.room_type_name}</td></tr>
                  <tr><th>Ngày</th><td>{dmy(b.check_in)} → {dmy(b.check_out)}</td></tr>
                  <tr><th>Số khách</th><td>{b.adults} người lớn{b.children ? `, ${b.children} trẻ em` : ""}</td></tr>
                  <tr><th>Tổng tiền</th><td><b>{vnd(b.total_price)}</b></td></tr>
                  {b.note && <tr><th>Ghi chú</th><td>{b.note}</td></tr>}
                  <tr><th>Tạo lúc</th><td className="small muted">{b.created_at}</td></tr>
                </tbody>
              </table>
            ) : (
              <div className="form">
                <div className="form-2">
                  <Field label="Khách"><input value={edit.guest_name} onChange={(e) => setEdit({ ...edit, guest_name: e.target.value })} /></Field>
                  <Field label="Điện thoại"><input value={edit.phone} onChange={(e) => setEdit({ ...edit, phone: e.target.value })} /></Field>
                  <Field label="Nhận phòng"><input type="date" value={edit.check_in} onChange={(e) => setEdit({ ...edit, check_in: e.target.value })} /></Field>
                  <Field label="Trả phòng"><input type="date" value={edit.check_out} onChange={(e) => setEdit({ ...edit, check_out: e.target.value })} /></Field>
                </div>
                <Field label="Chuyển phòng" hint="Chỉ hiện các phòng trống cho đúng ngày hiện tại của booking">
                  <select value={edit.room_id} onChange={(e) => setEdit({ ...edit, room_id: +e.target.value })}>
                    <option value={b.room_id}>{b.room_number} · {b.room_type_name} (hiện tại)</option>
                    {data.movable_rooms.filter((r: any) => r.id !== b.room_id).map((r: any) => (
                      <option key={r.id} value={r.id}>{r.number} · {r.type_name}</option>
                    ))}
                  </select>
                </Field>
                <Field label="Ghi chú"><textarea value={edit.note || ""} onChange={(e) => setEdit({ ...edit, note: e.target.value })} /></Field>
                <div className="row">
                  <button className="btn btn-primary" disabled={act.busy} onClick={save}>Lưu</button>
                  <button className="btn" onClick={() => setEdit(null)}>Hủy sửa</button>
                </div>
              </div>
            )}
            {!edit && (
              <div className="row">
                {b.status === "hold" && <button className="btn btn-ok" disabled={act.busy} onClick={() => action("confirm")}>Xác nhận đặt phòng</button>}
                {["confirmed", "hold"].includes(b.status) && b.check_in <= todayISO() &&
                  <button className="btn btn-primary" disabled={act.busy} onClick={() => action("check-in")}>Check-in</button>}
                {b.status === "checked_in" && <button className="btn btn-primary" disabled={act.busy} onClick={() => action("check-out")}>Check-out</button>}
                {["hold", "confirmed", "checked_in"].includes(b.status) &&
                  <button className="btn" onClick={() => setEdit({ guest_name: b.guest_name, phone: b.phone, note: b.note, room_id: b.room_id,
                    check_in: b.check_in, check_out: b.check_out })}>Sửa / chuyển phòng</button>}
                {["hold", "confirmed"].includes(b.status) &&
                  <button className="btn btn-danger" disabled={act.busy} onClick={() => action("cancel", "Hủy booking này?")}>Hủy</button>}
              </div>
            )}
          </div>
          <div className="form">
            <h3>Giá từng đêm</h3>
            <table>
              <tbody>
                {data.nights.map((n: any) => (
                  <tr key={n.date}><td>{weekday(n.date)} {dm(n.date)}</td><td className="num">{vnd(n.price)}</td></tr>
                ))}
              </tbody>
            </table>
            {b.session_id && (
              <>
                <div className="row"><h3>Hội thoại với AI</h3><span className="spacer" />
                  <Link className="small" to={`/staff/inbox/${b.session_id}`}>Mở trong hộp thư →</Link></div>
                <div className="form" style={{ maxHeight: 260, overflowY: "auto" }}>
                  {data.conversation.map((m: any) => (
                    <div key={m.id} className="small"><b>{m.role === "user" ? "Khách" : m.role === "staff" ? "Nhân viên" : "AI"}:</b> {m.content}</div>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}
