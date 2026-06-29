"""FastAPI application entry point for the NetEngine operator API."""

from fastapi import FastAPI

from netengine.api.routes import router
from logs.middleware import StructuredLoggingMiddleware

app = FastAPI(
    title="NetEngine Operator API",
    version="0.1",
    description="L1 operator surface for world management, phase orchestration, and inspection.",
)

app.add_middleware(StructuredLoggingMiddleware)
app.include_router(router)
