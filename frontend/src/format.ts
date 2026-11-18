export const vnd = (n: number | null | undefined) => (n ?? 0).toLocaleString("vi-VN") + "đ";

const WD = ["CN", "T2", "T3", "T4", "T5", "T6", "T7"];

/** Local-date ISO (YYYY-MM-DD), not UTC. */
export function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function parseISO(s: string): Date {
  const [y, m, d] = s.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}

export const todayISO = () => isoDate(new Date());

export function addDays(s: string, n: number): string {
  const d = parseISO(s);
  d.setDate(d.getDate() + n);
  return isoDate(d);
}

export function diffDays(a: string, b: string): number {
  return Math.round((parseISO(b).getTime() - parseISO(a).getTime()) / 86400000);
}

export const dm = (s: string) => `${s.slice(8, 10)}/${s.slice(5, 7)}`;
export const weekday = (s: string) => WD[parseISO(s).getDay()];
export const dmy = (s: string) => `${s.slice(8, 10)}/${s.slice(5, 7)}/${s.slice(0, 4)}`;
export const time = (s?: string | null) => (s ? s.slice(11, 16) : "");

export function ago(s?: string | null): string {
  if (!s) return "";
  const t = new Date(s.replace(" ", "T")).getTime();
  const m = Math.round((Date.now() - t) / 60000);
  if (m < 1) return "vừa xong";
  if (m < 60) return `${m} phút trước`;
  if (m < 1440) return `${Math.round(m / 60)} giờ trước`;
  return `${Math.round(m / 1440)} ngày trước`;
}

export function minutesLeft(s?: string | null): number | null {
  if (!s) return null;
  return Math.round((new Date(s.replace(" ", "T")).getTime() - Date.now()) / 60000);
}

export const STATUS: Record<string, { label: string; tone: string }> = {
  hold: { label: "Giữ chỗ", tone: "warn" },
  confirmed: { label: "Đã xác nhận", tone: "info" },
  checked_in: { label: "Đang ở", tone: "ok" },
  checked_out: { label: "Đã trả phòng", tone: "muted" },
  cancelled: { label: "Đã hủy", tone: "bad" },
  expired: { label: "Hết hạn", tone: "muted" },
};

export const CHANNEL: Record<string, string> = {
  "ai-chat": "AI chat",
  website: "Website",
  "walk-in": "Khách vãng lai",
  phone: "Điện thoại",
  ota: "OTA",
  zalo: "Zalo",
};
