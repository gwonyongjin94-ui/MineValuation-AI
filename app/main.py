from fastapi import FastAPI

from app.api.analysis import router as analysis_router
from app.api.health import router as health_router

app = FastAPI(title="SafetyMargin-AI")
app.include_router(health_router)
app.include_router(analysis_router)
