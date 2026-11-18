"""Create a simulated hotel database: 2 fictional hotels, rooms, rates,
policies and ~90 days of random bookings relative to today.

Run:  python -m app.seed            (recreates data/hotel.db)
"""
import json
import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

from .auth import create_staff
from .config import settings
from .db import connect, init_db

HOTELS = [
    {
        "slug": "song-han",
        "name": "Sông Hàn Riverside Hotel",
        "stars": 3,
        "address": "68 Bạch Đằng, Hải Châu",
        "city": "Đà Nẵng",
        "phone": "0236 3888 168",
        "email": "booking@songhan-demo.vn",
        "checkin_time": "14:00",
        "checkout_time": "12:00",
        "description": "Khách sạn 3 sao bên sông Hàn, cách cầu Rồng 500m, cách biển Mỹ Khê 2,5km, "
                       "có hồ bơi tầng thượng và nhà hàng buffet sáng.",
        "room_types": [
            dict(code="STD", name="Standard Double", bed="1 giường đôi 1m6", max_adults=2, max_children=1,
                 size_m2=22, view="Nhìn phố", base_price=650_000, breakfast_included=1, count=10,
                 description="Phòng tiêu chuẩn ấm cúng, phù hợp cặp đôi hoặc công tác.",
                 amenities=["Wifi", "Điều hòa", "TV", "Minibar", "Phòng tắm vòi sen"]),
            dict(code="SUP", name="Superior Twin", bed="2 giường đơn 1m2", max_adults=2, max_children=1,
                 size_m2=25, view="Nhìn phố", base_price=750_000, breakfast_included=1, count=8,
                 description="Phòng 2 giường đơn, phù hợp bạn bè hoặc đồng nghiệp.",
                 amenities=["Wifi", "Điều hòa", "TV", "Minibar", "Bàn làm việc"]),
            dict(code="DLX", name="Deluxe River View", bed="1 giường King 1m8", max_adults=2, max_children=1,
                 size_m2=32, view="Nhìn sông Hàn, ban công", base_price=1_100_000, breakfast_included=1, count=8,
                 description="Phòng rộng có ban công nhìn thẳng sông Hàn, xem cầu Rồng phun lửa cuối tuần.",
                 amenities=["Wifi", "Điều hòa", "Smart TV", "Bồn tắm", "Ban công", "Máy pha cà phê"]),
            dict(code="FAM", name="Family Suite", bed="1 giường King + 2 giường đơn", max_adults=4, max_children=2,
                 size_m2=48, view="Nhìn sông, góc", base_price=1_800_000, breakfast_included=1, count=4,
                 description="Suite 2 phòng ngủ thông nhau cho gia đình, có khu vực sofa.",
                 amenities=["Wifi", "Điều hòa", "2 Smart TV", "Bồn tắm", "Sofa", "Lò vi sóng"]),
        ],
        "season_rules": [
            ("Cao điểm hè", "06-01", "08-31", 1.3, 10),
            ("Giáng sinh & Năm mới", "12-23", "01-02", 1.4, 20),
            ("Tết Nguyên Đán 2027", "2027-02-04", "2027-02-12", 1.6, 30),
            ("Lễ hội pháo hoa (DIFF)", "06-10", "06-20", 1.5, 25),
        ],
        "weekend_multiplier": 1.2,
        "policies": [
            ("checkin", "Giờ nhận/trả phòng", "Nhận phòng từ 14:00, trả phòng trước 12:00. Nhận phòng sớm (từ 10:00) "
             "hoặc trả muộn (đến 15:00) phụ thu 30% giá phòng, tùy tình trạng phòng.", "check-in check-out som muon early late gio"),
            ("cancel", "Chính sách hủy phòng", "Hủy miễn phí trước 3 ngày so với ngày nhận phòng. Hủy trong vòng 3 ngày "
             "tính phí 1 đêm đầu. Không đến (no-show) tính 100% đêm đầu. Dịp Tết và lễ không hoàn cọc.", "huy cancel refund hoan coc no-show"),
            ("deposit", "Đặt cọc & thanh toán", "Đặt cọc 30% tổng tiền để giữ phòng, qua chuyển khoản ngân hàng, MoMo hoặc VNPay. "
             "Phần còn lại thanh toán khi nhận phòng bằng tiền mặt, thẻ Visa/Master/JCB.", "coc thanh toan payment deposit chuyen khoan the visa momo"),
            ("breakfast", "Bữa sáng", "Giá phòng đã gồm buffet sáng 6:30–9:30 tại nhà hàng tầng 2. Trẻ em dưới 6 tuổi ăn miễn phí, "
             "6–11 tuổi 100.000đ/bé, khách thêm 180.000đ/người.", "an sang breakfast buffet"),
            ("children", "Trẻ em & giường phụ", "Miễn phí 1 trẻ dưới 6 tuổi ngủ chung giường bố mẹ. Giường phụ 300.000đ/đêm "
             "(gồm bữa sáng), chỉ áp dụng cho Deluxe và Family Suite.", "tre em children kid giuong phu extra bed em be"),
            ("pets", "Thú cưng", "Rất tiếc khách sạn không nhận thú cưng.", "thu cung pet cho meo cun dog cat"),
            ("smoking", "Hút thuốc", "Toàn bộ phòng không hút thuốc. Khu vực hút thuốc ở sân thượng. Phạt vệ sinh 1.000.000đ nếu hút trong phòng.",
             "hut thuoc smoking"),
            ("parking", "Gửi xe", "Gửi xe máy miễn phí. Ô tô gửi tại bãi đối diện, 50.000đ/đêm.", "gui xe parking oto xe may do xe"),
            ("transfer", "Đưa đón sân bay", "Đưa đón sân bay Đà Nẵng (cách 15 phút): xe 4 chỗ 200.000đ/chiều, xe 7 chỗ 250.000đ/chiều. "
             "Đặt trước ít nhất 6 tiếng.", "dua don san bay airport transfer taxi xe"),
            ("facility", "Tiện ích", "Hồ bơi vô cực tầng thượng (6:00–21:00), phòng gym, nhà hàng, quầy bar sân thượng, giặt ủi, "
             "cho thuê xe máy 120.000đ/ngày. Wifi miễn phí toàn khách sạn.", "ho boi pool gym wifi tien ich giat ui thue xe may bar"),
            ("location", "Vị trí & đi lại", "Cách cầu Rồng 500m (đi bộ 7 phút), chợ Hàn 800m, biển Mỹ Khê 2,5km, sân bay 4km. "
             "Cầu Rồng phun lửa 21:00 tối thứ Bảy và Chủ nhật.", "vi tri location duong di cau rong cho han bien my khe san bay"),
            ("group", "Đoàn & sự kiện", "Đoàn từ 10 phòng trở lên được báo giá riêng, vui lòng để lại thông tin để nhân viên kinh doanh liên hệ.",
             "doan group su kien event cong ty hop hoi nghi"),
        ],
    },
    {
        "slug": "doi-thong",
        "name": "Đồi Thông Homestay",
        "stars": 0,
        "address": "12 Đường Hoa Hồng, Phường 4",
        "city": "Đà Lạt",
        "phone": "0909 123 456",
        "email": "hello@doithong-demo.vn",
        "checkin_time": "13:00",
        "checkout_time": "11:00",
        "description": "Homestay gỗ giữa đồi thông, cách chợ Đà Lạt 3km, có sân vườn BBQ và lò sưởi.",
        "room_types": [
            dict(code="ATTIC", name="Phòng Đôi Gác Mái", bed="1 nệm đôi 1m6", max_adults=2, max_children=0,
                 size_m2=18, view="Cửa sổ trần nhìn trời", base_price=450_000, breakfast_included=0, count=4,
                 description="Phòng gác mái gỗ xinh xắn, cửa sổ trên trần để ngắm sao.",
                 amenities=["Wifi", "Máy sưởi", "Nước nóng", "Ấm đun nước"]),
            dict(code="BUNG", name="Bungalow Đồi Thông", bed="1 giường đôi 1m8", max_adults=2, max_children=1,
                 size_m2=28, view="Nhìn rừng thông, hiên riêng", base_price=900_000, breakfast_included=1, count=3,
                 description="Bungalow gỗ riêng biệt có hiên và lò sưởi, view rừng thông.",
                 amenities=["Wifi", "Lò sưởi", "Bồn tắm", "Hiên riêng", "Bếp nhỏ"]),
            dict(code="GROUP", name="Phòng Nhóm 6 người", bed="3 giường đôi", max_adults=6, max_children=2,
                 size_m2=40, view="Nhìn vườn", base_price=1_500_000, breakfast_included=0, count=1,
                 description="Phòng lớn cho nhóm bạn, có khu sinh hoạt chung.",
                 amenities=["Wifi", "Máy sưởi", "2 phòng tắm", "Loa bluetooth"]),
        ],
        "season_rules": [
            ("Mùa hoa dã quỳ", "11-01", "11-30", 1.15, 10),
            ("Giáng sinh & Năm mới", "12-22", "01-02", 1.5, 20),
            ("Tết Nguyên Đán 2027", "2027-02-04", "2027-02-12", 1.8, 30),
        ],
        "weekend_multiplier": 1.3,
        "policies": [
            ("checkin", "Giờ nhận/trả phòng", "Nhận phòng từ 13:00, trả phòng trước 11:00. Có thể gửi hành lý miễn phí.",
             "check-in check-out gio som muon hanh ly"),
            ("cancel", "Chính sách hủy phòng", "Hủy miễn phí trước 5 ngày. Sau đó không hoàn cọc, nhưng được dời ngày 1 lần trong 3 tháng.",
             "huy cancel refund hoan coc doi ngay"),
            ("deposit", "Đặt cọc & thanh toán", "Cọc 50% qua chuyển khoản hoặc MoMo. Thanh toán phần còn lại bằng tiền mặt/chuyển khoản khi nhận phòng.",
             "coc thanh toan payment deposit chuyen khoan momo"),
            ("breakfast", "Bữa sáng", "Bungalow đã gồm bữa sáng. Các phòng khác gọi bữa sáng 60.000đ/người (bánh mì, phở, cà phê).",
             "an sang breakfast"),
            ("pets", "Thú cưng", "Nhận chó mèo nhỏ dưới 10kg, phụ thu 100.000đ/đêm, chỉ ở Bungalow.", "thu cung pet cho meo cun"),
            ("bbq", "BBQ & lửa trại", "Sân vườn BBQ: thuê bếp 150.000đ/tối, có thể đặt set thịt nướng 250.000đ/người. Lửa trại tối thứ Bảy.",
             "bbq nuong lua trai tiec vuon"),
            ("transfer", "Đưa đón", "Đón tại bến xe liên tỉnh Đà Lạt miễn phí. Sân bay Liên Khương 300.000đ/chiều.",
             "dua don ben xe san bay lien khuong airport"),
            ("parking", "Gửi xe", "Có sân đỗ ô tô 4 chỗ miễn phí (2 chỗ), xe máy miễn phí. Cho thuê xe máy 100.000đ/ngày.",
             "gui xe parking oto xe may thue xe"),
        ],
    },
]

