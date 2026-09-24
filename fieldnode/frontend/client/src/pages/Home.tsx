import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  Bell,
  BatteryCharging,
  CalendarDays,
  ChartNoAxesCombined,
  Check,
  ClipboardList,
  ChevronDown,
  CircleHelp,
  Clock3,
  CloudRain,
  Cpu,
  Droplets,
  Eye,
  FlaskConical,
  Globe2,
  Languages,
  Leaf,
  LayoutDashboard,
  LoaderCircle,
  LockKeyhole,
  LogOut,
  Menu,
  MessageSquare,
  MoreHorizontal,
  Pause,
  Play,
  RefreshCcw,
  Search,
  Settings2,
  SlidersHorizontal,
  ShieldCheck,
  Sparkles,
  Sprout,
  Sun,
  ThermometerSun,
  UserRound,
  Waves,
  Wifi,
  Wind,
  X,
  Zap,
} from "lucide-react";
import { toast } from "sonner";

import AIChat from "@/components/AIChat";
import { useAuth } from "@/contexts/AuthContext";
import { useFieldData } from "@/hooks/useFieldData";
import { api, type AdvisorResult, type AnalyticsPoint, type WateringEvent } from "@/lib/api";
import { buildPath, downsample, formatClock, formatDateTime, formatRelative } from "@/lib/chart";

type ViewKey = "overview" | "zones" | "schedule" | "analytics" | "activity" | "settings";
type LanguageKey = "EN" | "HI";
type Tone = "green" | "amber" | "blue" | "red";

const navItems: { key: ViewKey; label: string; icon: typeof LayoutDashboard }[] = [
  { key: "overview", label: "Overview", icon: LayoutDashboard },
  { key: "zones", label: "Zones & crops", icon: Sprout },
  { key: "schedule", label: "Schedule", icon: CalendarDays },
  { key: "analytics", label: "Analytics", icon: ChartNoAxesCombined },
  { key: "activity", label: "Activity log", icon: ClipboardList },
  { key: "settings", label: "System settings", icon: Settings2 },
];

const labels = {
  EN: {
    overview: "Overview",
    greeting: (name: string) => `Hello, ${name.split(" ")[0]}`,
    subtitle:
      "A calm, connected view of your field — with water decisions made from live signals, not guesswork.",
    live: "Live field status",
  },
  HI: {
    overview: "अवलोकन",
    greeting: (name: string) => `नमस्ते, ${name.split(" ")[0]}`,
    subtitle: "आपके खेत की लाइव स्थिति — पानी के फैसले अनुमान से नहीं, वास्तविक संकेतों से।",
    live: "लाइव खेत स्थिति",
  },
};

const ZONE_COLORS = ["lime", "amber", "blue"] as const;

const RAIN_SENSOR_COPY: Record<string, string> = {
  dry: "Rain plate dry",
  wet: "Rain plate wet — watering held",
  suspect_stuck: "Rain plate may be stuck — veto lifted",
  offline: "Rain sensor offline",
  no_data: "No rain sensor data yet",
};

function toneForEvent(event: WateringEvent): Tone {
  if (event.trigger_type === "skipped") return "blue";
  if (event.trigger_type === "manual") return "amber";
  return "green";
}

function eventTitle(event: WateringEvent): string {
  if (event.trigger_type === "skipped") return "Cycle skipped";
  if (event.trigger_type === "manual")
    return event.amount_liters > 0 ? "Manual cycle completed" : "Manual command sent";
  return "Cycle completed";
}

function ProgressRing({ value, color = "#b7f36b", label }: { value: number; color?: string; label?: string }) {
  const radius = 40;
  const circumference = 2 * Math.PI * radius;
  const safe = Math.max(0, Math.min(100, value));
  const dash = (safe / 100) * circumference;
  return (
    <div className="progress-ring" style={{ ["--ring-color" as string]: color }}>
      <svg viewBox="0 0 100 100" aria-label={`${label ?? "Progress"}: ${Math.round(safe)}%`}>
        <circle className="ring-track" cx="50" cy="50" r={radius} />
        <circle
          className="ring-value"
          cx="50"
          cy="50"
          r={radius}
          style={{ strokeDasharray: `${dash} ${circumference - dash}` }}
        />
      </svg>
      <div className="ring-copy">
        <strong>{Math.round(safe)}%</strong>
        <span>{label}</span>
      </div>
    </div>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
  unit,
  note,
  accent,
  trend,
}: {
  icon: typeof Droplets;
  label: string;
  value: string;
  unit: string;
  note: string;
  accent: "lime" | "cyan" | "amber" | "violet";
  trend?: "up" | "down";
}) {
  return (
    <div className={`metric-card metric-${accent}`}>
      <div className="metric-top">
        <span className="metric-icon">
          <Icon size={17} />
        </span>
        <span className="metric-label">{label}</span>
        <MoreHorizontal size={17} className="muted-icon" />
      </div>
      <div className="metric-value">
        {value}
        <small>{unit}</small>
      </div>
      <div className="metric-note">
        {trend === "up" ? (
          <ArrowUpRight size={14} />
        ) : trend === "down" ? (
          <ArrowDownRight size={14} />
        ) : (
          <Activity size={14} />
        )}{" "}
        {note}
      </div>
    </div>
  );
}

function SectionTitle({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="section-title">
      <div>
        <div className="section-eyebrow">{eyebrow}</div>
        <h2>{title}</h2>
        {description && <p>{description}</p>}
      </div>
      {action}
    </div>
  );
}

