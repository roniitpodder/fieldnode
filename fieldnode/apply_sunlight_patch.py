"""
Adds "Sunlight Percentage" (from the LDR) to the FieldNode overview.

What it changes
  backend : new app/services/sunlight.py, plus small edits in schemas.py,
            routers/overview.py, routers/sensors.py
  frontend: lib/api.ts, hooks/useFieldData.ts, pages/Home.tsx
            (Overview "Temperature" card, the signal row and the chart become Sunlight)

How to use
  1. Put this file in the `fieldnode` folder (the one that contains `backend` and `frontend`).
  2. Stop the backend and frontend (Ctrl+C).
  3. In a terminal opened in that folder run:   python apply_sunlight_patch.py
  4. Start the backend and frontend again.

Safe to run twice (already-patched edits are skipped).
Every file it touches is first copied to  <file>.bak  - to undo, copy the .bak back over it.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if not (ROOT / "backend").is_dir() or not (ROOT / "frontend").is_dir():
    ROOT = Path.cwd()
if not (ROOT / "backend").is_dir() or not (ROOT / "frontend").is_dir():
    sys.exit("ERROR: run this from the 'fieldnode' folder (the one containing 'backend' and 'frontend').")

failed = []


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    bak = path.with_name(path.name + ".bak")
    if not bak.exists():
        bak.write_text(read(path), encoding="utf-8", newline="\n")
    path.write_text(text, encoding="utf-8", newline="\n")


def patch(rel: str, old: str, new: str, marker: str) -> None:
    """Replace `old` with `new` in file `rel`. Whitespace in `old` is matched loosely.
    `marker` is text that only exists after the edit, so re-running does nothing."""
    path = ROOT / rel
    text = read(path)
    if marker in text:
        print(f"  skip (already done): {rel}  [{marker[:40]}]")
        return
    pattern = r"\s+".join(re.escape(tok) for tok in old.split())
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        print(f"  !! FAILED in {rel}: expected 1 place to edit, found {len(matches)} for: {old[:60]!r}")
        failed.append((rel, old[:60]))
        return
    m = matches[0]
    write(path, text[: m.start()] + new + text[m.end():])
    print(f"  ok: {rel}  [{marker[:40]}]")


# ---------------------------------------------------------------- backend
print("Backend")

sun = ROOT / "backend/app/services/sunlight.py"
if sun.exists():
    print("  skip (already exists): backend/app/services/sunlight.py")
else:
    sun.write_text('''"""Converts the LDR reading into a sunlight percentage (0-100).

The firmware sends `light_level` on a 0..1200 scale (raw LDR 0..4095 scaled down),
where higher = brighter. 1200 therefore means "as bright as the LDR can read".

