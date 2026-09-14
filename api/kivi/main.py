from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from kivi.db.session import engine

app = FastAPI(title="Kivi Semantic Memory API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

REQUIRED_EXTENSIONS = ("vector", "pg_trgm", "unaccent")


@app.get("/health")
async def health() -> dict:
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname = ANY(:names)"),
                {"names": list(REQUIRED_EXTENSIONS)},
            )
            installed = {row[0] for row in result.fetchall()}
        db_ok = True
    except Exception:
        installed = set()
        db_ok = False

    extensions = {name: name in installed for name in REQUIRED_EXTENSIONS}

    return {
        "status": "ok" if db_ok and all(extensions.values()) else "degraded",
        "db": db_ok,
        "pgvector": extensions["vector"],
        "extensions": extensions,
    }
