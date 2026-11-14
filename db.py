import atexit
import os
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/invoices"
)
SCHEMA = (Path(__file__).parent / "schema.sql").read_text()

# autocommit + no prepared statements is what the LangGraph checkpointer needs from a shared pool
pool = ConnectionPool(
    DATABASE_URL,
    kwargs={"autocommit": True, "row_factory": dict_row, "prepare_threshold": None},
    open=False,
)
atexit.register(pool.close)


def query(sql, params=None):
    with pool.connection() as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else []


def reset():
    """Drop and reload the demo tables."""
    query(SCHEMA)


def init():
    pool.open()
    if not query("select to_regclass('public.invoices') as t")[0]["t"]:
        reset()