If full sunlight only reaches, say, 80% on your dashboard, lower LDR_FULL_SCALE
(e.g. to 960) so that full sun shows as 100%.
"""
from typing import Optional

LDR_FULL_SCALE = 1200.0


def sunlight_percent(light_level: Optional[float]) -> Optional[float]:
    if light_level is None:
        return None
    pct = light_level / LDR_FULL_SCALE * 100.0
    return round(max(0.0, min(100.0, pct)), 1)
''', encoding="utf-8", newline="\n")
    print("  ok: backend/app/services/sunlight.py (new file)")

patch("backend/app/schemas.py",
      "from pydantic import BaseModel, EmailStr, ConfigDict, Field",
      "from pydantic import BaseModel, EmailStr, ConfigDict, Field, computed_field\n\n"
      "from app.services.sunlight import sunlight_percent",
      "import sunlight_percent")

patch("backend/app/schemas.py",
      "rain_intensity: Optional[float] = None\n    sensor_fault: bool",
      "rain_intensity: Optional[float] = None\n    sensor_fault: bool\n\n"
      "    @computed_field  # sunlight % derived from the LDR (light_level)\n"
      "    @property\n"
      "    def sunlight_pct(self) -> Optional[float]:\n"
      "        return sunlight_percent(self.light_level)",
      "def sunlight_pct(self)")

patch("backend/app/schemas.py",
      "feels_like: Optional[float] = None",
      "feels_like: Optional[float] = None\n"
      "    sunlight_pct: Optional[float] = None      # from the LDR",
      "sunlight_pct: Optional[float] = None      # from the LDR")

patch("backend/app/routers/overview.py",
      "from app.services.rain_sensor import get_rain_sensor_status",
      "from app.services.rain_sensor import get_rain_sensor_status\n"
      "from app.services.sunlight import sunlight_percent",
      "import sunlight_percent")

patch("backend/app/routers/overview.py",
      "feels_like=feels_like,",
      "feels_like=feels_like,\n"
      "        sunlight_pct=sunlight_percent(latest.light_level) if latest else None,",
      "sunlight_pct=sunlight_percent")

patch("backend/app/routers/sensors.py",
      "from app.services.ws_manager import manager",
      "from app.services.ws_manager import manager\n"
      "from app.services.sunlight import sunlight_percent",
      "import sunlight_percent")

patch("backend/app/routers/sensors.py",
      '"light_level": reading.light_level,',
      '"light_level": reading.light_level,\n'
      '        "sunlight_pct": sunlight_percent(reading.light_level),',
      '"sunlight_pct": sunlight_percent')

# --------------------------------------------------------------- frontend
print("Frontend")

patch("frontend/client/src/lib/api.ts",
      "light_level: number | null;",
      "light_level: number | null;\n  sunlight_pct: number | null;",
      "sunlight_pct: number | null;\n  rain_detected")

patch("frontend/client/src/lib/api.ts",
      "feels_like: number | null;",
      "feels_like: number | null;\n  sunlight_pct: number | null;",
      "feels_like: number | null;\n  sunlight_pct")

patch("frontend/client/src/hooks/useFieldData.ts",
      "temperature: payload.temperature ?? current.temperature,",
      "temperature: payload.temperature ?? current.temperature,\n"
      "                  sunlight_pct: payload.sunlight_pct ?? current.sunlight_pct,",
      "sunlight_pct: payload.sunlight_pct ?? current")

patch("frontend/client/src/hooks/useFieldData.ts",
      "light_level: payload.light_level ?? null,",
      "light_level: payload.light_level ?? null,\n"
      "                sunlight_pct: payload.sunlight_pct ?? null,",
      "sunlight_pct: payload.sunlight_pct ?? null")

H = "frontend/client/src/pages/Home.tsx"

patch(H, "ThermometerSun,\n  UserRound,",
      "Sun,\n  ThermometerSun,\n  UserRound,",
      "Sun,\n  ThermometerSun")

patch(H,
      "const tempSeries = useMemo(() => downsample(readings.map((r) => r.temperature), 60), [readings]);",
      "const sunlightSeries = useMemo(() => downsample(readings.map((r) => r.sunlight_pct), 60), [readings]);",
      "const sunlightSeries")

# 1) the big Overview metric card
patch(H,
      """icon={ThermometerSun}
                  label="Temperature"
                  value={overview?.temperature != null ? overview.temperature.toFixed(1) : "—"}
                  unit="°C"
                  note={overview?.feels_like != null ? `Feels like ${overview.feels_like}°` : "Not measured"}""",
      """icon={Sun}
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
                  }""",
      'label="Sunlight Percentage"')

# 2) the small signal row further down
patch(H,
      """<ThermometerSun size={15} />
                        </span>
                        <div>
                          <strong>Temperature</strong>
                          <small>Ambient reading</small>
                        </div>
                        <b>
                          {latest?.temperature != null ? latest.temperature.toFixed(1) : "—"}
                          <small>°C</small>
                        </b>
                        <span className="signal-line">
                          <i style={{ width: `${Math.min(100, ((latest?.temperature ?? 0) / 50) * 100)}%` }} />""",
      """<Sun size={15} />
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
                          <i style={{ width: `${latest?.sunlight_pct ?? 0}%` }} />""",
      "<strong>Sunlight Percentage</strong>")

# 3) the 24h chart
patch(H, "<h2>Moisture &amp; temperature</h2>", "<h2>Moisture &amp; sunlight</h2>",
      "<h2>Moisture &amp; sunlight</h2>")
patch(H, '<i className="legend-dot cyan" /> Temp °C', '<i className="legend-dot cyan" /> Sunlight %',
      "Sunlight %\n")
patch(H, 'aria-label="Moisture and temperature over 24 hours"',
      'aria-label="Moisture and sunlight over 24 hours"',
      'aria-label="Moisture and sunlight over 24 hours"')
patch(H, '<path className="chart-line-cyan" d={buildPath(tempSeries, 500, 110, 0, 50)} />',
      '<path className="chart-line-cyan" d={buildPath(sunlightSeries, 500, 110, 0, 100)} />',
      "buildPath(sunlightSeries")

print()
if failed:
    print("Some edits could not be applied (your file differs from the original):")
    for rel, snippet in failed:
        print(f"  - {rel}: {snippet!r}")
    print("Nothing else is broken - send me this list and I will give you the exact manual edit.")
    sys.exit(1)
print("Done. Restart the backend and the frontend. Backups are saved as <file>.bak")