export default function Home() {
  const { user, logout } = useAuth();
  const data = useFieldData(Boolean(user));
  const {
    farms,
    zones,
    crops,
    zoneId,
    setZoneId,
    overview,
    readings,
    devices,
    events,
    schedules,
    notifications,
    activity,
    loading,
    error,
    lastSync,
    live,
    refresh,
    refreshZone,
    setZones,
  } = data;

  const [view, setView] = useState<ViewKey>("overview");
  const [language, setLanguage] = useState<LanguageKey>(user?.language === "hi" ? "HI" : "EN");
  const [mobileOpen, setMobileOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [advisor, setAdvisor] = useState<AdvisorResult | null>(null);
  const [advisorBusy, setAdvisorBusy] = useState(false);
  const [pumpBusy, setPumpBusy] = useState(false);
  const [manualSeconds, setManualSeconds] = useState(60);
  const [trends, setTrends] = useState<AnalyticsPoint[]>([]);
  const [activityFilter, setActivityFilter] = useState<"all" | "auto" | "manual" | "skipped">("all");
  const [, setTick] = useState(0);

  const copy = labels[language];
  const farm = farms[0];
  const zone = zones.find((z) => z.id === zoneId) ?? null;
  const device = devices[0];

  // Re-render every 30s so relative timestamps ("3 min ago") stay honest.
  useEffect(() => {
    const timer = window.setInterval(() => setTick((t) => t + 1), 30000);
    return () => window.clearInterval(timer);
  }, []);

  // Advisor output is per zone; drop it when the zone changes.
  useEffect(() => setAdvisor(null), [zoneId]);

  // The analytics view pulls a longer window than the dashboard needs.
  useEffect(() => {
    if (view !== "analytics" || !zoneId) return;
    api
      .zoneTrends(zoneId, 30)
      .then(setTrends)
      .catch(() => setTrends([]));
  }, [view, zoneId]);

  const moistureSeries = useMemo(() => downsample(readings.map((r) => r.soil_moisture), 60), [readings]);
  const sunlightSeries = useMemo(() => downsample(readings.map((r) => r.sunlight_pct), 60), [readings]);
  const latest = readings.length ? readings[readings.length - 1] : null;

  const filteredZones = useMemo(
    () =>
      zones.filter((z) =>
        `${z.name} ${z.soil_type} ${crops.find((c) => c.id === z.crop_preset_id)?.crop_name ?? ""}`
          .toLowerCase()
          .includes(query.toLowerCase()),
      ),
    [zones, crops, query],
  );

  const unreadCount = notifications.filter((n) => !n.read).length;

  const changeView = (next: ViewKey) => {
    setView(next);
    setMobileOpen(false);
  };

  const cropNameFor = (zoneCropId: string | null) =>
    crops.find((c) => c.id === zoneCropId)?.crop_name ?? "No crop preset";

  /* ------------------------------------------------------------ actions */

  const toggleAutoMode = async () => {
    if (!zone) return;
    try {
      const updated = await api.updateZone(zone.id, { auto_mode: !zone.auto_mode });
      setZones((current) => current.map((z) => (z.id === updated.id ? updated : z)));
      toast.success(updated.auto_mode ? "Auto mode enabled" : "Manual mode enabled", {
        description: updated.auto_mode
          ? "Threshold watering and rain-aware rules are active."
          : "You control pump start and stop for this zone.",
      });
      refreshZone();
    } catch (err) {
      toast.error("Could not change the mode", {
        description: err instanceof Error ? err.message : undefined,
      });
    }
  };

  const togglePump = async () => {
    if (!zone) return;
    const running = overview?.pump_running ?? false;
    setPumpBusy(true);
    try {
      await api.controlPump(zone.id, running ? "stop" : "start", manualSeconds);
      toast.success(running ? "Stop command queued" : "Start command queued", {
        description: running
          ? "The node stops on its next poll."
          : `The node will run the pump for ${manualSeconds}s on its next poll.`,
      });
      refreshZone();
    } catch (err) {
      toast.error("Pump command rejected", {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setPumpBusy(false);
    }
  };

  const runAdvisor = async () => {
    if (!zone) return;
    setAdvisorBusy(true);
    try {
      const result = await api.advise(zone.id);
      setAdvisor(result);
      toast.success(result.should_water ? "Watering recommended" : "No watering needed", {
        description: result.reasoning.slice(0, 120),
      });
    } catch (err) {
      toast.error("Advisor unavailable", { description: err instanceof Error ? err.message : undefined });
    } finally {
      setAdvisorBusy(false);
    }
  };

  const generateSchedule = async () => {
    if (!zone) return;
    setAdvisorBusy(true);
    try {
      const created = await api.generateSchedule(zone.id);
      toast.success("Schedule generated", {
        description: created.rain_skip
          ? "Rain is expected, so this cycle is marked skipped."
          : `Next run ${formatDateTime(created.next_run_at)}.`,
      });
      await refreshZone();
      setView("schedule");
    } catch (err) {
      toast.error("Could not generate a schedule", {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setAdvisorBusy(false);
    }
  };

  const cancelSchedule = async (scheduleId: string) => {
    try {
      await api.cancelSchedule(scheduleId);
      toast.success("Schedule cancelled");
      refreshZone();
    } catch (err) {
      toast.error("Could not cancel", { description: err instanceof Error ? err.message : undefined });
    }
  };

  const applyCropPreset = async (cropId: string) => {
    if (!zone) return;
    const preset = crops.find((c) => c.id === cropId);
    try {
      const updated = await api.updateZone(zone.id, {
        crop_preset_id: cropId,
        moisture_threshold_low: preset?.moisture_min,
        moisture_threshold_high: preset?.moisture_max,
      });
      setZones((current) => current.map((z) => (z.id === updated.id ? updated : z)));
      toast.success(`${preset?.crop_name ?? "Preset"} applied`, {
        description: `Target moisture ${updated.moisture_threshold_low}–${updated.moisture_threshold_high}%.`,
      });
    } catch (err) {
      toast.error("Could not apply the preset", {
        description: err instanceof Error ? err.message : undefined,
      });
    }
  };

  const addZone = async () => {
    if (!farm) {
      toast.error("No farm yet", { description: "Create a farm before adding zones." });
      return;
    }
    const name = window.prompt("Zone name", `Zone ${zones.length + 1}`);
    if (!name) return;
    try {
      await api.createZone({ farm_id: farm.id, name, soil_type: "loam" });
      toast.success("Zone created");
      refresh();
    } catch (err) {
      toast.error("Could not create the zone", {
        description: err instanceof Error ? err.message : undefined,
      });
    }
  };

  const createFarm = async () => {
    const name = window.prompt("Farm name", "My farm");
    if (!name) return;
    try {
      await api.createFarm(name);
      toast.success("Farm created", { description: "Add a zone next." });
      refresh();
    } catch (err) {
      toast.error("Could not create the farm", {
        description: err instanceof Error ? err.message : undefined,
      });
    }
  };

  const readNotifications = async () => {
    if (unreadCount === 0) {
      toast.info("No new alerts", { description: "Your field is operating within its safety limits." });
      return;
    }
    const preview = notifications.filter((n) => !n.read).slice(0, 3);
    toast.warning(`${unreadCount} alert${unreadCount > 1 ? "s" : ""}`, {
      description: preview.map((n) => n.message).join(" · "),
    });
    try {
      await api.markAllNotificationsRead();
      refresh();
    } catch {
      /* non-critical */
    }
  };

  /* ------------------------------------------------------------- render */

  if (loading) {
    return (
      <div className="boot-screen">
        <LoaderCircle size={26} className="spin" />
        <p>Connecting to your field node…</p>
      </div>
    );
  }

  if (error && farms.length === 0 && zones.length === 0) {
    return (
      <div className="boot-screen">
        <AlertTriangle size={26} />
        <p>{error}</p>
        <div className="boot-actions">
          <button className="button secondary" onClick={refresh}>
            <RefreshCcw size={15} /> Retry
          </button>
          <button className="button primary" onClick={createFarm}>
            <Sprout size={15} /> Create a farm
          </button>
        </div>
      </div>
    );
  }

  const moisture = overview?.soil_moisture ?? null;
  const moistureDelta = overview?.soil_moisture_delta_vs_yesterday ?? null;
  const savings = overview?.water_used_delta_vs_fixed_schedule_pct ?? null;
  const rainStatus = overview?.rain_sensor_status ?? "no_data";
  const autoMode = zone?.auto_mode ?? true;
  const pumpRunning = overview?.pump_running ?? false;
  const nextPending = schedules.find((s) => s.status === "pending");

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileOpen ? "is-open" : ""}`}>
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

        <div className="farm-switcher">
          <div className="farm-avatar">{(farm?.name ?? "FN").slice(0, 2).toUpperCase()}</div>
          <div>
            <strong>{farm?.name ?? "No farm yet"}</strong>
            <span>
              {farms.length} farm{farms.length === 1 ? "" : "s"} · {zones.length} zone
              {zones.length === 1 ? "" : "s"}
            </span>
          </div>
          <ChevronDown size={15} />
        </div>

        <div className="sidebar-label">Workspace</div>
        <nav className="side-nav" aria-label="Main navigation">
          {navItems.map(({ key, label, icon: Icon }) => (
            <button key={key} className={view === key ? "active" : ""} onClick={() => changeView(key)}>
              <Icon size={18} />
              <span>{language === "HI" && key === "overview" ? "अवलोकन" : label}</span>
              {key === "activity" && activity.length > 0 && <em>{Math.min(activity.length, 99)}</em>}
            </button>
          ))}
        </nav>

        <div className="sidebar-spacer" />

        <div className="connect-card">
          <div className="connect-top">
            <span className={`status-dot ${overview?.device_online ? "" : "offline"}`} />
            <span>{overview?.device_online ? "Node connected" : "Node offline"}</span>
            <Wifi size={14} />
          </div>
          <strong>{device ? `Field Node ${device.device_code}` : "No device registered"}</strong>
          <small>
            {device
              ? `${device.gsm_fallback_ready ? "GSM fallback ready" : "WiFi only"} · ${Math.round(device.battery_health)}% health`
              : "Register a device to receive telemetry"}
          </small>
          <div className="connect-bar">
            <i style={{ width: `${device ? device.battery_health : 0}%` }} />
          </div>
        </div>

        <button
          className="help-link"
          onClick={() => toast.info("Help center", { description: "API docs: http://localhost:8000/docs" })}
        >
          <CircleHelp size={17} /> Help center
        </button>

        <div className="sidebar-profile">
          <div className="profile-avatar">{(user?.name ?? "U").slice(0, 2).toUpperCase()}</div>
          <div>
            <strong>{user?.name}</strong>
            <span>Owner account</span>
          </div>
          <button onClick={() => setProfileOpen(!profileOpen)} aria-label="Open profile menu">
            <MoreHorizontal size={17} />
          </button>
        </div>
      </aside>

      {mobileOpen && (
        <button className="mobile-scrim" aria-label="Close navigation" onClick={() => setMobileOpen(false)} />
      )}

      <main className="main-content">
        <header className="topbar">
          <button className="mobile-menu" onClick={() => setMobileOpen(true)} aria-label="Open navigation">
            <Menu size={21} />
          </button>
          <div className="breadcrumbs">
            <span>{farm?.name ?? "FieldNode"}</span>
            <span>/</span>
            <strong>{view === "overview" ? copy.overview : navItems.find((i) => i.key === view)?.label}</strong>
          </div>
          <div className="topbar-actions">
            <div className="sync-status">
              <span className={`status-dot ${live ? "" : "offline"}`} />
              <span>{live ? "Live sync" : "Polling"}</span>
              <small>· {lastSync ? formatRelative(lastSync) : "—"}</small>
            </div>
            <button className="icon-button" onClick={readNotifications} aria-label="Notifications">
              <Bell size={18} />
              {unreadCount > 0 && <i>{unreadCount}</i>}
            </button>
            <div className="language-switch">
              <Globe2 size={15} />
              <select
                value={language}
                onChange={(event) => setLanguage(event.target.value as LanguageKey)}
                aria-label="Language"
              >
                <option value="EN">EN</option>
                <option value="HI">हिं</option>
              </select>
            </div>
            <div className="top-profile" onClick={() => setProfileOpen(!profileOpen)}>
              <div className="profile-avatar">{(user?.name ?? "U").slice(0, 2).toUpperCase()}</div>
              <ChevronDown size={14} />
            </div>
            {profileOpen && (
              <div className="profile-menu">
                <strong>{user?.name}</strong>
                <span>{user?.email}</span>
                <button onClick={() => changeView("settings")}>
                  <UserRound size={15} /> Settings
                </button>
                <button onClick={logout}>
                  <LogOut size={15} /> Sign out
                </button>
              </div>
            )}
          </div>
        </header>

        <div className="page-content">
          {zones.length === 0 && (
            <section className="empty-state">
              <div className="empty-icon">
                <Sprout size={22} />
              </div>
              <h2>{farm ? "No zones yet" : "No farm yet"}</h2>
              <p>
                {farm
                  ? "Add your first growing zone to start receiving telemetry and AI advice."
                  : "Create a farm, then add a zone and register your field node against it."}
              </p>
              <button className="button primary" onClick={farm ? addZone : createFarm}>
                <Sprout size={16} /> {farm ? "Add zone" : "Create farm"}
              </button>
            </section>
          )}

          {zones.length > 0 && view === "overview" && (
            <>
              <section className="hero-intro">
                <div>
                  <div className="eyebrow-pill">
                    <span className="live-pulse" /> {copy.live} <span>·</span>{" "}
                    {new Date().toLocaleDateString([], {
                      weekday: "long",
                      day: "numeric",
                      month: "long",
                      year: "numeric",
                    })}
                  </div>
                  <h1>
                    {copy.greeting(user?.name ?? "there")}
                    <br />
                    <span>
                      your field is <i>thinking ahead.</i>
                    </span>
                  </h1>
                  <p>{copy.subtitle}</p>
                </div>
                <div className="hero-actions">
                  <select
                    className="zone-select"
                    value={zoneId ?? ""}
                    onChange={(event) => setZoneId(event.target.value)}
                    aria-label="Active zone"
                  >
                    {zones.map((z) => (
                      <option key={z.id} value={z.id}>
                        {z.name}
                      </option>
                    ))}
                  </select>
                  <button className="button secondary" onClick={refreshZone}>
                    <RefreshCcw size={16} /> Refresh data
                  </button>
                  <button className="button primary" onClick={generateSchedule} disabled={advisorBusy}>
                    {advisorBusy ? <LoaderCircle size={16} className="spin" /> : <CalendarDays size={16} />} Plan
                    watering
                  </button>
                </div>
              </section>

              {(overview?.rain_protection_on || rainStatus === "suspect_stuck") && (
                <section className={`notice-strip ${rainStatus === "suspect_stuck" ? "warn" : ""}`}>
                  <div className="notice-icon">
                    {rainStatus === "suspect_stuck" ? <AlertTriangle size={18} /> : <CloudRain size={18} />}
                  </div>
                  <div>
                    <strong>
                      {rainStatus === "suspect_stuck" ? "Rain sensor may be stuck" : "Rain-aware protection is on"}
                    </strong>
                    <span>
                      {rainStatus === "suspect_stuck"
                        ? "The plate has read wet for too long, so the rain veto was lifted to avoid blocking watering forever. Check the sensor."
                        : overview?.raining_now
                          ? "The rain plate is wet right now, so watering is held."
                          : `${Math.round(overview?.rain_probability ?? 0)}% rain probability tomorrow${
                              overview?.rain_forecast_source === "mock" ? " (simulated forecast)" : ""
                            }. The next cycle will be skipped if it holds.`}
                    </span>
                  </div>
                  <button onClick={() => changeView("schedule")}>
                    Review schedule <ArrowUpRight size={15} />
                  </button>
                </section>
              )}

              <div className="metrics-grid">
                <MetricCard
                  icon={Droplets}
                  label="Soil moisture"
                  value={moisture !== null ? moisture.toFixed(0) : "—"}
                  unit="%"
                  note={
                    moistureDelta !== null
                      ? `${moistureDelta > 0 ? "+" : ""}${moistureDelta}% vs yesterday`
                      : "No comparison yet"
                  }
                  accent="lime"
                  trend={moistureDelta !== null ? (moistureDelta >= 0 ? "up" : "down") : undefined}
                />
                <MetricCard
                  icon={Sun}
                  label="Sunlight Percentage"
                  value={overview?.sunlight_pct != null ? overview.sunlight_pct.toFixed(0) : "—"}
                  unit="%"
                  note={
                    overview?.sunlight_pct != null
                      ? overview.sunlight_pct >= 60
                        ? "Bright sunlight"
                        : overview.sunlight_pct >= 25
                          ? "Dim / cloudy"
                          : "Dark"
                      : "Not measured"
                  }
                  accent="amber"
                />
                <MetricCard
                  icon={Waves}
                  label="Water used"
                  value={(overview?.water_used_today_liters ?? 0).toFixed(1)}
                  unit="L today"
                  note={
                    savings !== null
                      ? `${savings > 0 ? "−" : "+"}${Math.abs(savings).toFixed(0)}% vs fixed schedule`
                      : "Estimated from run time"
                  }
                  accent="cyan"
                  trend={savings !== null && savings > 0 ? "down" : undefined}
                />
                <MetricCard
                  icon={ShieldCheck}
                  label="Field health"
                  value={String(overview?.field_health_score ?? "—")}
                  unit="/100"
                  note={`${Math.round(overview?.sensors_reporting_pct ?? 0)}% of sensors reporting`}
                  accent="violet"
                />
              </div>

              <div className="dashboard-grid top-grid">
                <section className="panel sensor-panel">
                  <div className="panel-heading">
                    <div>
                      <div className="section-eyebrow">Live telemetry</div>
                      <h2>Field signals</h2>
                    </div>
                    <button className="more-button" onClick={() => changeView("analytics")}>
                      <Eye size={15} /> View analytics
                    </button>
                  </div>
                  <div className="telemetry-body">
                    <div className="ring-block">
                      <ProgressRing value={moisture ?? 0} label="Moisture" />
                      <span className="ring-foot">
                        <span className="status-dot" /> Target · {zone?.moisture_threshold_low}–
                        {zone?.moisture_threshold_high}%
                      </span>
                    </div>
                    <div className="signal-list">
                      <div className="signal-row">
                        <span className="signal-bullet lime">
                          <Droplets size={15} />
                        </span>
                        <div>
                          <strong>Soil moisture</strong>
                          <small>{zone?.name}</small>
                        </div>
                        <b>
                          {moisture !== null ? moisture.toFixed(0) : "—"}
                          <small>%</small>
                        </b>
                        <span className="signal-line">
                          <i style={{ width: `${moisture ?? 0}%` }} />
                        </span>
                      </div>
                      <div className="signal-row">
                        <span className="signal-bullet amber">
                          <Sun size={15} />
                        </span>
                        <div>
                          <strong>Sunlight Percentage</strong>
                          <small>LDR reading</small>
                        </div>
                        <b>
                          {latest?.sunlight_pct != null ? latest.sunlight_pct.toFixed(0) : "—"}
                          <small>%</small>
                        </b>
                        <span className="signal-line">
                          <i style={{ width: `${latest?.sunlight_pct ?? 0}%` }} />
                        </span>
                      </div>
                      <div className="signal-row">
                        <span className="signal-bullet cyan">
                          <Wind size={15} />
                        </span>
                        <div>
                          <strong>Humidity</strong>
                          <small>Air moisture</small>
                        </div>
                        <b>
                          {latest?.humidity != null ? latest.humidity.toFixed(0) : "—"}
                          <small>%</small>
                        </b>
                        <span className="signal-line">
                          <i style={{ width: `${latest?.humidity ?? 0}%` }} />
                        </span>
                      </div>
                      <div className="signal-row">
                        <span className={`signal-bullet ${overview?.raining_now ? "blue" : "lime"}`}>
                          <CloudRain size={15} />
                        </span>
                        <div>
                          <strong>Rain sensor</strong>
                          <small>{RAIN_SENSOR_COPY[rainStatus]}</small>
                        </div>
                        <b>{overview?.raining_now ? "WET" : rainStatus === "no_data" ? "—" : "DRY"}</b>
                        <span className="signal-line">
                          <i style={{ width: `${overview?.raining_now ? 100 : 6}%` }} />
                        </span>
                      </div>
                    </div>
                  </div>
                </section>

                <section className="panel pump-panel">
                  <div className="panel-heading">
                    <div>
                      <div className="section-eyebrow">Watering control</div>
                      <h2>Pump status</h2>
                    </div>
                    <span className={`mode-badge ${autoMode ? "auto" : "manual"}`}>
                      {autoMode ? <Sparkles size={13} /> : <SlidersHorizontal size={13} />}{" "}
                      {autoMode ? "Auto mode" : "Manual mode"}
                    </span>
                  </div>
                  <div className={`pump-orb ${pumpRunning ? "running" : ""}`}>
                    <div className="orb-glow" />
                    <div className="pump-icon">
                      <Droplets size={27} />
                    </div>
                    <span>{pumpRunning ? "PUMP ON" : "PUMP READY"}</span>
                    <small>
                      {pumpRunning
                        ? `Watering ${zone?.name}`
                        : nextPending
                          ? `Next cycle · ${formatDateTime(nextPending.next_run_at)}`
                          : "No cycle scheduled"}
                    </small>
                  </div>
                  <div className="mode-toggle">
                    <button className={autoMode ? "selected" : ""} onClick={() => !autoMode && toggleAutoMode()}>
                      <Sparkles size={15} /> Auto
                    </button>
                    <button className={!autoMode ? "selected" : ""} onClick={() => autoMode && toggleAutoMode()}>
                      <SlidersHorizontal size={15} /> Manual
                    </button>
                  </div>
                  {!autoMode && !pumpRunning && (
                    <label className="duration-field">
                      <span>Run for</span>
                      <input
                        type="number"
                        min={1}
                        max={300}
                        value={manualSeconds}
                        onChange={(event) => setManualSeconds(Number(event.target.value))}
                      />
                      <small>seconds (max 300)</small>
                    </label>
                  )}
                  <button
                    className={`pump-action ${pumpRunning ? "stop" : ""}`}
                    onClick={togglePump}
                    disabled={autoMode || pumpBusy || !device}
                  >
                    {pumpBusy ? (
                      <>
                        <LoaderCircle size={16} className="spin" /> Sending…
                      </>
                    ) : pumpRunning ? (
                      <>
                        <Pause size={16} /> Stop pump
                      </>
                    ) : (
                      <>
                        <Play size={16} /> {autoMode ? "Switch to manual to start" : "Start pump manually"}
                      </>
                    )}
                  </button>
                  <div className="safety-note">
                    <ShieldCheck size={14} /> Run capped at 300 s <span>·</span>{" "}
                    {device ? `${device.pump_flow_rate_lpm} L/min calibrated` : "no device registered"}
                  </div>
                </section>
              </div>

              <div className="dashboard-grid bottom-grid">
                <section className="panel chart-panel">
                  <div className="panel-heading">
                    <div>
                      <div className="section-eyebrow">Last 24 hours</div>
                      <h2>Moisture &amp; sunlight</h2>
                    </div>
                    <div className="chart-legend">
                      <span>
                        <i className="legend-dot lime" /> Moisture %
                      </span>
                      <span>
                        <i className="legend-dot cyan" /> Sunlight %
                      </span>
                    </div>
                  </div>
                  <div className="chart-wrap">
                    <div className="y-axis">
                      <span>100%</span>
                      <span>75%</span>
                      <span>50%</span>
                      <span>25%</span>
                      <span>0%</span>
                    </div>
                    <div className="chart">
                      <div className="grid-lines">
                        <i />
                        <i />
                        <i />
                        <i />
                        <i />
                      </div>
                      {readings.length === 0 ? (
                        <div className="chart-empty">No readings in the last 24 hours.</div>
                      ) : (
                        <svg
                          viewBox="0 0 500 110"
                          preserveAspectRatio="none"
                          role="img"
                          aria-label="Moisture and sunlight over 24 hours"
                        >
                          <path className="chart-line-lime" d={buildPath(moistureSeries, 500, 110, 0, 100)} />
                          <path className="chart-line-cyan" d={buildPath(sunlightSeries, 500, 110, 0, 100)} />
                        </svg>
                      )}
                      <div className="x-axis">
                        <span>24h ago</span>
                        <span>18h</span>
                        <span>12h</span>
                        <span>6h</span>
                        <span>now</span>
                      </div>
                    </div>
                  </div>
                  <div className="chart-callout">
                    <span className="callout-dot" />
                    <strong>
                      {savings !== null
                        ? `${Math.abs(savings).toFixed(0)}% ${savings > 0 ? "less" : "more"} water`
                        : "Water tracking active"}
                    </strong>
                    <span>than a fixed daily schedule</span>
                    <ArrowDownRight size={15} />
                  </div>
                </section>

                <section className="panel activity-panel">
                  <div className="panel-heading">
                    <div>
                      <div className="section-eyebrow">What just happened</div>
                      <h2>Activity log</h2>
                    </div>
                    <button className="text-button" onClick={() => changeView("activity")}>
                      See all <ArrowUpRight size={14} />
                    </button>
                  </div>
                  <div className="activity-list">
                    {activity.length === 0 && <div className="chart-empty">No watering events yet.</div>}
                    {activity.slice(0, 4).map((item) => {
                      const tone = toneForEvent(item);
                      return (
                        <div className="activity-item" key={item.id}>
                          <div className={`activity-dot ${tone}`}>
                            {tone === "green" ? (
                              <Check size={13} />
                            ) : tone === "amber" ? (
                              <AlertTriangle size={13} />
                            ) : tone === "red" ? (
                              <X size={13} />
                            ) : (
                              <CloudRain size={13} />
                            )}
                          </div>
                          <div>
                            <strong>{eventTitle(item)}</strong>
                            <span>
                              {zones.find((z) => z.id === item.zone_id)?.name ?? "Zone"} ·{" "}
                              {item.amount_liters > 0 ? `${item.amount_liters.toFixed(2)} L · ` : ""}
                              {item.reason ?? item.trigger_type}
                            </span>
                          </div>
                          <time>{formatRelative(item.timestamp)}</time>
                        </div>
                      );
                    })}
                  </div>
                </section>
              </div>
            </>
          )}

          {zones.length > 0 && view === "zones" && (
            <section className="view-section">
              <SectionTitle
                eyebrow="OPERATIONS / ZONES"
                title="Zones & crops"
                description="Manage each growing zone with its own crop profile, soil target, and schedule."
                action={
                  <button className="button primary" onClick={addZone}>
                    <Sprout size={16} /> Add zone
                  </button>
                }
              />
              <div className="toolbar">
                <div className="search-field">
                  <Search size={16} />
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Search zones or crops"
                  />
                </div>
                <button className="filter-button" onClick={refresh}>
                  <RefreshCcw size={15} /> Refresh
                </button>
              </div>

              <div className="zone-grid">
                {filteredZones.map((z, index) => {
                  const color = ZONE_COLORS[index % ZONE_COLORS.length];
                  const isActive = z.id === zoneId;
                  return (
                    <article className={`zone-card ${isActive ? "is-active" : ""}`} key={z.id}>
                      <div className="zone-card-top">
                        <div className={`zone-illustration ${color}`}>
                          <Leaf size={24} />
                        </div>
                        <button className="more-button" onClick={() => setZoneId(z.id)}>
                          {isActive ? "Selected" : "Select"}
                        </button>
                      </div>
                      <div className="zone-title">
                        <div>
                          <h3>{z.name}</h3>
                          <p style={{ textTransform: "capitalize" }}>
                            {cropNameFor(z.crop_preset_id)} · {z.soil_type} soil
                          </p>
                        </div>
                        <span className={`zone-status ${color}`}>
                          <span /> {z.auto_mode ? "Auto" : "Manual"}
                        </span>
                      </div>
                      <div className="zone-progress">
                        <div>
                          <span>{isActive ? "Soil moisture" : "Target moisture"}</span>
                          <strong>
                            {isActive && moisture !== null
                              ? `${moisture.toFixed(0)}%`
                              : `${z.moisture_threshold_low}–${z.moisture_threshold_high}%`}
                          </strong>
                        </div>
                        <div className="progress-track">
                          <i style={{ width: `${(isActive ? moisture : null) ?? z.moisture_threshold_low}%` }} />
                        </div>
                      </div>
                      <div className="zone-footer">
                        <span>
                          <Droplets size={14} />{" "}
                          {isActive
                            ? `${(overview?.water_used_today_liters ?? 0).toFixed(1)} L today`
                            : "Select to view"}
                        </span>
                        <span>
                          <CalendarDays size={14} /> {z.auto_mode ? "Threshold-driven" : "Manual only"}
                        </span>
                      </div>
                      <button className="zone-open" onClick={() => setZoneId(z.id)}>
                        {isActive ? "Active zone" : "Open zone"} <ArrowUpRight size={15} />
                      </button>
                    </article>
                  );
                })}
              </div>

              {/* AI advisory chat, grounded in the selected zone's live sensor data */}
              <AIChat zones={zones} zoneId={zoneId} onZoneChange={setZoneId} onPumpStarted={refreshZone} />

              <div className="insight-card">
                <div className="insight-icon">
                  <Sparkles size={19} />
                </div>
                <div>
                  <span className="section-eyebrow">Field Node insight</span>
                  <h3>
                    {advisor
                      ? advisor.should_water
                        ? "Watering is recommended."
                        : "No watering needed right now."
                      : "Run the advisor for this zone."}
                  </h3>
                  <p>
                    {advisor
                      ? advisor.reasoning
                      : "The ML advisor combines soil moisture, the rain plate, the forecast and your crop target to decide whether this zone needs water."}
                  </p>
                </div>
                <button className="button secondary" onClick={runAdvisor} disabled={advisorBusy}>
                  {advisorBusy ? <LoaderCircle size={15} className="spin" /> : <Sparkles size={15} />} Run advisor
                </button>
              </div>
            </section>
          )}

          {zones.length > 0 && view === "schedule" && (
            <section className="view-section">
              <SectionTitle
                eyebrow="PLANNING / RAIN-AWARE"
                title="Watering schedule"
                description="A schedule that adapts to moisture, crop needs, and the weather — before the pump ever runs."
                action={
                  <button className="button primary" onClick={generateSchedule} disabled={advisorBusy}>
                    {advisorBusy ? <LoaderCircle size={16} className="spin" /> : <Sparkles size={16} />} Generate
                    with AI
                  </button>
                }
              />
              <div className="schedule-layout">
                <div className="panel schedule-main">
                  <div className="schedule-head">
                    <div>
                      <span className="section-eyebrow">{zone?.name}</span>
                      <h2>Recommended plan</h2>
                    </div>
                    <span className="ai-status">
                      <span className="status-dot" /> {advisor ? "AI reviewed" : "Not yet run"}
                    </span>
                  </div>

                  <div className="schedule-hero">
                    <div className="schedule-time">
                      <span>
                        {advisor ? formatClock(advisor.recommended_time) : "—:—"}
                      </span>
                      <small>
                        {advisor?.recommended_duration_seconds
                          ? `${advisor.recommended_duration_seconds}s run`
                          : "run the advisor"}
                      </small>
                    </div>
                    <div className="schedule-explain">
                      <strong>
                        {advisor ? (advisor.should_water ? "Water this zone." : "Hold watering.") : "No advice yet."}
                      </strong>
                      <p>
                        {advisor?.reasoning ??
                          "Use “Generate with AI” to produce a rain-aware plan for this zone."}
                      </p>
                      <div className="reason-chips">
                        <span>
                          <CloudRain size={13} /> {advisor ? `${Math.round(advisor.rain_probability)}% rain` : "—"}
                        </span>
                        <span>
                          <Droplets size={13} /> {advisor ? `${advisor.predicted_liters.toFixed(2)} L` : "—"}
                        </span>
                        <span>
                          <ShieldCheck size={13} /> {advisor?.raining_now ? "Plate wet" : "Plate dry"}
                        </span>
                      </div>
                      {advisor?.warnings?.length ? (
                        <div className="advisor-warnings">
                          {advisor.warnings.map((warning) => (
                            <span key={warning}>
                              <AlertTriangle size={12} /> {warning}
                            </span>
                          ))}
                        </div>
                      ) : null}
                    </div>
                    <div className="schedule-confidence">
                      <ProgressRing
                        value={advisor ? (advisor.confidence <= 1 ? advisor.confidence * 100 : advisor.confidence) : 0}
                        color="#7dd3fc"
                        label="confidence"
                      />
                    </div>
                  </div>

                  <div className="schedule-table">
                    <div className="schedule-row schedule-row-head">
                      <span>When</span>
                      <span>Source</span>
                      <span>Liters</span>
                      <span>Reason</span>
                      <span>Status</span>
                    </div>
                    {schedules.length === 0 && <div className="chart-empty">No schedules for this zone yet.</div>}
                    {schedules.slice(0, 10).map((s) => (
                      <div className="schedule-row" key={s.id}>
                        <div className="schedule-zone">
                          <span className={`mini-zone-dot ${s.rain_skip ? "blue" : "lime"}`} />
                          <strong>{formatDateTime(s.next_run_at)}</strong>
                        </div>
                        <span>{s.is_auto_generated ? "AI" : "Manual"}</span>
                        <span>{s.predicted_liters != null ? `${s.predicted_liters.toFixed(2)} L` : "—"}</span>
                        <span className="schedule-reason">{s.reasoning ?? s.recurrence}</span>
                        <span className={`table-status ${s.status === "pending" ? "queued" : "skipped"}`}>
                          {s.status}
                          {s.status === "pending" && (
                            <button className="mini-cancel" onClick={() => cancelSchedule(s.id)}>
                              cancel
                            </button>
                          )}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                <aside className="schedule-side">
                  <div className="panel preset-panel">
                    <div className="panel-heading">
                      <div>
                        <div className="section-eyebrow">Quick start</div>
                        <h2>Crop presets</h2>
                      </div>
                      <FlaskConical size={18} className="muted-icon" />
                    </div>
                    <p className="small-copy">Applying a preset sets this zone's moisture target.</p>
                    <div className="preset-list">
                      {crops.length === 0 && <div className="chart-empty">No crop presets configured.</div>}
                      {crops.map((preset) => (
                        <button
                          key={preset.id}
                          className={zone?.crop_preset_id === preset.id ? "selected" : ""}
                          onClick={() => applyCropPreset(preset.id)}
                        >
                          <span className="preset-icon">
                            <Leaf size={15} />
                          </span>
                          <span>
                            <strong style={{ textTransform: "capitalize" }}>{preset.crop_name}</strong>
                            <small>
                              {preset.moisture_min}–{preset.moisture_max}% target
                            </small>
                          </span>
                          {zone?.crop_preset_id === preset.id && <Check size={16} />}
                        </button>
                      ))}
                    </div>
                    <button className="button primary full" onClick={generateSchedule} disabled={advisorBusy}>
                      <Sparkles size={15} /> Generate plan for {zone?.name}
                    </button>
                  </div>

                  <div className="panel schedule-settings">
                    <div className="setting-line">
                      <span>
                        <Sparkles size={15} /> Auto mode
                      </span>
                      <button
                        className={`switch ${autoMode ? "on" : ""}`}
                        onClick={toggleAutoMode}
                        aria-label="Toggle auto mode"
                      >
                        <i />
                      </button>
                    </div>
                    <div className="setting-line">
                      <span>
                        <MessageSquare size={15} /> GSM fallback
                      </span>
                      <span className="locked-pill">{device?.gsm_fallback_ready ? "Ready" : "Not ready"}</span>
                    </div>
                    <small>Set on the device and reported by the field node.</small>
                  </div>
                </aside>
              </div>
            </section>
          )}

          {zones.length > 0 && view === "analytics" && (
            <section className="view-section">
              <SectionTitle
                eyebrow="INSIGHTS / PERFORMANCE"
                title="Analytics that prove the difference"
                description="Track moisture consistency, water usage, and the avoided cost of fixed-schedule irrigation."
                action={<span className="range-button big">Last 30 days · {zone?.name}</span>}
              />
              <div className="analytics-kpis">
                <div className="analytics-kpi">
                  <span className="metric-icon lime">
                    <Droplets size={17} />
                  </span>
                  <strong>{events.reduce((sum, e) => sum + e.amount_liters, 0).toFixed(1)} L</strong>
                  <small>water used (recent events)</small>
                  <em className="positive">
                    <ArrowDownRight size={13} /> {savings !== null ? `${Math.abs(savings).toFixed(0)}% saved` : "—"}
                  </em>
                </div>
                <div className="analytics-kpi">
                  <span className="metric-icon cyan">
                    <Zap size={17} />
                  </span>
                  <strong>{overview?.field_health_score ?? "—"}</strong>
                  <small>field health score</small>
                  <em className="neutral">{Math.round(overview?.sensors_reporting_pct ?? 0)}% reporting</em>
                </div>
                <div className="analytics-kpi">
                  <span className="metric-icon amber">
                    <Activity size={17} />
                  </span>
                  <strong>
                    {(() => {
                      if (!zone || readings.length === 0) return "—";
                      const inRange = readings.filter(
                        (r) =>
                          r.soil_moisture != null &&
                          r.soil_moisture >= zone.moisture_threshold_low &&
                          r.soil_moisture <= zone.moisture_threshold_high,
                      ).length;
                      return `${((inRange / readings.length) * 100).toFixed(1)}%`;
                    })()}
                  </strong>
                  <small>in target moisture (24h)</small>
                  <em className="neutral">{readings.length} samples</em>
                </div>
                <div className="analytics-kpi">
                  <span className="metric-icon violet">
                    <Clock3 size={17} />
                  </span>
                  <strong>
                    {Math.floor(events.reduce((s, e) => s + e.duration_seconds, 0) / 60)}m{" "}
                    {events.reduce((s, e) => s + e.duration_seconds, 0) % 60}s
                  </strong>
                  <small>pump run-time</small>
                  <em className="neutral">{events.length} cycles</em>
                </div>
              </div>

              <div className="panel analytics-chart">
                <div className="panel-heading">
                  <div>
                    <div className="section-eyebrow">Daily trend · 30 days</div>
                    <h2>Moisture consistency</h2>
                  </div>
                  <div className="chart-legend">
                    <span>
                      <i className="legend-dot lime" /> Moisture
                    </span>
                    <span>
                      <i className="legend-dot amber" /> Temperature
                    </span>
                  </div>
                </div>
                <div className="big-chart">
                  <div className="y-axis">
                    <span>100%</span>
                    <span>75%</span>
                    <span>50%</span>
                    <span>25%</span>
                    <span>0%</span>
                  </div>
                  <div className="chart">
                    {zone && (
                      <div
                        className="target-band"
                        style={{
                          top: `${100 - zone.moisture_threshold_high}%`,
                          height: `${zone.moisture_threshold_high - zone.moisture_threshold_low}%`,
                        }}
                      />
                    )}
                    <div className="grid-lines">
                      <i />
                      <i />
                      <i />
                      <i />
                      <i />
                    </div>
                    {trends.length === 0 ? (
                      <div className="chart-empty">No readings in this window.</div>
                    ) : (
                      <svg viewBox="0 0 800 170" preserveAspectRatio="none">
                        <path
                          className="chart-line-lime"
                          d={buildPath(downsample(trends.map((t) => t.soil_moisture), 120), 800, 170, 0, 100)}
                        />
                        <path
                          className="chart-line-amber"
                          d={buildPath(downsample(trends.map((t) => t.temperature), 120), 800, 170, 0, 50)}
                        />
                      </svg>
                    )}
                    <div className="x-axis">
                      <span>30d ago</span>
                      <span>22d</span>
                      <span>15d</span>
                      <span>7d</span>
                      <span>today</span>
                    </div>
                  </div>
                </div>
                <div className="chart-footnote">
                  <span className="status-dot" /> Shaded band is this zone's crop-safe target range <span>·</span>{" "}
                  Liters are estimated from pump run time, not a flow sensor.
                </div>
              </div>
            </section>
          )}

          {zones.length > 0 && view === "activity" && (
            <section className="view-section">
              <SectionTitle
                eyebrow="AUDIT TRAIL / EXPLAINABLE"
                title="Activity log"
                description="Every watering decision is recorded with the reason it happened, was skipped, or was blocked."
                action={
                  <button className="button secondary" onClick={refresh}>
                    <RefreshCcw size={16} /> Refresh
                  </button>
                }
              />
              <div className="panel activity-full">
                <div className="activity-toolbar">
                  <div className="activity-filters">
                    {(["all", "auto", "manual", "skipped"] as const).map((key) => (
                      <button
                        key={key}
                        className={activityFilter === key ? "active" : ""}
                        onClick={() => setActivityFilter(key)}
                      >
                        {key === "all" ? "All events" : key}
                      </button>
                    ))}
                  </div>
                  <div className="range-button">{activity.length} events</div>
                </div>
                {activity.length === 0 && <div className="chart-empty">Nothing recorded yet.</div>}
                {activity
                  .filter((item) => activityFilter === "all" || item.trigger_type === activityFilter)
                  .map((item) => {
                    const tone = toneForEvent(item);
                    return (
                      <div className="activity-row" key={item.id}>
                        <div className={`activity-dot ${tone}`}>
                          {tone === "green" ? (
                            <Check size={13} />
                          ) : tone === "amber" ? (
                            <AlertTriangle size={13} />
                          ) : (
                            <CloudRain size={13} />
                          )}
                        </div>
                        <div className="activity-row-main">
                          <strong>{eventTitle(item)}</strong>
                          <span>
                            {zones.find((z) => z.id === item.zone_id)?.name ?? "Zone"} ·{" "}
                            {item.duration_seconds > 0 ? `${item.duration_seconds}s · ` : ""}
                            {item.reason ?? "—"}
                          </span>
                        </div>
                        <span className={`activity-tag ${tone}`}>
                          {item.amount_liters > 0 ? `${item.amount_liters.toFixed(2)} L` : item.trigger_type}
                        </span>
                        <time>{formatRelative(item.timestamp)}</time>
                        <MoreHorizontal size={17} className="muted-icon" />
                      </div>
                    );
                  })}
              </div>
            </section>
          )}

          {view === "settings" && (
            <section className="view-section">
              <SectionTitle
                eyebrow="SYSTEM / PREFERENCES"
                title="System settings"
                description="Configure language, safety thresholds, and how Field Node communicates with you."
                action={
                  <button className="button primary" onClick={refresh}>
                    <RefreshCcw size={16} /> Reload
                  </button>
                }
              />
              <div className="settings-layout">
                <div className="panel settings-panel">
                  <div className="settings-section">
                    <div className="settings-section-head">
                      <div className="settings-icon">
                        <Languages size={17} />
                      </div>
                      <div>
                        <h3>Language & accessibility</h3>
                        <p>Make alerts feel native to the field.</p>
                      </div>
                    </div>
                    <div className="settings-field">
                      <label>Interface language</label>
                      <select value={language} onChange={(event) => setLanguage(event.target.value as LanguageKey)}>
                        <option value="EN">English</option>
                        <option value="HI">हिन्दी (Hindi)</option>
                      </select>
                    </div>
                  </div>

                  <div className="settings-section">
                    <div className="settings-section-head">
                      <div className="settings-icon amber-bg">
                        <ShieldCheck size={17} />
                      </div>
                      <div>
                        <h3>Safety & protection</h3>
                        <p>Rules that run before any pump cycle.</p>
                      </div>
                    </div>
                    <div className="setting-line bordered">
                      <span>
                        <span className="setting-plain-icon">
                          <ShieldCheck size={16} />
                        </span>
                        <span>
                          <strong>Run-time cap</strong>
                          <small>
                            There is no water-level sensor, so every pump run is capped server-side
                            (MAX_PUMP_RUN_SECONDS, default 300 s).
                          </small>
                        </span>
                      </span>
                      <span className="locked-pill">
                        <LockKeyhole size={12} /> Always on
                      </span>
                    </div>
                    <div className="setting-line bordered">
                      <span>
                        <CloudRain size={16} />
                        <span>
                          <strong>Rain veto</strong>
                          <small>{RAIN_SENSOR_COPY[rainStatus]}</small>
                        </span>
                      </span>
                      <span className="locked-pill">{overview?.raining_now ? "Holding" : "Clear"}</span>
                    </div>
                    <div className="setting-line bordered">
                      <span>
                        <Droplets size={16} />
                        <span>
                          <strong>Pump calibration (L/min)</strong>
                          <small>Liters are estimated as run time × this rate. Measure it with a jug.</small>
                        </span>
                      </span>
                      <input
                        className="inline-number"
                        type="number"
                        step="0.1"
                        min="0.1"
                        defaultValue={device?.pump_flow_rate_lpm ?? 1.2}
                        disabled={!device}
                        onBlur={async (event) => {
                          if (!device) return;
                          const value = Number(event.target.value);
                          if (!value || value === device.pump_flow_rate_lpm) return;
                          try {
                            await api.updateDevice(device.id, { pump_flow_rate_lpm: value });
                            toast.success("Pump calibration saved", { description: `${value} L/min` });
                            refreshZone();
                          } catch (err) {
                            toast.error("Could not save", {
                              description: err instanceof Error ? err.message : undefined,
                            });
                          }
                        }}
                      />
                    </div>
                  </div>
                </div>

                <aside className="settings-side">
                  <div className="panel device-card">
                    <div className="device-header">
                      <div className="device-chip">
                        <Cpu size={20} />
                      </div>
                      <span className={`online-pill ${overview?.device_online ? "" : "off"}`}>
                        <span className="status-dot" /> {overview?.device_online ? "Online" : "Offline"}
                      </span>
                    </div>
                    <h3>{device ? `Field Node ${device.device_code}` : "No device"}</h3>
                    <p>
                      {device
                        ? `Firmware v${device.firmware_version} · Last seen ${device.last_seen ? formatRelative(device.last_seen) : "never"}`
                        : "Register a device against this zone to receive telemetry."}
                    </p>
                    <div className="device-details">
                      <span>
                        <Wifi size={14} /> {device?.gsm_fallback_ready ? "GSM + WiFi ready" : "WiFi only"}
                      </span>
                      <span>
                        <BatteryCharging size={14} /> Battery health {Math.round(device?.battery_health ?? 0)}%
                      </span>
                      <span>
                        <ShieldCheck size={14} /> {Math.round(overview?.sensors_reporting_pct ?? 0)}% sensors
                        reporting
                      </span>
                    </div>
                    {device?.api_key && (
                      <div className="device-key">
                        <span>Device key</span>
                        <code>{device.api_key}</code>
                        <small>Send this as the X-Device-Key header from your ESP32 firmware.</small>
                      </div>
                    )}
                    <button className="button secondary full" onClick={refreshZone}>
                      <RefreshCcw size={15} /> Run device check
                    </button>
                  </div>

                  <div className="panel profile-card">
                    <div className="profile-avatar large">{(user?.name ?? "U").slice(0, 2).toUpperCase()}</div>
                    <div>
                      <h3>{user?.name}</h3>
                      <p>{user?.email}</p>
                    </div>
                    <button onClick={logout} aria-label="Sign out">
                      <LogOut size={15} />
                    </button>
                  </div>
                </aside>
              </div>
            </section>
          )}
        </div>

        <footer className="app-footer">
          <span>
            Field Node <strong>v1.0</strong> · Precision irrigation where WiFi doesn't.
          </span>
          <span>
            <span className={`status-dot ${overview?.device_online ? "" : "offline"}`} />{" "}
            {overview?.device_online ? "All systems operational" : "Waiting for the field node"}
          </span>
        </footer>
      </main>
    </div>
  );
}
