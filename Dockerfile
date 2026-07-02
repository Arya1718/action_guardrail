# ==========================================================
# Stage 1 - Install dependencies
# ==========================================================
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# ==========================================================
# Stage 2 - Runtime
# ==========================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copy installed Python packages
COPY --from=builder /usr/local /usr/local

# Copy application source
COPY app/ ./app/
COPY policies/ ./policies/

# (Optional) Copy any other runtime files if your app needs them
# COPY .env.example ./

# Create non-root user (required by Hugging Face Spaces)
RUN useradd -m -u 1000 appuser

# Give ownership of the application
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Verify uvicorn exists during build
RUN python -m uvicorn --version

# Hugging Face Spaces exposes port 7860
EXPOSE 7860

# Start FastAPI
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]