FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PROOF_STORAGE_ROOT=/app/storage \
    PROOF_OUTBOX_DIR=/app/storage/outbox

WORKDIR /app

COPY requirements.txt .
# httpx and pdfplumber are only for tests: they are not installed in the image
RUN grep -vE '^(httpx|pdfplumber)' requirements.txt > requirements.prod.txt \
    && pip install --no-cache-dir -r requirements.prod.txt \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY src ./src
COPY migrations ./migrations

RUN useradd --create-home --uid 10001 prima \
    && mkdir -p /app/storage \
    && chown -R prima:prima /app/storage
USER prima

EXPOSE 8050
CMD ["python", "-m", "src.api"]
