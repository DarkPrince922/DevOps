FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd -r app && useradd -r -g app -d /app app
COPY . .
RUN mkdir -p /app/data && chown -R app:app /app && chmod 600 /app/.env 2>/dev/null || true
USER app
CMD ["python", "bot.py"]
