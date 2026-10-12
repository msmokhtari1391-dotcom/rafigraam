"""
Rafigram — بک‌اند (FastAPI)

M0: /health + اسکلت
M1: احراز هویت + چت خصوصی + WebSocket ریل‌تایم
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # رباتِ تلگرام (کدِ دعوت) درون همین پروسه، با long polling.
    # بی `TELEGRAM_BOT_TOKEN` خودش ساکت خاموش می‌ماند.
    from . import telegram_bot
    await telegram_bot.start()
    # جاروی استوری‌های منقضی (۲۴ساعته) — یک وظیفه‌ی سبکِ پس‌زمینه
    from . import stories
    await stories.start_reaper()
    try:
        yield
    finally:
        await telegram_bot.stop()
        await stories.stop_reaper()


app = FastAPI(title="Rafigram API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── روترها ──────────────────────────────────────────
from .auth import router as auth_router
from .chat import router as chat_router
from .ws import router as ws_router
from .profile import router as profile_router
from .privacy import router as privacy_router
from .gifs import router as gifs_router
from .referrals import router as referral_router
from .stories import router as stories_router
from .updates import router as updates_router
from .push import router as push_router

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(ws_router)
app.include_router(profile_router)
app.include_router(privacy_router)
app.include_router(gifs_router)
app.include_router(referral_router)
app.include_router(stories_router)
app.include_router(updates_router)
app.include_router(push_router)

# سرو فایل‌های مدیا (عکس پروفایل و ...)
app.mount("/media", StaticFiles(directory=str(settings.media_dir)), name="media")


@app.get("/")
async def root():
    return {"service": "rafigram", "docs": "/docs", "health": "/health"}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "rafigram",
        "version": app.version,
        "registration_required": bool(settings.registration_code),
        "referral_required": bool(settings.require_referral),
        "telegram_bot": bool(settings.telegram_bot_token),
    }
