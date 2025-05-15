"""Database module for managing user search history in a Telegram bot."""

from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    select,
    func,
    desc,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import relationship
from sqlalchemy.ext.asyncio import async_sessionmaker

Base = declarative_base()


class User(Base):  # type: ignore
    """
    User model representing a Telegram user.

    Attributes:
        id: Primary key of the user
        telegram_id: Telegram user ID
        searches: Relationship to user's search history
    """

    # pylint: disable=too-few-public-methods
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    searches = relationship("SearchHistory", back_populates="user")


class SearchHistory(Base):  # type: ignore
    """
    Model for storing user search history.

    Attributes:
        id: Primary key of the search entry
        user_id: Foreign key referencing User.telegram_id
        query: The search query input by the user
        film_name: Name of the found film (if any)
        film_year: Year of the found film (if any)
        timestamp: When the search was performed
        user: Relationship to the user who performed the search
    """

    # pylint: disable=too-few-public-methods
    __tablename__ = "search_history"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.telegram_id"), nullable=False)
    query = Column(String, nullable=False)
    film_name = Column(String)
    film_year = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)
    user = relationship("User", back_populates="searches")


def init_db(database_url="sqlite+aiosqlite:///bot_history.db"):
    """Initialize the database and return an async session factory."""
    engine = create_async_engine(database_url, echo=False)
    async_session = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )

    async def init_tables():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    return async_session, init_tables


async def add_search(session, telegram_id, query, film_name=None, film_year=None):
    """Add a search query to the user's history."""
    # Check if user exists, create if not
    user_stmt = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(user_stmt)
    user = result.scalars().first()

    if not user:
        user = User(telegram_id=telegram_id)
        session.add(user)
        await session.commit()

    search = SearchHistory(
        user_id=telegram_id, query=query, film_name=film_name, film_year=film_year
    )
    session.add(search)

    searches_stmt = (
        select(SearchHistory)
        .where(SearchHistory.user_id == telegram_id)
        .order_by(SearchHistory.timestamp.desc())
    )

    result = await session.execute(searches_stmt)

    await session.commit()


async def get_user_history(session, telegram_id, page_size=20):
    """Retrieve a user's search history with pagination.
    If page_size is None, returns all history items.
    """
    stmt = (
        select(SearchHistory)
        .where(SearchHistory.user_id == telegram_id)
        .order_by(SearchHistory.timestamp.desc())
    )

    if page_size is not None:
        stmt = stmt.limit(page_size)

    result = await session.execute(stmt)
    return result.scalars().all()


async def get_user_stats(session, telegram_id):
    """Get statistics on how many times each film has been suggested to the user."""

    stmt = (
        select(
            SearchHistory.film_name,
            SearchHistory.film_year,
            func.count(1).label("count"),
        )
        .where(
            SearchHistory.user_id == telegram_id, SearchHistory.film_name.is_not(None)
        )
        .group_by(SearchHistory.film_name, SearchHistory.film_year)
        .order_by(desc("count"))
    )

    result = await session.execute(stmt)
    return result.all()
