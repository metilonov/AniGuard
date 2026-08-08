from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# Этот импорт должен быть раньше app.api/app.bot: он подключает покупку
# Premium аккаунта к существующему обработчику Telegram Stars.
from app.premium_account import router as premium_account_router
from app.api import router as api_router
from app.store import router as store_router
from app.bot import start_polling, stop_bot
from app.naruto_game import install_naruto_game
from app.config import get_settings
from app.db import init_db
from app.monitoring import resource_monitor

# Подключаем Naruto RPG/MMO V2 до init_db/start_polling.
install_naruto_game()


settings = get_settings()
STATIC_DIR = Path(__file__).resolve().parent / "static"
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
PREMIUM_SCRIPT = '<script src="/static/premium-purchase.js?v=2402"></script>'

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO)
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    bot_task = asyncio.create_task(start_polling(), name="telegram-bot-polling")
    resource_task = await resource_monitor.start()
    app.state.bot_task = bot_task
    app.state.resource_task = resource_task
    try:
        yield
    finally:
        bot_task.cancel()
        await asyncio.gather(bot_task, return_exceptions=True)
        await resource_monitor.stop()
        try:
            await stop_bot()
        except Exception:
            pass


app = FastAPI(title="AniGuard", version="2.0.0", lifespan=lifespan)
app.include_router(premium_account_router)
app.include_router(api_router)
app.include_router(store_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/panel", status_code=307)


def _panel_html() -> str:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    if PREMIUM_SCRIPT not in html:
        html = html.replace("</body>", f"  {PREMIUM_SCRIPT}\n</body>", 1)
    return html


@app.get("/panel", include_in_schema=False)
@app.get("/panel/", include_in_schema=False)
@app.get("/shop", include_in_schema=False)
@app.get("/shop/", include_in_schema=False)
@app.get("/account", include_in_schema=False)
@app.get("/account/", include_in_schema=False)
@app.get("/group", include_in_schema=False)
@app.get("/group/", include_in_schema=False)
async def mini_app() -> HTMLResponse:
    return HTMLResponse(
        content=_panel_html(),
        headers=NO_CACHE_HEADERS,
    )


@app.get("/admin", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
async def admin_panel() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "admin.html",
        headers=NO_CACHE_HEADERS,
    )
