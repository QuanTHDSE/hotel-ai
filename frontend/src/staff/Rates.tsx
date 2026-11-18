import { useState } from "react";
import { sapi } from "../api";
import { addDays, dm, dmy, parseISO, todayISO, vnd, weekday } from "../format";
import { ErrorText, Field, Modal, useAction, useLoad } from "../ui";
import { useStaff } from "./Layout";

const WD = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"];

export default function RatesPage() {
  const { isManager } = useStaff();
  const [start, setStart] = useState(todayISO());
  const matrix = useLoad(() => sapi.get(`/staff/prices?start=${start}&days=14`), [start]);
  const rules = useLoad(() => sapi.get<any[]>("/staff/rate-rules"));
  const types = useLoad(() => sapi.get<any[]>("/staff/room-types"));
  const [edit, setEdit] = useState<any>(null);
  const act = useAction();

  const refresh = () => { rules.reload(); matrix.reload(); };
  const del = (id: number) => window.confirm("Xóa quy tắc giá này?") &&
    act.run(async () => { await sapi.del(`/staff/rate-rules/${id}`); refresh(); });

  return (
    <div className="grid">
      <div className="page-head"><h1>Giá phòng</h1>
        <span className="muted small">Giá đêm = giá gốc × hệ số mùa (ưu tiên cao nhất) × hệ số ngày trong tuần. AI báo giá đúng theo bảng này.</span>
      </div>
      <div className="card">
        <div className="card-head">
          <h2>Bảng giá 14 ngày</h2>
          <div className="row">
            <button className="btn btn-sm" onClick={() => setStart(addDays(start, -14))}>←</button>
            <input type="date" style={{ width: 160 }} value={start} onChange={(e) => e.target.value && setStart(e.target.value)} />
            <button className="btn btn-sm" onClick={() => setStart(addDays(start, 14))}>→</button>
          </div>
        </div>
        {matrix.data && (
          <div className="table-wrap"><table>
            <thead><tr><th>Loại phòng</th>{matrix.data.dates.map((d: string) => {
              const wd = parseISO(d).getDay();
              return <th key={d} className="num" style={wd === 5 || wd === 6 ? { color: "var(--accent)" } : undefined}>{weekday(d)}<br />{dm(d)}</th>;
            })}</tr></thead>
            <tbody>{matrix.data.room_types.map((t: any) => (
              <tr key={t.code}><td><b>{t.code}</b> <span className="small muted">{vnd(t.base_price)}</span></td>
                {t.prices.map((p: number, i: number) => (
                  <td key={i} className="num small" style={p !== t.base_price ? { fontWeight: 600 } : { color: "var(--muted)" }}>
                    {(p / 1000).toLocaleString("vi-VN")}k
                  </td>
                ))}</tr>
            ))}</tbody>
          </table></div>
        )}
      </div>
      <div className="card">
        <div className="card-head"><h2>Quy tắc giá</h2>
          {isManager && <button className="btn" onClick={() => setEdit({ kind: "season", name: "", start_date: todayISO(), end_date: addDays(todayISO(), 7),
            weekdays: [], multiplier: 1.2, priority: 10, room_type_id: null })}>+ Thêm quy tắc</button>}
        </div>
        <ErrorText error={act.error} />
        <div className="table-wrap"><table>
          <thead><tr><th>Tên</th><th>Loại</th><th>Áp dụng</th><th>Phòng</th><th className="num">Hệ số</th><th className="num">Ưu tiên</th><th /></tr></thead>
          <tbody>{rules.data?.map((r) => (
            <tr key={r.id}>
              <td>{r.name}</td><td>{r.kind === "season" ? "Theo mùa" : "Ngày trong tuần"}</td>
              <td className="small">{r.kind === "season" ? `${dmy(r.start_date)} → ${dmy(r.end_date)}` : r.weekdays.map((w: number) => WD[w]).join(", ")}</td>
              <td className="small">{r.room_type_name || "Tất cả"}</td>
              <td className="num">×{r.multiplier}</td><td className="num">{r.kind === "season" ? r.priority : "–"}</td>
              <td>{isManager && <div className="row">
                <button className="btn btn-sm" onClick={() => setEdit(r)}>Sửa</button>
                <button className="btn btn-sm btn-danger" onClick={() => del(r.id)}>Xóa</button></div>}</td>
            </tr>
          ))}</tbody>
        </table></div>
      </div>
      {edit && <RuleForm value={edit} types={types.data || []} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); refresh(); }} />}
    </div>
  );
}

function RuleForm({ value, types, onClose, onSaved }: { value: any; types: any[]; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState<any>({ ...value });
  const act = useAction();
  const save = () => act.run(async () => {
    const body = { ...f, room_type_id: f.room_type_id ? +f.room_type_id : null, multiplier: +f.multiplier, priority: +f.priority,
      start_date: f.kind === "season" ? f.start_date : null, end_date: f.kind === "season" ? f.end_date : null };
    if (value.id) await sapi.put(`/staff/rate-rules/${value.id}`, body);
    else await sapi.post("/staff/rate-rules", body);
    onSaved();
  });
  const toggleWd = (w: number) => setF({ ...f, weekdays: f.weekdays.includes(w) ? f.weekdays.filter((x: number) => x !== w) : [...f.weekdays, w] });
  return (
    <Modal title={value.id ? "Sửa quy tắc giá" : "Thêm quy tắc giá"} onClose={onClose}>
      <form className="form" onSubmit={(e) => { e.preventDefault(); save(); }}>
        <div className="form-2">
          <Field label="Tên"><input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Loại">
            <select value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
              <option value="season">Theo mùa / dịp lễ</option><option value="weekday">Ngày trong tuần</option>
            </select>
          </Field>
        </div>
        {f.kind === "season" ? (
          <div className="form-2">
            <Field label="Từ ngày"><input type="date" value={f.start_date || ""} onChange={(e) => setF({ ...f, start_date: e.target.value })} /></Field>
            <Field label="Đến ngày (gồm)"><input type="date" value={f.end_date || ""} onChange={(e) => setF({ ...f, end_date: e.target.value })} /></Field>
          </div>
        ) : (
          <Field label="Các đêm áp dụng">
            <div className="row">{WD.map((w, i) => (
              <label key={w} className="row small"><input type="checkbox" checked={f.weekdays.includes(i)} onChange={() => toggleWd(i)} /> {w}</label>
            ))}</div>
          </Field>
        )}
        <div className="form-2">
          <Field label="Hệ số giá" hint="1.2 = tăng 20%, 0.9 = giảm 10%"><input type="number" step="0.05" value={f.multiplier} onChange={(e) => setF({ ...f, multiplier: e.target.value })} /></Field>
          {f.kind === "season" && <Field label="Ưu tiên" hint="Nhiều mùa trùng nhau: lấy ưu tiên cao nhất"><input type="number" value={f.priority} onChange={(e) => setF({ ...f, priority: e.target.value })} /></Field>}
        </div>
        <Field label="Áp dụng cho">
          <select value={f.room_type_id || ""} onChange={(e) => setF({ ...f, room_type_id: e.target.value || null })}>
            <option value="">Tất cả loại phòng</option>
            {types.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </Field>
        <ErrorText error={act.error} />
        <button className="btn btn-primary" disabled={act.busy}>Lưu</button>
      </form>
    </Modal>
  );
}
