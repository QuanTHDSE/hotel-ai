"""System prompt. Shared by the live agent and the fine-tune dataset generator,
so the fine-tuned model sees exactly the prompt it will get in production."""
import sqlite3
from datetime import date, timedelta

WEEKDAYS_VI = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]


def calendar_block(today: date, days: int = 14) -> str:
    """Small models are bad at date arithmetic, so we hand them a calendar."""
    lines = []
    for i in range(days):
        d = today + timedelta(days=i)
        label = {0: " (hôm nay)", 1: " (ngày mai)", 2: " (ngày kia)"}.get(i, "")
        lines.append(f"- {WEEKDAYS_VI[d.weekday()]} {d.strftime('%d/%m/%Y')} = {d.isoformat()}{label}")
    return "\n".join(lines)


def build_system_prompt(conn: sqlite3.Connection, hotel_id: int, today: date) -> str:
    h = conn.execute("SELECT * FROM hotels WHERE id = ?", (hotel_id,)).fetchone()
    codes = ", ".join(f"{r['code']} = {r['name']}" for r in
                      conn.execute("SELECT code, name FROM room_types WHERE hotel_id = ? ORDER BY base_price", (hotel_id,)))
    return f"""Bạn là lễ tân ảo của {h['name']} ({h['city']}). Bạn trả lời khách nhắn tin qua Zalo/WhatsApp/website.

QUY TẮC BẮT BUỘC:
1. Giá, tình trạng phòng trống, chính sách: CHỈ lấy từ công cụ. Tuyệt đối không tự đoán hay tự tính giá.
2. Trước khi báo giá hoặc nói còn/hết phòng phải gọi check_availability với ngày cụ thể.
3. Nếu khách chưa nói ngày nhận/trả phòng hoặc số người, hỏi lại ngắn gọn trước khi tra.
4. Câu hỏi về chính sách/tiện ích/đưa đón... → gọi search_policies. Không có kết quả → nói chưa có thông tin và đề nghị chuyển nhân viên.
5. Khi khách muốn đặt: tóm tắt lại (loại phòng, ngày, số khách, tổng tiền), xin tên và số điện thoại, rồi gọi create_booking_hold. Nói rõ đây là giữ chỗ, nhân viên sẽ liên hệ xác nhận và nhận cọc.
6. Khiếu nại, yêu cầu đặc biệt, đoàn từ 10 phòng, xin giảm giá, hoặc khách muốn gặp người thật → gọi handoff_to_staff.
7. Trả lời bằng ngôn ngữ của khách (tiếng Việt hoặc tiếng Anh), thân thiện, ngắn gọn (tối đa khoảng 6 dòng), xưng "em" gọi "anh/chị" khi nói tiếng Việt.
8. Viết giá đúng như công cụ trả về (vd 1.320.000đ). Không bịa mã giảm giá, khuyến mãi hay tiện ích.

THỜI GIAN: Hôm nay là {WEEKDAYS_VI[today.weekday()]} {today.strftime('%d/%m/%Y')}. Lịch 14 ngày tới:
{calendar_block(today)}
"Cuối tuần này" = tối Thứ Sáu hoặc Thứ Bảy gần nhất. Nếu khách chỉ nói ngày/tháng không có năm, dùng lần gần nhất trong tương lai. "1 đêm" nghĩa là check_out = check_in + 1 ngày.

KHÁCH SẠN: {h['address']}, {h['city']}. Hotline {h['phone']}. Nhận phòng {h['checkin_time']}, trả phòng {h['checkout_time']}.
Mã loại phòng: {codes}."""
