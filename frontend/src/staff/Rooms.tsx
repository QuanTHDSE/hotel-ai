import { useState } from "react";
import { sapi } from "../api";
import { vnd } from "../format";
import { Badge, ErrorText, Field, Modal, useAction, useLoad } from "../ui";
import { useStaff } from "./Layout";

export default function RoomsPage() {
  const { isManager } = useStaff();
  const rooms = useLoad(() => sapi.get<any[]>("/staff/rooms"), [], 15000);
  const types = useLoad(() => sapi.get<any[]>("/staff/room-types"));
  const [editType, setEditType] = useState<any>(null);
  const [newRoom, setNewRoom] = useState<any>(null);
  const act = useAction();

  const patch = (id: number, body: any) => act.run(async () => { await sapi.patch(`/staff/rooms/${id}`, body); rooms.reload(); });

  return (
    <div className="grid">
      <div className="page-head"><h1>Phòng</h1><span className="spacer" />
        {isManager && <button className="btn" onClick={() => setNewRoom({ number: "", floor: "", room_type_id: types.data?.[0]?.id })}>+ Thêm phòng</button>}
      </div>
      <ErrorText error={act.error} />
      <div className="card table-wrap" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Phòng</th><th>Loại</th><th>Đêm nay</th><th>Dọn phòng</th><th>Trạng thái</th></tr></thead>
          <tbody>{rooms.data?.map((r) => (
            <tr key={r.id}>
              <td><b>{r.number}</b> <span className="small muted">tầng {r.floor}</span></td>
              <td>{r.type_name}</td>
              <td>{r.guest ? <Badge tone="ok">{r.guest}</Badge> : r.occupied_tonight ? <Badge tone="info">Đã đặt</Badge> : <span className="muted small">Trống</span>}</td>
              <td>
                <button className="btn btn-sm" onClick={() => patch(r.id, { housekeeping: r.housekeeping === "clean" ? "dirty" : "clean" })}>
                  <i className={`dot dot-${r.housekeeping}`} /> {r.housekeeping === "clean" ? "Sạch" : "Cần dọn"}
                </button>
              </td>
              <td>
                <button className="btn btn-sm" onClick={() => patch(r.id, { status: r.status === "active" ? "maintenance" : "active" })}>
                  {r.status === "active" ? "Đang bán" : <span style={{ color: "var(--bad)" }}>Bảo trì (AI không bán)</span>}
                </button>
              </td>
            </tr>
          ))}</tbody>
        </table>
      </div>

      <div className="page-head" style={{ marginTop: 10 }}><h2>Loại phòng</h2><span className="spacer" />
        {isManager && <button className="btn" onClick={() => setEditType({ code: "", name: "", description: "", bed: "", max_adults: 2, max_children: 0,
          size_m2: 20, view: "", amenities: [], base_price: 500000, breakfast_included: false })}>+ Thêm loại phòng</button>}
      </div>
      <div className="card table-wrap" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Mã</th><th>Tên</th><th>Sức chứa</th><th>Giường</th><th>Số phòng</th><th className="num">Giá gốc/đêm</th><th /></tr></thead>
          <tbody>{types.data?.map((t) => (
            <tr key={t.id}>
              <td><code>{t.code}</code></td><td>{t.name}{t.breakfast_included && <span className="small muted"> · ăn sáng</span>}</td>
              <td>{t.max_adults} NL{t.max_children ? ` + ${t.max_children} TE` : ""}</td><td className="small">{t.bed}</td>
              <td>{t.room_count}</td><td className="num">{vnd(t.base_price)}</td>
              <td>{isManager && <button className="btn btn-sm" onClick={() => setEditType(t)}>Sửa</button>}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      {editType && <RoomTypeForm value={editType} onClose={() => setEditType(null)} onSaved={() => { setEditType(null); types.reload(); rooms.reload(); }} />}
      {newRoom && (
        <Modal title="Thêm phòng" onClose={() => setNewRoom(null)}>
          <form className="form" onSubmit={(e) => { e.preventDefault(); act.run(async () => {
            await sapi.post("/staff/rooms", { ...newRoom, floor: newRoom.floor ? +newRoom.floor : null, room_type_id: +newRoom.room_type_id });
            setNewRoom(null); rooms.reload(); types.reload();
          }); }}>
            <div className="form-2">
              <Field label="Số phòng"><input required value={newRoom.number} onChange={(e) => setNewRoom({ ...newRoom, number: e.target.value })} /></Field>
              <Field label="Tầng"><input type="number" value={newRoom.floor} onChange={(e) => setNewRoom({ ...newRoom, floor: e.target.value })} /></Field>
            </div>
            <Field label="Loại phòng">
              <select value={newRoom.room_type_id} onChange={(e) => setNewRoom({ ...newRoom, room_type_id: e.target.value })}>
                {types.data?.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
              </select>
            </Field>
            <ErrorText error={act.error} />
            <button className="btn btn-primary" disabled={act.busy}>Thêm</button>
          </form>
        </Modal>
      )}
    </div>
  );
}

function RoomTypeForm({ value, onClose, onSaved }: { value: any; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ ...value, amenities_text: (value.amenities || []).join(", ") });
  const act = useAction();
  const set = (k: string, num = false) => (e: any) => setF({ ...f, [k]: num ? +e.target.value : e.target.value });
  const save = () => act.run(async () => {
    const body = { ...f, amenities: f.amenities_text.split(",").map((s: string) => s.trim()).filter(Boolean) };
    if (value.id) await sapi.put(`/staff/room-types/${value.id}`, body);
    else await sapi.post("/staff/room-types", body);
    onSaved();
  });
  return (
    <Modal title={value.id ? `Sửa ${value.name}` : "Thêm loại phòng"} onClose={onClose}>
      <form className="form" onSubmit={(e) => { e.preventDefault(); save(); }}>
        <div className="form-2">
          <Field label="Mã (AI dùng để gọi)"><input required value={f.code} onChange={set("code")} /></Field>
          <Field label="Tên"><input required value={f.name} onChange={set("name")} /></Field>
          <Field label="Giá gốc/đêm (VND)"><input type="number" value={f.base_price} onChange={set("base_price", true)} /></Field>
          <Field label="Giường"><input value={f.bed} onChange={set("bed")} /></Field>
          <Field label="Người lớn tối đa"><input type="number" value={f.max_adults} onChange={set("max_adults", true)} /></Field>
          <Field label="Trẻ em tối đa"><input type="number" value={f.max_children} onChange={set("max_children", true)} /></Field>
          <Field label="Diện tích m²"><input type="number" value={f.size_m2 || ""} onChange={set("size_m2", true)} /></Field>
          <Field label="View"><input value={f.view} onChange={set("view")} /></Field>
        </div>
        <Field label="Tiện nghi (phân cách bằng dấu phẩy)"><input value={f.amenities_text} onChange={set("amenities_text")} /></Field>
        <Field label="Mô tả"><textarea value={f.description} onChange={set("description")} /></Field>
        <label className="row"><input type="checkbox" checked={!!f.breakfast_included} onChange={(e) => setF({ ...f, breakfast_included: e.target.checked })} /> Gồm bữa sáng</label>
        <ErrorText error={act.error} />
        <button className="btn btn-primary" disabled={act.busy}>Lưu</button>
      </form>
    </Modal>
  );
}
