# Field Node — Smart Irrigation

A professional software-first smart irrigation dashboard based on the supplied Smart Irrigation System project report. The project is a static React front end designed to be opened locally in VS Code.

## Run locally

```bash
pnpm install
pnpm dev
```

Then open the local URL printed by Vite. The project uses React, TypeScript, Vite, Tailwind, Lucide icons, and the existing shadcn/ui setup.

## What is implemented

The dashboard includes live-style sensor telemetry for soil moisture, temperature, humidity, and tank level; auto/manual pump controls; dry-run protection; rain-aware schedule reasoning; activity logging; crop presets; multi-zone management; historical analytics; water-usage and estimated savings tracking; device health; GSM/SMS fallback presentation; voice alert toggles; English/Hindi language switching; profile/settings foundations; and responsive desktop/mobile layouts.

All interactions in this version are intentionally front-end demo interactions. They update local React state and show confirmation toasts so the software flows can be reviewed without connected hardware. Real ESP32/GSM sensor ingestion, authentication persistence, push/SMS delivery, and weather/AI APIs should be wired in the next backend integration phase.

## Visual direction

The UI uses a dark field-operations palette with deep soil-green surfaces, electric lime status accents, cyan water signals, warm amber warnings, compact mono labels, and Space Grotesk display typography. The layout takes inspiration from modern agriculture SaaS dashboards and water-management interfaces: persistent navigation, data-dense cards, soft depth, clear status hierarchy, and explainable schedule decisions.

## Project structure

- `client/src/pages/Home.tsx` — interactive dashboard, zones, schedules, analytics, activity, and settings views.
- `client/src/index.css` — design system, responsive layout, charts, motion, and component styling.
- `client/src/App.tsx` — app shell and routing.
- `client/index.html` — document metadata and title.

## Integration notes

The UI labels the backend-dependent areas clearly through demo behavior rather than inventing live connectivity. For production, connect telemetry to the ESP32/GSM gateway, replace demo state with API queries/mutations, persist user/farm/zone data, and connect weather, SMS, authentication, and scheduling services.
