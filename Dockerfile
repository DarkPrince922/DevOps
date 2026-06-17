FROM python:3.11-slim

WORKDIR /app
ENV HOME=/app \
    MPLCONFIGDIR=/app/.cache/matplotlib \
    PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/data /app/.cache/matplotlib
# Запускается от root: бот — админский инструмент и пишет в смонтированный
# с хоста (root) каталог /app/data, поэтому совпадение uid избавляет от
# проблем с правами на bind-mount.
CMD ["python", "bot.py"]
