FROM python:3.11-slim

WORKDIR /app

# نصب وابستگی‌ها (لایه‌ی جدا تا کش بشه)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# کد اپلیکیشن
COPY app ./app

# پوشه‌ی داده؛ روی Railway یه volume دائمی همین مسیر mount می‌شه
ENV DATA_DIR=/data

# Railway مقدار $PORT رو موقع اجرا ست می‌کنه
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
