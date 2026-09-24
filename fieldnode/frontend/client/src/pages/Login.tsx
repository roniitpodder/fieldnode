import { useState } from "react";
import { Droplets, LoaderCircle, LockKeyhole, Mail, ShieldCheck, Sprout, UserRound, Wifi } from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "@/contexts/AuthContext";

export default function Login() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!email.trim() || !password) {
      toast.error("Email and password are required");
      return;
    }
    if (mode === "register" && !name.trim()) {
      toast.error("Please enter your name");
      return;
    }
    setBusy(true);
    try {
      if (mode === "login") {
        await login(email.trim(), password);
        toast.success("Welcome back");
      } else {
        await register(name.trim(), email.trim(), password);
        toast.success("Account created");
      }
    } catch (error) {
      toast.error(mode === "login" ? "Sign in failed" : "Sign up failed", {
        description: error instanceof Error ? error.message : "Something went wrong.",
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-shell">
      <div className="auth-aside">
        <div className="brand-lockup">
          <div className="brand-mark">
            <Sprout size={19} />
          </div>
          <div>
            <strong>
              field<span>node</span>
            </strong>
            <small>SMART IRRIGATION</small>
          </div>
        </div>
        <h1>
          Precision irrigation
          <br />
          <span>where WiFi doesn't.</span>
        </h1>
        <p>
          Live soil, rain and pump telemetry from your field node — with an AI advisor that explains every
          watering decision before the pump ever runs.
        </p>
        <ul className="auth-points">
          <li>
            <Droplets size={15} /> Rain-aware watering that skips wasted cycles
          </li>
          <li>
            <ShieldCheck size={15} /> Run-time caps and sensor fault detection
          </li>
          <li>
            <Wifi size={15} /> GSM fallback when the network drops
          </li>
        </ul>
      </div>

      <div className="auth-panel">
        <div className="auth-card">
          <div className="auth-tabs">
            <button className={mode === "login" ? "selected" : ""} onClick={() => setMode("login")}>
              Sign in
            </button>
            <button className={mode === "register" ? "selected" : ""} onClick={() => setMode("register")}>
              Create account
            </button>
          </div>

          <h2>{mode === "login" ? "Sign in to your farm" : "Set up your farm account"}</h2>
          <p className="auth-sub">
            {mode === "login"
              ? "Use the account your field node is registered to."
              : "A farm and your first zone can be added right after sign up."}
          </p>

          <div className="auth-fields">
            {mode === "register" && (
              <label className="auth-field">
                <span>Full name</span>
                <div>
                  <UserRound size={15} />
                  <input
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder="Arjun Mehta"
                    autoComplete="name"
                  />
                </div>
              </label>
            )}

            <label className="auth-field">
              <span>Email</span>
              <div>
                <Mail size={15} />
                <input
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="you@farm.example"
                  autoComplete="email"
                />
              </div>
            </label>

            <label className="auth-field">
              <span>Password</span>
              <div>
                <LockKeyhole size={15} />
                <input
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  onKeyDown={(event) => event.key === "Enter" && submit()}
                  placeholder="••••••••"
                  autoComplete={mode === "login" ? "current-password" : "new-password"}
                />
              </div>
            </label>
          </div>

          <button className="button primary full" onClick={submit} disabled={busy}>
            {busy ? <LoaderCircle size={16} className="spin" /> : <ShieldCheck size={16} />}
            {busy ? "Working…" : mode === "login" ? "Sign in" : "Create account"}
          </button>

          <div className="auth-hint">
            After running <code>python seed.py</code> the demo login is{" "}
            <strong>arjun@mehtafarm.example</strong> / <strong>password123</strong>.
          </div>
        </div>
      </div>
    </div>
  );
}
