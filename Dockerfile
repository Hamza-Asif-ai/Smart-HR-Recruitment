FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HR_DATA_ROOT=/app/data \
    PORT=10000

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --upgrade pip && pip install -r requirements.txt

COPY . .
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data/output \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 10000

CMD ["python", "app.py"]
