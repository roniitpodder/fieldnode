import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import Base, engine
from app.migrate import run_light_migrations
from app import models  # noqa: F401
from app.services.auto_watering import auto_watering_loop

from app.routers import (
    auth,
    farms,
    zones,
    devices,
    sensors,
    pump,
    schedule,
    analytics,
    activity,
    notifications,
    crops,
    ai_advisor,
    ai_chat,
    overview,
    ws,
)

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    Base.metadata.create_all(bind=engine)
    run_light_migrations(engine)

    # Reconcile any dirty unconfirmed pump_running states
    # from previous unexpected shutdown.
    from app.database import SessionLocal

    with SessionLocal() as db:
        stale_pumps = (
            db.query(models.Device)
            .filter(
                models.Device.pump_running.is_(True)
            )
            .all()
        )

        for d in stale_pumps:
            d.pump_running = False

        db.commit()

    print("[STARTUP] Database ready")
    print(
        f"[STARTUP] AUTO_WATER_ENABLED = "
        f"{settings.AUTO_WATER_ENABLED}"
    )

    if settings.AUTO_WATER_ENABLED:
        asyncio.create_task(
            auto_watering_loop()
        )
        print(
            "[AUTO-WATERING] Background loop STARTED"
        )
    else:
        print(
            "[AUTO-WATERING] Disabled by configuration"
        )


app.include_router(auth.router)
app.include_router(farms.router)
app.include_router(zones.router)
app.include_router(devices.router)
app.include_router(sensors.router)
app.include_router(pump.router)
app.include_router(schedule.router)
app.include_router(analytics.router)
app.include_router(activity.router)
app.include_router(notifications.router)
app.include_router(crops.router)
app.include_router(ai_advisor.router)
app.include_router(ai_chat.router)
app.include_router(overview.router)
app.include_router(ws.router)


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "auto_watering": settings.AUTO_WATER_ENABLED,
    }


FRONTEND_DIST = (
    Path(__file__).resolve().parent.parent.parent
    / "frontend"
    / "dist"
    / "public"
).resolve()


if FRONTEND_DIST.is_dir():

    if (FRONTEND_DIST / "assets").is_dir():
        app.mount(
            "/assets",
            StaticFiles(
                directory=FRONTEND_DIST / "assets"
            ),
            name="assets",
        )

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):

        if full_path.startswith(("api/", "ws/")):
            raise HTTPException(
                status_code=404,
                detail="Not found",
            )

        candidate = (
            FRONTEND_DIST / full_path
        ).resolve()

        if (
            full_path
            and candidate.is_file()
            and FRONTEND_DIST in candidate.parents
        ):
            return FileResponse(candidate)

        return FileResponse(
            FRONTEND_DIST / "index.html"
        )

else:

    @app.get("/")
    def root():
        return {
            "status": "ok",
            "service": settings.APP_NAME,
            "hint": (
                "Frontend not built. "
                "Run `pnpm build` in ../frontend, "
                "or use the Vite dev server."
            ),
        }