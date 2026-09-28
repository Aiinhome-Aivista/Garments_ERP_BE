"""Thin SQLAlchemy Core helpers: pooled engine, dict rows, tiny query API."""
from contextlib import contextmanager
from sqlalchemy import create_engine, text

_engine = None


def init_engine(url):
    global _engine
    _engine = create_engine(url, pool_size=8, max_overflow=4, pool_recycle=1800, pool_pre_ping=True, future=True)
    return _engine


def engine():
    return _engine


@contextmanager
def tx():
    """One transaction per with-block; commits on success, rolls back on error."""
    with _engine.begin() as conn:
        yield conn


def all_(conn, sql, **p):
    return [dict(r) for r in conn.execute(text(sql), p).mappings().all()]


def one(conn, sql, **p):
    r = conn.execute(text(sql), p).mappings().first()
    return dict(r) if r else None


def scalar(conn, sql, **p):
    return conn.execute(text(sql), p).scalar()


def run(conn, sql, **p):
    """Execute; returns lastrowid for INSERTs, else rowcount."""
    res = conn.execute(text(sql), p)
    return res.lastrowid if res.lastrowid else res.rowcount


def insert(conn, table, data):
    cols = ", ".join(f"`{k}`" for k in data)
    vals = ", ".join(f":{k}" for k in data)
    return run(conn, f"INSERT INTO `{table}` ({cols}) VALUES ({vals})", **data)


def update(conn, table, data, where_col, where_val):
    if not data:
        return 0
    sets = ", ".join(f"`{k}`=:{k}" for k in data)
    return run(conn, f"UPDATE `{table}` SET {sets} WHERE `{where_col}`=:__w", __w=where_val, **data)