DEMO_PASSWORD = "demo123"  # local demo only

GUEST_NAMES = ["Nguyễn Văn An", "Trần Thị Bình", "Lê Hoàng Cường", "Phạm Thu Dung", "Hoàng Minh Đức",
               "Võ Thị Hà", "John Smith", "Kim Min-ji", "Đặng Quốc Huy", "Bùi Lan Anh", "Maria Garcia"]
CHANNELS = ["ota", "ota", "ota", "walk-in", "phone", "zalo"]


def _season_dates(start: str, end: str, today: date) -> list[tuple[str, str]]:
    """'MM-DD' ranges recur every year (cover this year and next); full dates are used as-is."""
    if len(start) == 10:
        return [(start, end)]
    out = []
    for year in (today.year - 1, today.year, today.year + 1):
        s = date(year, int(start[:2]), int(start[3:]))
        e_year = year + 1 if end < start else year  # wraps over New Year
        e = date(e_year, int(end[:2]), int(end[3:]))
        out.append((s.isoformat(), e.isoformat()))
    return out


def seed(conn: sqlite3.Connection, today: date | None = None, rng_seed: int = 42, horizon_days: int = 90) -> None:
    from .pricing import nightly_price  # local import to avoid cycles

    today = today or date.today()
    rng = random.Random(rng_seed)
    init_db(conn)
    booking_no = 1000

    for h in HOTELS:
        cur = conn.execute(
            "INSERT INTO hotels (slug,name,stars,address,city,phone,email,checkin_time,checkout_time,description) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (h["slug"], h["name"], h["stars"], h["address"], h["city"], h["phone"], h["email"],
             h["checkin_time"], h["checkout_time"], h["description"]))
        hotel_id = cur.lastrowid

        room_ids: list[tuple[int, int]] = []  # (room_id, room_type_id)
        for t_index, rt in enumerate(h["room_types"]):
            cur = conn.execute(
                "INSERT INTO room_types (hotel_id,code,name,description,bed,max_adults,max_children,size_m2,view,"
                "amenities,base_price,breakfast_included) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (hotel_id, rt["code"], rt["name"], rt["description"], rt["bed"], rt["max_adults"], rt["max_children"],
                 rt["size_m2"], rt["view"], json.dumps(rt["amenities"], ensure_ascii=False), rt["base_price"],
                 rt["breakfast_included"]))
            type_id = cur.lastrowid
            for i in range(rt["count"]):
                floor = t_index + 2
                number = f"{floor}{i + 1:02d}"
                status = "maintenance" if (h["slug"] == "song-han" and rt["code"] == "STD" and i == 0) else "active"
                cur = conn.execute("INSERT INTO rooms (hotel_id,room_type_id,number,floor,status) VALUES (?,?,?,?,?)",
                                   (hotel_id, type_id, number, floor, status))
                if status == "active":  # rooms under maintenance get no bookings
                    room_ids.append((cur.lastrowid, type_id))

        for name, start, end, mult, prio in h["season_rules"]:
            for s, e in _season_dates(start, end, today):
                conn.execute("INSERT INTO rate_rules (hotel_id,kind,name,start_date,end_date,multiplier,priority) "
                             "VALUES (?,?,?,?,?,?,?)", (hotel_id, "season", name, s, e, mult, prio))
        # Friday and Saturday nights
        conn.execute("INSERT INTO rate_rules (hotel_id,kind,name,weekdays,multiplier) VALUES (?,?,?,?,?)",
                     (hotel_id, "weekday", "Cuối tuần (tối T6, T7)", json.dumps([4, 5]), h["weekend_multiplier"]))

        for category, title, content, keywords in h["policies"]:
            conn.execute("INSERT INTO policies (hotel_id,category,title,content,keywords) VALUES (?,?,?,?,?)",
                         (hotel_id, category, title, content, keywords))

        # Demo staff accounts (credentials are documented in README.md — local testing only).
        for prefix, label, role in (("quanly", "Quản lý", "manager"), ("letan", "Lễ tân", "receptionist")):
            create_staff(conn, hotel_id, f"{prefix}.{h['slug']}", f"{label} {h['name']}", role, DEMO_PASSWORD)

        # Random existing bookings. Weekends fill up more.
        for room_id, type_id in room_ids:
            d = today - timedelta(days=rng.randint(0, 3))
            end = today + timedelta(days=horizon_days)
            while d < end:
                weekend = d.weekday() in (4, 5)
                if rng.random() < (0.55 if weekend else 0.35):
                    nights = rng.choice([1, 1, 2, 2, 3, 4])
                    ci, co = d, d + timedelta(days=nights)
                    total = sum(nightly_price(conn, hotel_id, type_id, ci + timedelta(days=n)) for n in range(nights))
                    booking_no += 1
                    if co <= today:
                        status = "checked_out"
                    elif ci < today:
                        status = "checked_in"
                    else:
                        status = "confirmed"  # includes today's arrivals, not yet checked in
                    conn.execute(
                        "INSERT INTO bookings (code,hotel_id,room_id,guest_name,phone,adults,check_in,check_out,status,"
                        "channel,total_price) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (f"BK{booking_no}", hotel_id, room_id, rng.choice(GUEST_NAMES),
                         f"09{rng.randint(10_000_000, 99_999_999)}", 2, ci.isoformat(), co.isoformat(),
                         status, rng.choice(CHANNELS), total))
                    if co == today:
                        conn.execute("UPDATE rooms SET housekeeping = 'dirty' WHERE id = ?", (room_id,))
                    d = co + timedelta(days=rng.choice([0, 0, 1, 2]))
                else:
                    d += timedelta(days=1)
    conn.commit()


def reset_database(db_path: str | None = None, today: date | None = None) -> None:
    path = db_path or settings.db_path
    if path != ":memory:":
        for suffix in ("", "-wal", "-shm"):
            p = Path(path + suffix)
            if p.exists():
                p.unlink()
    conn = connect(path)
    seed(conn, today=today)
    conn.close()


if __name__ == "__main__":
    reset_database()
    conn = connect()
    for row in conn.execute("SELECT h.name, COUNT(DISTINCT r.id) rooms, "
                            "(SELECT COUNT(*) FROM bookings b WHERE b.hotel_id = h.id) bookings "
                            "FROM hotels h JOIN rooms r ON r.hotel_id = h.id GROUP BY h.id"):
        print(f"  {row['name']}: {row['rooms']} phòng, {row['bookings']} booking giả lập")
    print(f"Đã tạo DB tại {settings.db_path}")
    sys.exit(0)
