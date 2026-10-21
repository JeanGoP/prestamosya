FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/data/uploads && chown -R 10001:10001 /app
USER 10001:10001

EXPOSE 8000
CMD ["gunicorn", "--preload", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", "app:app"]
