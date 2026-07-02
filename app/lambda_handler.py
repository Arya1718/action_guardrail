"""
Lambda entrypoint — adapts the FastAPI app for API Gateway HTTP API (v2 payload).

Usage in SAM template:
  Handler: app.lambda_handler.handler
"""
from mangum import Mangum
from app.main import app

handler = Mangum(app, lifespan="off")
