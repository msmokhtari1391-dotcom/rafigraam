"""
تنظیمات سرور — همه از متغیرهای محیطی (env) خونده می‌شن تا روی Railway
بدون تغییر کد قابل پیکربندی باشه.
"""
import os
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        # پوشه‌ای که دیتابیس و فایل‌های مدیا توش ذخیره می‌شن.
        # روی Railway یه volume دائمی رو همین مسیر mount می‌کنیم (مثلاً /data).
        self.data_dir = Path(os.getenv("DATA_DIR", "./data")).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.media_dir = self.data_dir / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)

        # آدرس دیتابیس. پیش‌فرض: SQLite داخل data_dir.
        # برای ارتقا به Postgres کافیه env به شکل postgresql+asyncpg://... بدی.
        default_sqlite = f"sqlite+aiosqlite:///{(self.data_dir / 'rafigram.db').as_posix()}"
        self.database_url = os.getenv("DATABASE_URL", default_sqlite)

        # کلید امضای توکن‌ها (JWT). حتماً روی سرور یه مقدار طولانی و تصادفی بذار.
        self.secret_key = os.getenv("SECRET_KEY", "dev-insecure-change-me")

        # کد دعوت برای ثبت‌نام؛ خالی یعنی ثبت‌نام برای همه بازه.
        # روی سرور یه مقدار بذار تا فقط رفقای خودت بتونن حساب بسازن.
        self.registration_code = os.getenv("REGISTRATION_CODE", "")

        # مدت اعتبار توکن ورود (روز).
        self.access_token_ttl_days = int(os.getenv("ACCESS_TOKEN_TTL_DAYS", "30"))

        # ── دعوت‌نامه (Referral) ──────────────────────
        # توکنِ رباتِ تلگرام (از @BotFather). خالی = ربات خاموش.
        # هیچ‌وقت توکن را در کد نگذار؛ فقط همین env.
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

        # کدِ مدیر: همیشگی و بی‌نهایت‌بار مصرف (برای خودت و مهمان‌های ویژه).
        # اگر env را خالی بگذاری خودش یکی می‌سازد که تا وقتی `SECRET_KEY` عوض
        # نشود همان می‌ماند (بی این، هر ری‌استارتِ Railway کدِ مدیر را عوض
        # می‌کرد و کدِ نوشته‌شده روی کاغذ باطل می‌شد).
        self.admin_referral_code = os.getenv("ADMIN_REFERRAL_CODE", "").strip()
        if not self.admin_referral_code:
            self.admin_referral_code = self._derive_admin_code()

        # شناسه‌ی تلگرامِ صاحبِ ربات: کدهای «دریافت کد» به همین چت می‌رود.
        # خالی؟ نخستین کسی که به ربات پیام بدهد صاحب می‌شود و در
        # `DATA_DIR/bot_owner.json` ذخیره می‌شود (تا ری‌استارت‌ها یادش بماند).
        self.telegram_owner_id = os.getenv("TELEGRAM_OWNER_ID", "").strip()
        self.owner_file = self.data_dir / "bot_owner.json"

        # ثبت‌نام کدِ دعوت لازم دارد؟ اگر env را دست نزنی، خودش تصمیم می‌گیرد:
        # وقتی ربات یا کدِ مدیر تنظیم شده باشد یعنی می‌خواهی دعوتی باشد.
        _rr = os.getenv("REQUIRE_REFERRAL", "").strip().lower()
        self.require_referral = (
            _rr in ("1", "true", "yes", "on") if _rr
            else bool(self.telegram_bot_token or self.admin_referral_code))

        # هر کاربرِ تلگرام حداکثر چند کدِ استفاده‌نشده‌ی همزمان داشته باشد.
        self.referral_quota = int(os.getenv("REFERRAL_QUOTA", "5"))

        # سقفِ *مادام‌العمرِ* حساب برای هر کاربرِ عادیِ تلگرام. یعنی حتی اگر
        # کدهایش را یکی‌یکی مصرف کند، مجموعِ حساب‌هایی که با کدهای او ساخته
        # شده از این عدد بالاتر نمی‌رود. صاحبِ ربات (مدیر) مستثناست.
        # `REFERRAL_QUOTA` سقفِ *همزمان* بود، این سقفِ *همیشگی*.
        self.referral_account_cap = int(
            os.getenv("REFERRAL_ACCOUNT_CAP", "4"))

        # نامِ عمومیِ ربات برای نشان‌دادن در کلاینت (بی @).
        self.telegram_bot_username = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")

    def _derive_admin_code(self) -> str:
        """کدِ مدیرِ ثابت از `SECRET_KEY` — نه تصادفی، نه قابلِ حدس.

        HMAC می‌گیریم و به حروفِ بی‌ابهام نگاشت می‌کنیم؛ پس با یک
        `SECRET_KEY` مشخص همیشه یک کد درمی‌آید و هیچ‌کس هم از بیرون
        نمی‌تواند بسازدش. طولش ۸ است (کدِ یک‌بارمصرف ۵ است) چون این یکی
        همیشگی است و باید سخت‌تر حدس زده شود.
        """
        import hashlib
        import hmac
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        dig = hmac.new(self.secret_key.encode("utf-8"), b"rafigram-admin-code",
                       hashlib.sha256).digest()
        return "".join(alphabet[b % len(alphabet)] for b in dig[:8])


settings = Settings()
