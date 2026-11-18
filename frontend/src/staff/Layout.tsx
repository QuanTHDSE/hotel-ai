import { createContext, useContext, useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { api, getToken, sapi, setToken } from "../api";
import { useLoad } from "../ui";

export type Staff = { id: number; hotel_id: number; username: string; name: string; role: "manager" | "receptionist";
  hotel_slug: string; hotel_name: string };

const StaffCtx = createContext<{ staff: Staff; isManager: boolean; refreshCounts: () => void } | null>(null);
export const useStaff = () => useContext(StaffCtx)!;

export default function StaffLayout() {
  const nav = useNavigate();
  const [staff, setStaff] = useState<Staff | null>(null);

  useEffect(() => {
    const onLogout = () => nav("/staff/login");
    window.addEventListener("staff-logout", onLogout);
    if (!getToken()) nav("/staff/login");
    else sapi.get<Staff>("/auth/me").then(setStaff).catch(() => nav("/staff/login"));
    return () => window.removeEventListener("staff-logout", onLogout);
  }, [nav]);

  const counts = useLoad(async () => (staff ? sapi.get("/staff/dashboard") : null), [staff?.id], 10000);

  if (!staff) return <div className="center muted">Đang tải…</div>;
  const d = counts.data;
  const logout = async () => {
    await api("/auth/logout", { auth: true, method: "POST" }).catch(() => {});
    setToken(null);
    nav("/staff/login");
  };

  const link = (to: string, label: string, count?: number) => (
    <NavLink to={to} end={to === "/staff"} className={({ isActive }) => `nav ${isActive ? "active" : ""}`}>
      {label}
      {!!count && <span className="count">{count}</span>}
    </NavLink>
  );

  return (
    <StaffCtx.Provider value={{ staff, isManager: staff.role === "manager", refreshCounts: counts.reload }}>
      <div className="staff">
        <aside className="side">
          <div className="side-brand">🏨 Hotel AI · PMS</div>
          <div className="side-hotel">{staff.hotel_name}</div>
          {link("/staff", "Tổng quan", d?.holds?.length)}
          {link("/staff/calendar", "Sơ đồ phòng")}
          {link("/staff/bookings", "Đặt phòng")}
          {link("/staff/inbox", "Hội thoại AI", d?.ai?.waiting_staff)}
          {link("/staff/rooms", "Phòng")}
          {link("/staff/rates", "Giá phòng")}
          {link("/staff/knowledge", "Kiến thức AI")}
          <div className="side-foot">
            <div><b>{staff.name}</b></div>
            <div className="muted small">{staff.role === "manager" ? "Quản lý" : "Lễ tân"} · {staff.username}</div>
            <div className="row" style={{ marginTop: 8 }}>
              <a className="small" href={`/h/${staff.hotel_slug}`} target="_blank" rel="noreferrer">Mở website khách ↗</a>
              <button className="btn btn-sm btn-ghost" onClick={logout}>Đăng xuất</button>
            </div>
          </div>
        </aside>
        <main className="main"><Outlet /></main>
      </div>
    </StaffCtx.Provider>
  );
}
