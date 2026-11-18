# Hotel AI: Lễ tân AI + PMS mini để test

Hệ thống gồm 2 phần dùng chung một DB:

- **Website khách** (`/h/<khách sạn>`): xem hạng phòng, tìm phòng trống, đặt phòng, **chat với lễ tân AI**.
- **Trang nhân viên** (`/staff`): tổng quan, sơ đồ phòng, quản lý booking (xác nhận / check-in / check-out / chuyển phòng),
  **hộp thư hội thoại AI** (xem AI đã tra gì, tiếp quản, trả lời khách), cấu hình phòng, giá, kiến thức AI.

AI chạy bằng model mở tự host (Ollama, `qwen2.5:3b`). Mọi giá và phòng trống đều tra từ DB.

## Chạy

**Yêu cầu:** Python 3.11+, Node 20+, Ollama đã chạy và đã `ollama pull qwen2.5:3b`.

```bash
cd D:\MyProject\hotel-ai
.venv\Scripts\activate
python -m uvicorn app.main:app --port 8000
```

Mở http://localhost:8000. Server dùng sẵn bản build của frontend trong `frontend/dist`.

**Khi sửa frontend** (tự reload khi lưu file):

```bash
cd frontend
npm install
npm run dev
```

Lệnh này chạy song song với uvicorn ở trên, mở http://localhost:5173. Sửa xong chạy `npm run build` để cập nhật bản cho cổng 8000.

## Tài khoản demo (chỉ dùng local)

| Tài khoản | Vai trò |
|---|---|
| `quanly.song-han` | Quản lý (sửa được cấu hình, giá, kiến thức AI) |
| `letan.song-han` | Lễ tân (vận hành booking, trả lời chat) |
| `quanly.doi-thong`, `letan.doi-thong` | Tương tự cho homestay |

Mật khẩu chung: `demo123` (khai báo trong `app/seed.py`).

## Kịch bản test quy trình AI

1. Mở website khách → bấm **Chat với lễ tân** → hỏi "Tối thứ 6 này còn phòng Deluxe cho 2 người không?".
2. Nhắn "Ok đặt giúp mình phòng Deluxe, tên Nguyễn Văn A, sđt 0905123456". AI giữ chỗ và trả mã `HD...`.
3. Đăng nhập nhân viên ở tab khác → **Tổng quan** thấy giữ chỗ chờ xác nhận → **Xác nhận**.
4. **Sơ đồ phòng**: booking có dấu ✦ (do AI tạo). Hỏi lại AI cùng ngày: số phòng trống đã giảm.
5. Chat "Cho mình gặp nhân viên" → **Hội thoại AI** hiện "Chờ nhân viên" → trả lời. Khách thấy tin ngay trong khung chat.
   Bấm **Trả lại cho AI** để AI tiếp tục.
6. **Kiến thức AI**: thêm mục "Sân tennis" → hỏi AI "Có sân tennis không?" → AI trả lời theo mục mới, không cần train lại.
7. **Giá phòng**: thêm quy tắc giá → bảng giá và câu trả lời của AI đổi theo.
8. **Phòng**: đặt một phòng sang "Bảo trì" → AI không bán phòng đó nữa.

## AI được kiểm soát thế nào

Model 3B nhỏ nên hay sai. Hệ thống không tin hoàn toàn vào model:

| Rủi ro | Cách chặn (code, không phụ thuộc model) |
|---|---|
| Tính sai ngày ("thứ 6 này") | `app/hints.py` tự đọc ngày trong tin nhắn và đưa sẵn cho AI |
| Tự chọn ngày khi khách chưa nói | Tool tra phòng từ chối, bắt AI hỏi lại |
| Bịa giá | `app/grounding.py` đối chiếu mọi số tiền với dữ liệu tool. Sai thì AI làm lại, vẫn sai thì thay bằng câu trả lời mẫu từ DB |
| Nói "đã giữ phòng" mà không giữ | Phát hiện câu khẳng định. Nếu khách đã đủ thông tin, code tự giữ phòng (`app/booking_flow.py`), thiếu thì hỏi đúng phần thiếu |
| Khách muốn gặp người thật | Code chuyển nhân viên ngay, AI tạm dừng ở cuộc trò chuyện đó |
| Đặt trùng phòng | Khóa ghi `BEGIN IMMEDIATE` khi tạo booking. Giữ chỗ tự hết hạn sau 30 phút |
| Lộ dữ liệu khách sạn khác | `hotel_id` lấy từ server hoặc phiên đăng nhập, không bao giờ do AI cung cấp |

## Kiểm thử

```bash
python -m pytest            # 37 test: giá, phòng trống, API, chuyển nhân viên, các lớp chống bịa
python -m eval.run_eval -v  # 18 tình huống với model thật
```

## Cấu trúc

| Thư mục / file | Vai trò |
|---|---|
| `app/api_public.py` | API cho website khách + chat |
| `app/api_staff.py` | API PMS cho nhân viên (đăng nhập, booking, phòng, giá, kiến thức, hộp thư) |
| `app/agent.py` | Vòng lặp AI + các lớp kiểm tra |
| `app/tools.py` | Các tool AI được gọi |
| `app/bookings.py` | Logic booking dùng chung cho AI, website và nhân viên |
| `app/seed.py` | Dữ liệu giả lập |
| `frontend/src/guest` | Website khách + khung chat |
| `frontend/src/staff` | Trang nhân viên |
| `eval/`, `finetune/` | Chấm điểm AI và fine-tune model riêng (xem `finetune/README.md`) |

## Bước tiếp theo

- Webhook **Zalo OA / WhatsApp**: gọi `ReceptionistAgent.run(..., channel="zalo")` giống `/api/chat`. Phần còn lại giữ nguyên.
- Đổi mật khẩu demo và dùng PostgreSQL trước khi cho người ngoài dùng.
