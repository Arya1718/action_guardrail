# ── Stage 1: install production dependencies ─────────────────────────────
FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ── Stage 2: runtime image ───────────────────────────────────────────────
FROM python:3.12-slim

ENV PATH=/root/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local

# Copy application code (only what's needed at runtime)
COPY app/ ./app/
COPY policies/ ./policies/

# Hugging Face Spaces requires a non-root user with UID 1000
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app

USER appuser

# HF Spaces expects the app on port 7860
EXPOSE 7860

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
