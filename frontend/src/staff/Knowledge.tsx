import { useEffect, useState } from "react";
import { sapi } from "../api";
import { ErrorText, Field, Modal, useAction, useLoad } from "../ui";
import { useStaff } from "./Layout";

export default function KnowledgePage() {
  const { isManager } = useStaff();
  const policies = useLoad(() => sapi.get<any[]>("/staff/policies"));
  const hotel = useLoad(() => sapi.get("/staff/hotel"));
  const [edit, setEdit] = useState<any>(null);
  const act = useAction();

  const del = (id: number) => window.confirm("Xóa mục này? AI sẽ không còn biết thông tin này.") &&
    act.run(async () => { await sapi.del(`/staff/policies/${id}`); policies.reload(); });

  return (
    <div className="grid">
      <div className="page-head"><h1>Kiến thức AI</h1>
        <span className="muted small">AI chỉ trả lời chính sách/tiện ích dựa trên các mục dưới đây. Sửa xong là AI dùng ngay, không cần train lại.</span>
      </div>
      {hotel.data && <HotelForm hotel={hotel.data} disabled={!isManager} onSaved={hotel.reload} />}
      <div className="card">
        <div className="card-head"><h2>Chính sách & FAQ ({policies.data?.length ?? 0})</h2>
          {isManager && <button className="btn" onClick={() => setEdit({ category: "", title: "", content: "", keywords: "" })}>+ Thêm mục</button>}
        </div>
        <ErrorText error={act.error} />
        <div className="grid">
          {policies.data?.map((p) => (
            <div key={p.id} style={{ borderBottom: "1px solid var(--line)", paddingBottom: 10 }}>
              <div className="row"><b>{p.title}</b><span className="small muted">{p.category}</span><span className="spacer" />
                {isManager && <><button className="btn btn-sm" onClick={() => setEdit(p)}>Sửa</button>
                  <button className="btn btn-sm btn-danger" onClick={() => del(p.id)}>Xóa</button></>}
              </div>
              <div className="small">{p.content}</div>
              {p.keywords && <div className="small muted">Từ khóa: {p.keywords}</div>}
            </div>
          ))}
        </div>
      </div>
      {isManager && <DemoReset />}
      {edit && <PolicyForm value={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); policies.reload(); }} />}
    </div>
  );
}

function HotelForm({ hotel, disabled, onSaved }: { hotel: any; disabled: boolean; onSaved: () => void }) {
  const [f, setF] = useState(hotel);
  const [saved, setSaved] = useState(false);
  const act = useAction();
  useEffect(() => setF(hotel), [hotel]);
  const set = (k: string) => (e: any) => { setF({ ...f, [k]: e.target.value }); setSaved(false); };
  const save = () => act.run(async () => { await sapi.patch("/staff/hotel", f); setSaved(true); onSaved(); });
  return (
    <form className="card form" onSubmit={(e) => { e.preventDefault(); save(); }}>
      <div className="card-head" style={{ marginBottom: 0 }}><h2>Thông tin khách sạn</h2><span className="small muted">Nằm trong lời dặn (system prompt) của AI</span></div>
      <fieldset disabled={disabled} style={{ border: 0, padding: 0, margin: 0 }} className="form">
        <div className="form-2">
          <Field label="Tên"><input value={f.name} onChange={set("name")} /></Field>
          <Field label="Hotline"><input value={f.phone} onChange={set("phone")} /></Field>
          <Field label="Địa chỉ"><input value={f.address} onChange={set("address")} /></Field>
          <Field label="Thành phố"><input value={f.city} onChange={set("city")} /></Field>
          <Field label="Giờ nhận phòng"><input value={f.checkin_time} onChange={set("checkin_time")} /></Field>
          <Field label="Giờ trả phòng"><input value={f.checkout_time} onChange={set("checkout_time")} /></Field>
        </div>
        <Field label="Mô tả"><textarea value={f.description} onChange={set("description")} /></Field>
        {!disabled && <div className="row"><button className="btn btn-primary" disabled={act.busy}>Lưu</button>{saved && <span className="small" style={{ color: "var(--ok)" }}>Đã lưu</span>}</div>}
      </fieldset>
      <ErrorText error={act.error} />
    </form>
  );
}

function PolicyForm({ value, onClose, onSaved }: { value: any; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ ...value });
  const act = useAction();
  const save = () => act.run(async () => {
    if (value.id) await sapi.put(`/staff/policies/${value.id}`, f);
    else await sapi.post("/staff/policies", f);
    onSaved();
  });
  return (
    <Modal title={value.id ? "Sửa mục kiến thức" : "Thêm mục kiến thức"} onClose={onClose}>
      <form className="form" onSubmit={(e) => { e.preventDefault(); save(); }}>
        <div className="form-2">
          <Field label="Tiêu đề"><input required value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
          <Field label="Nhóm"><input value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} placeholder="vd: facility" /></Field>
        </div>
        <Field label="Nội dung (AI sẽ trả lời dựa trên đoạn này)"><textarea rows={5} required value={f.content} onChange={(e) => setF({ ...f, content: e.target.value })} /></Field>
        <Field label="Từ khóa tìm kiếm" hint="Viết không dấu, cách nhau bằng dấu cách — giúp AI tìm đúng mục (vd: tennis san the thao)">
          <input value={f.keywords} onChange={(e) => setF({ ...f, keywords: e.target.value })} />
        </Field>
        <ErrorText error={act.error} />
        <button className="btn btn-primary" disabled={act.busy}>Lưu</button>
      </form>
    </Modal>
  );
}

function DemoReset() {
  const act = useAction();
  const reset = () => window.confirm("Xóa TOÀN BỘ dữ liệu (booking, hội thoại, thay đổi cấu hình) và tạo lại dữ liệu giả lập? Bạn sẽ bị đăng xuất.") &&
    act.run(async () => { await sapi.post("/staff/reset-demo"); window.location.href = "/staff/login"; });
  return (
    <div className="card row">
      <div><b>Dữ liệu demo</b><div className="small muted">Tạo lại 2 khách sạn giả lập với booking ngẫu nhiên 90 ngày tới.</div></div>
      <span className="spacer" />
      <button className="btn btn-danger" disabled={act.busy} onClick={reset}>Tạo lại dữ liệu demo</button>
      <ErrorText error={act.error} />
    </div>
  );
}
