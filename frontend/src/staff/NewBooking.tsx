import { useEffect, useState } from "react";
import { sapi } from "../api";
import { addDays, todayISO } from "../format";
import { ErrorText, Field, Modal, useAction, useLoad } from "../ui";

/** Staff creates a confirmed booking (walk-in / phone / OTA). */
export default function NewBooking({ initial, onClose, onCreated }: {
  initial?: { room_id?: number; type_code?: string; check_in?: string }; onClose: () => void; onCreated: (code: string) => void;
}) {
  const types = useLoad(() => sapi.get<any[]>("/staff/room-types"));
  const ci = initial?.check_in || todayISO();
  const [f, setF] = useState<any>({
    room_type_code: initial?.type_code || "", room_id: initial?.room_id || "", check_in: ci, check_out: addDays(ci, 1),
    adults: 2, children: 0, guest_name: "", phone: "", email: "", note: "", channel: "walk-in",
  });
  const [free, setFree] = useState<any[]>([]);
  const act = useAction();

  useEffect(() => {
    if (!f.room_type_code && types.data?.length) setF((x: any) => ({ ...x, room_type_code: types.data![0].code }));
  }, [types.data]);

  useEffect(() => {
    if (!f.room_type_code || f.check_out <= f.check_in) return;
    sapi.get(`/staff/availability?check_in=${f.check_in}&check_out=${f.check_out}&room_type_code=${f.room_type_code}`)
      .then(setFree).catch(() => setFree([]));
  }, [f.room_type_code, f.check_in, f.check_out]);

  const submit = () => act.run(async () => {
    const b = await sapi.post("/staff/bookings", { ...f, room_id: f.room_id ? +f.room_id : null });
    onCreated(b.code);
  });

  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const roomStillFree = !f.room_id || free.some((r) => r.id === +f.room_id);

  return (
    <Modal title="Tạo đặt phòng" onClose={onClose}>
      <form className="form" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <div className="form-2">
          <Field label="Loại phòng">
            <select value={f.room_type_code} onChange={(e) => setF({ ...f, room_type_code: e.target.value, room_id: "" })}>
              {types.data?.map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}
            </select>
          </Field>
          <Field label={`Phòng (${free.length} trống)`}>
            <select value={f.room_id} onChange={set("room_id")}>
              <option value="">Tự chọn phòng trống</option>
              {free.map((r) => <option key={r.id} value={r.id}>{r.number}</option>)}
            </select>
          </Field>
          <Field label="Nhận phòng"><input type="date" value={f.check_in} onChange={(e) => setF({ ...f, check_in: e.target.value,
            check_out: f.check_out <= e.target.value ? addDays(e.target.value, 1) : f.check_out })} /></Field>
          <Field label="Trả phòng"><input type="date" value={f.check_out} min={addDays(f.check_in, 1)} onChange={set("check_out")} /></Field>
          <Field label="Người lớn"><input type="number" min={1} value={f.adults} onChange={(e) => setF({ ...f, adults: +e.target.value })} /></Field>
          <Field label="Trẻ em"><input type="number" min={0} value={f.children} onChange={(e) => setF({ ...f, children: +e.target.value })} /></Field>
          <Field label="Tên khách"><input required value={f.guest_name} onChange={set("guest_name")} /></Field>
          <Field label="Điện thoại"><input required value={f.phone} onChange={set("phone")} /></Field>
          <Field label="Nguồn">
            <select value={f.channel} onChange={set("channel")}>
              <option value="walk-in">Khách vãng lai</option><option value="phone">Điện thoại</option>
              <option value="ota">OTA</option><option value="zalo">Zalo</option>
            </select>
          </Field>
          <Field label="Email"><input value={f.email} onChange={set("email")} /></Field>
        </div>
        <Field label="Ghi chú"><textarea value={f.note} onChange={set("note")} /></Field>
        {!roomStillFree && <div className="error-text">Phòng đã chọn không còn trống cho ngày này.</div>}
        <ErrorText error={act.error} />
        <button className="btn btn-primary" disabled={act.busy || !free.length || !roomStillFree}>
          {free.length ? "Tạo booking (đã xác nhận)" : "Hết phòng loại này"}
        </button>
      </form>
    </Modal>
  );
}
