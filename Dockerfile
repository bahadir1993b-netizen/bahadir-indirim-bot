FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY . .
RUN python build_safety_patch.py \
    && python -m py_compile publish_core.py run_telegram_realtime.py run_trusted_fast_lane.py run_web_first_deals.py run_direct_watch_v3.py run_price_analyst.py health_server.py local_store.py sitecustomize.py \
    && python selftest_publish_core.py

CMD ["python", "run_bot.py"]
