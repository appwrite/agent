import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.build_info import BUILD_ID, BUILD_TIME, as_dict
from app.config import get_settings

logger = logging.getLogger("uvicorn.error")
settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info(
        "appwrite-assistant starting build_id=%s build_time=%s",
        BUILD_ID,
        BUILD_TIME,
    )
    yield


app = FastAPI(
    title="Appwrite Assistant (LangGraph)",
    description="Cluster-internal coding/assistant engine for Appwrite Cloud /v1/assistant.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
async def root():
    return {
        "service": "appwrite-assistant",
        "engine": "langgraph",
        "docs": "/docs",
        **as_dict(),
    }
