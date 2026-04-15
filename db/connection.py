"""
Thread-safe PostgreSQL connection pool backed by psycopg2.

Usage — context manager (recommended):

    from db.connection import connection

    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        # conn.commit() is called automatically on clean exit
        # conn.rollback() is called on exception

Usage — manual acquire/release:

    from db.connection import get_conn, release_conn

    conn = get_conn()
    try:
        ...
    finally:
        release_conn(conn)
"""
from __future__ import annotations

import contextlib
import logging
from typing import Generator

import psycopg2
import psycopg2.extensions
import psycopg2.extras
from psycopg2 import pool as pg_pool

from config.settings import DATABASE_URL

logger = logging.getLogger(__name__)

_pool: pg_pool.ThreadedConnectionPool | None = None


def _get_pool() -> pg_pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = pg_pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=DATABASE_URL,
        )
        logger.debug("DB connection pool initialised (min=1, max=10)")
    return _pool


def get_conn() -> psycopg2.extensions.connection:
    """Acquire a connection from the pool."""
    return _get_pool().getconn()


def release_conn(conn: psycopg2.extensions.connection) -> None:
    """Return a connection to the pool."""
    _get_pool().putconn(conn)


@contextlib.contextmanager
def connection() -> Generator[psycopg2.extensions.connection, None, None]:
    """
    Context manager that acquires a pooled connection, commits on success,
    rolls back on exception, and always returns the connection to the pool.
    """
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        release_conn(conn)


def close_pool() -> None:
    """Close all connections in the pool (call at process shutdown)."""
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None
        logger.debug("DB connection pool closed")
