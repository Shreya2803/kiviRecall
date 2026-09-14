import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from kivi.config import settings


@pytest_asyncio.fixture
async def db_session():
    """A real session bound to one connection's transaction, rolled back after
    the test — exercises real SQL (joins, generated columns, constraints)
    without leaving any data behind. Code under test must not call
    session.commit() itself (none of consolidate.py does).

    Builds its own engine with NullPool rather than importing the shared one
    from kivi.db.session: that engine is a module-level singleton created once
    at import time, and reusing its pooled connections across pytest-asyncio's
    per-test event loops caused stale reads on refresh() — a fresh, unpooled
    engine per test sidesteps the whole class of bug."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            session = AsyncSession(bind=conn, expire_on_commit=False)
            try:
                yield session
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await engine.dispose()
