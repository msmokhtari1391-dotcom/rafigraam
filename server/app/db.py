"""
اتصال به دیتابیس با SQLModel روی موتور async (aiosqlite برای SQLite).
"""
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from .config import settings

# `pool_pre_ping` بی‌خود نیست: اگر کلاینتِ موبایل وسطِ یک درخواست قطع شود،
# تسک کنسل می‌شود و اتصالِ aiosqlite همان‌جا terminate می‌شود — ولی همان
# اتصالِ مرده در استخر می‌ماند و درخواستِ بعدی با «no active connection»
# می‌شکند. با pre_ping، اتصال در لحظه‌ی برداشتن سنجیده و در صورتِ مرگ
# بی‌صدا با یک اتصالِ تازه عوض می‌شود.
engine = create_async_engine(settings.database_url, echo=False, future=True,
                             pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def init_db() -> None:
    """ساخت جدول‌ها موقع بالا اومدن سرور."""
    # مدل‌ها باید قبل از create_all ایمپورت شن تا در metadata ثبت بشن.
    # (از M1 به بعد فایل models.py اضافه می‌شه.)
    try:
        from . import models  # noqa: F401
    except ImportError:
        pass

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


# ستون‌هایی که بعد از ساختِ اولیه‌ی دیتابیس اضافه شدن.
# create_all جدولِ موجود رو تغییر نمی‌ده، پس دستی ALTER می‌کنیم.
_NEW_COLUMNS: dict[str, dict[str, str]] = {
    "users": {
        "last_seen_at": "DATETIME",
        "show_last_seen": "BOOLEAN NOT NULL DEFAULT 1",
        "birthday": "VARCHAR",
    },
    "chat_members": {
        "last_read_at": "DATETIME",
        "role": "VARCHAR NOT NULL DEFAULT 'member'",
        "archived": "BOOLEAN NOT NULL DEFAULT 0",
        "banned": "BOOLEAN NOT NULL DEFAULT 0",
    },
    "chats": {
        "kind": "VARCHAR NOT NULL DEFAULT 'private'",
        "is_public": "BOOLEAN NOT NULL DEFAULT 0",
        "handle": "VARCHAR",
    },
    "messages": {
        "edited_at": "DATETIME",
        "deleted": "BOOLEAN NOT NULL DEFAULT 0",
        "pinned": "BOOLEAN NOT NULL DEFAULT 0",
        "forward_from": "VARCHAR",
        "enc": "BOOLEAN NOT NULL DEFAULT 0",
        "caption": "VARCHAR",
        # نامِ اصلیِ فایلِ پیوست‌شده — سرور آپلود را با uuid ذخیره می‌کند و
        # بی این ستون کاربر فقط «فایل ZIP» می‌دید نه اسمِ واقعی فایل.
        "file_name": "VARCHAR",
    },
}

# پس از افزودنِ ستون‌ها، داده‌های قدیمی باید معنی‌دار بشن.
# هر جمله فقط وقتی اجرا می‌شه که ستونِ کلیدی‌اش تازه ساخته شده باشه.
_BACKFILL: list[tuple[str, str, str]] = [
    # (جدول، ستونِ تازه، SQL)
    ("chats", "kind",
     "UPDATE chats SET kind = CASE WHEN is_group THEN 'group' ELSE 'private' END"),
    ("chat_members", "role",
     "UPDATE chat_members SET role = 'owner' WHERE chat_id IN "
     "(SELECT id FROM chats WHERE creator_id = chat_members.user_id)"),
]


def _add_missing_columns(conn) -> None:
    """مهاجرتِ سبک: ستون‌های تازه رو به جدول‌های قدیمی اضافه می‌کنه."""
    from sqlalchemy import inspect as sa_inspect, text

    insp = sa_inspect(conn)
    tables = set(insp.get_table_names())
    added: set[tuple[str, str]] = set()
    for table, columns in _NEW_COLUMNS.items():
        if table not in tables:
            continue
        have = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in columns.items():
            if name not in have:
                conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {ddl}'))
                added.add((table, name))
    for table, column, sql in _BACKFILL:
        if (table, column) in added:
            try:
                conn.execute(text(sql))
            except Exception:
                pass  # نبودِ داده‌ی قدیمی نباید بالا اومدنِ سرور رو بشکنه
    # ایندکسِ یکتا برای @handle (سطحِ دیتابیس؛ در کد هم چک می‌شه)
    if "chats" in tables:
        try:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_chats_handle_uniq "
                "ON chats (handle) WHERE handle IS NOT NULL"))
        except Exception:
            pass


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependency فست‌ای‌پی‌آی برای گرفتن یه session دیتابیس."""
    async with AsyncSessionLocal() as session:
        yield session
