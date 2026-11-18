"""FastAPI dependencies shared by routers."""
import sqlite3

from fastapi import Depends, Header, HTTPException

from .auth import staff_for_token
from .db import connect, get_hotel


def get_conn():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def hotel_or_404(conn: sqlite3.Connection, slug: str) -> sqlite3.Row:
    h = get_hotel(conn, slug)
    if not h:
        raise HTTPException(404, f"Không có khách sạn '{slug}'")
    return h


def _token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Chưa đăng nhập")
    return authorization.split(" ", 1)[1].strip()


def current_staff(authorization: str | None = Header(default=None), conn=Depends(get_conn)) -> dict:
    staff = staff_for_token(conn, _token(authorization))
    if not staff:
        raise HTTPException(401, "Phiên đăng nhập hết hạn")
    return dict(staff)


def manager_only(staff: dict = Depends(current_staff)) -> dict:
    if staff["role"] != "manager":
        raise HTTPException(403, "Chỉ quản lý mới được thay đổi cấu hình")
    return staff
