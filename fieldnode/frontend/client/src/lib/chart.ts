/** Turns a numeric series into an SVG path sized to the given viewBox.
 *  The original design used hand-drawn paths; these are generated from the
 *  readings the backend returns instead. */
export function buildPath(
  values: (number | null | undefined)[],
  width: number,
  height: number,
  min = 0,
  max = 100,
): string {
  const points = values
    .map((value, index) => ({ value, index }))
    .filter((point): point is { value: number; index: number } => typeof point.value === "number");

  if (points.length === 0) return "";
  if (points.length === 1) {
    const y = project(points[0].value, min, max, height);
    return `M0 ${y} L${width} ${y}`;
  }

  const span = Math.max(1, values.length - 1);
  return points
    .map((point, i) => {
      const x = (point.index / span) * width;
      const y = project(point.value, min, max, height);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
}

function project(value: number, min: number, max: number, height: number): number {
  const range = max - min || 1;
  const clamped = Math.min(max, Math.max(min, value));
  return height - ((clamped - min) / range) * height;
}

/** Averages a series into `buckets` slots so a week of 30-minute samples
 *  renders as a readable line instead of noise. */
export function downsample(values: (number | null | undefined)[], buckets: number): (number | null)[] {
  if (values.length === 0) return [];
  if (values.length <= buckets) return values.map((v) => (typeof v === "number" ? v : null));

  const size = values.length / buckets;
  const out: (number | null)[] = [];
  for (let i = 0; i < buckets; i++) {
    const slice = values.slice(Math.floor(i * size), Math.floor((i + 1) * size));
    const nums = slice.filter((v): v is number => typeof v === "number");
    out.push(nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : null);
  }
  return out;
}

export function formatTime(value: string | Date): string {
  const date = typeof value === "string" ? new Date(ensureUtc(value)) : value;
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Alias kept separate so call sites read clearly where only a clock is shown. */
export const formatClock = formatTime;

export function formatRelative(value: string | Date): string {
  const date = typeof value === "string" ? new Date(ensureUtc(value)) : value;
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  if (seconds < 172800) return "yesterday";
  return date.toLocaleDateString([], { day: "2-digit", month: "short" });
}

export function formatDateTime(value: string | Date): string {
  const date = typeof value === "string" ? new Date(ensureUtc(value)) : value;
  return date.toLocaleString([], {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** The backend stores naive UTC timestamps, which JS would otherwise read as
 *  local time and show the wrong "x hours ago". */
function ensureUtc(value: string): string {
  return /[zZ]|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`;
}
