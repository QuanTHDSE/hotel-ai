import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, setToken } from "../api";
import { ErrorText, Field, useAction } from "../ui";

export default function Login() {
  const [params] = useSearchParams();
  const [username, setUsername] = useState(params.get("u") || "");
  const [password, setPassword] = useState("");
  const act = useAction();
  const nav = useNavigate();

  const submit = () => act.run(async () => {
    const r = await api("/auth/login", { body: { username, password } });
    setToken(r.token);
    nav("/staff");
  });

  return (
    <div className="center">
      <form className="card login form" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <h1>Đăng nhập nhân viên</h1>
        <Field label="Tên đăng nhập"><input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus={!username} /></Field>
        <Field label="Mật khẩu"><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus={!!username} /></Field>
        <ErrorText error={act.error} />
        <button className="btn btn-primary" disabled={act.busy}>Đăng nhập</button>
        <div className="small muted">
          Tài khoản demo: <code>quanly.song-han</code>, <code>letan.song-han</code>, <code>quanly.doi-thong</code>, <code>letan.doi-thong</code>.
          Mật khẩu demo ghi trong README.
        </div>
        <Link className="small" to="/">← Trang chủ</Link>
      </form>
    </div>
  );
}
