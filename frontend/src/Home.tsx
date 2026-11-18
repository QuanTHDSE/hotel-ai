import { Link } from "react-router-dom";
import { api } from "./api";
import { useLoad } from "./ui";

export default function Home() {
  const { data: hotels } = useLoad(() => api<any[]>("/hotels"));
  const { data: health } = useLoad(() => api<any>("/health"));
  return (
    <div className="center">
      <div className="home">
        <div>
          <h1>Hotel AI — môi trường test</h1>
          <p className="muted">
            Website cho khách (có chat AI) và trang quản lý cho nhân viên dùng chung một cơ sở dữ liệu.
            Thử: chat với AI để giữ phòng → đăng nhập nhân viên → xác nhận giữ chỗ → xem phòng đó biến mất khỏi kết quả tìm kiếm.
          </p>
          {health && (
            <p className="small">
              AI:{" "}
              {health.llm?.ok ? (
                health.llm.model_ready ? <span className="badge badge-ok">✓ {health.llm_model} sẵn sàng</span>
                  : <span className="badge badge-warn">chưa tải model {health.llm_model}</span>
              ) : <span className="badge badge-bad">Ollama chưa chạy</span>}
            </p>
          )}
        </div>
        <div className="grid grid-2">
          {hotels?.map((h) => (
            <div className="card" key={h.slug}>
              <h2>{h.name}</h2>
              <p className="muted small">{h.address}, {h.city}</p>
              <p className="small">{h.description}</p>
              <div className="row">
                <Link className="btn btn-primary" to={`/h/${h.slug}`}>Website khách + chat AI</Link>
                <Link className="btn" to={`/staff/login?u=quanly.${h.slug}`}>Trang nhân viên</Link>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
