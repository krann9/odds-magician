import json
import os
from datetime import datetime

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get('DATABASE_URL')


def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    return conn


def _to_iso(val):
    """Convert a datetime object to ISO string, leave strings as-is."""
    if isinstance(val, datetime):
        return val.isoformat()
    return val


def _serialize(row: dict) -> dict:
    """Convert any datetime values in a row dict to ISO strings."""
    return {k: _to_iso(v) for k, v in row.items()}


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS pulls (
        id SERIAL PRIMARY KEY,
        pack_id TEXT NOT NULL,
        collectible_id TEXT NOT NULL,
        title TEXT,
        fmv_usd REAL NOT NULL,
        tx_time TEXT,
        image_url TEXT,
        created_at TIMESTAMP DEFAULT NOW(),
        UNIQUE(pack_id, collectible_id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS odds_snapshots (
        id SERIAL PRIMARY KEY,
        pack_id TEXT NOT NULL,
        pack_title TEXT,
        price REAL,
        buckets_json TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT NOW()
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS ev_snapshots (
        id SERIAL PRIMARY KEY,
        pack_id TEXT NOT NULL,
        ev_usd REAL NOT NULL,
        ev_ratio REAL NOT NULL,
        overall_confidence REAL,
        total_obs INTEGER,
        details_json TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    )''')

    c.execute('CREATE INDEX IF NOT EXISTS idx_pulls_pack_time ON pulls(pack_id, tx_time DESC)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_ev_pack_time ON ev_snapshots(pack_id, created_at DESC)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_odds_pack_time ON odds_snapshots(pack_id, created_at DESC)')

    conn.commit()
    conn.close()


def save_pull(pack_id, collectible_id, title, fmv_usd, tx_time, image_url):
    """Insert a pull. Returns True if new, False if duplicate."""
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute(
            '''INSERT INTO pulls (pack_id, collectible_id, title, fmv_usd, tx_time, image_url)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (pack_id, collectible_id) DO NOTHING''',
            (pack_id, collectible_id, title, fmv_usd, tx_time, image_url)
        )
        inserted = c.rowcount > 0
        conn.commit()
        return inserted
    finally:
        conn.close()


def save_odds_snapshot(pack_id, price, odds, pack_title=None):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute(
            '''INSERT INTO odds_snapshots (pack_id, pack_title, price, buckets_json)
               VALUES (%s, %s, %s, %s)''',
            (pack_id, pack_title, price, json.dumps(odds))
        )
        conn.commit()
    finally:
        conn.close()


def save_ev_snapshot(pack_id, ev_result):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute(
            '''INSERT INTO ev_snapshots (pack_id, ev_usd, ev_ratio, overall_confidence, total_obs, details_json)
               VALUES (%s, %s, %s, %s, %s, %s)''',
            (
                pack_id,
                ev_result['ev_usd'],
                ev_result['ev_ratio'],
                ev_result.get('overall_confidence'),
                ev_result.get('total_obs'),
                json.dumps(ev_result),
            )
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_ev(pack_id):
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute(
            '''SELECT * FROM ev_snapshots WHERE pack_id=%s ORDER BY created_at DESC LIMIT 1''',
            (pack_id,)
        )
        row = c.fetchone()
        if row:
            d = _serialize(dict(row))
            if d.get('details_json'):
                d.update(json.loads(d['details_json']))
                del d['details_json']
            return d
        return None
    finally:
        conn.close()


def get_ev_history(pack_id, limit=100_000):
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute(
            '''SELECT ev_ratio, ev_usd, overall_confidence, total_obs, created_at
               FROM ev_snapshots WHERE pack_id=%s
               ORDER BY created_at DESC LIMIT %s''',
            (pack_id, limit)
        )
        rows = [_serialize(dict(r)) for r in c.fetchall()]
        return list(reversed(rows))
    finally:
        conn.close()


def get_recent_pulls(pack_id, limit=50):
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute(
            '''SELECT * FROM pulls WHERE pack_id=%s ORDER BY tx_time DESC LIMIT %s''',
            (pack_id, limit)
        )
        return [_serialize(dict(r)) for r in c.fetchall()]
    finally:
        conn.close()


def get_pulls_for_calibration(pack_id, limit=2000):
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute(
            '''SELECT fmv_usd, tx_time FROM pulls WHERE pack_id=%s ORDER BY tx_time DESC LIMIT %s''',
            (pack_id, limit)
        )
        return [dict(r) for r in c.fetchall()]
    finally:
        conn.close()


def get_latest_odds(pack_id):
    conn = get_conn()
    try:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute(
            '''SELECT * FROM odds_snapshots WHERE pack_id=%s ORDER BY created_at DESC LIMIT 1''',
            (pack_id,)
        )
        row = c.fetchone()
        return _serialize(dict(row)) if row else None
    finally:
        conn.close()


def get_pull_count(pack_id):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM pulls WHERE pack_id=%s', (pack_id,))
        return c.fetchone()[0]
    finally:
        conn.close()


def pulls_since_last_tier(pack_id: str, fmv_ranges: list[tuple]) -> int | None:
    """
    Count how many pulls occurred after the most recent pull whose FMV falls
    within any of the given (lo, hi) ranges (inclusive).

    Returns None if that tier has never been pulled, else an int (0 = last pull
    was that tier, n = n pulls have happened since).
    """
    if not fmv_ranges:
        return None
    conn = get_conn()
    try:
        c = conn.cursor()

        # Build fully-parameterised range conditions
        conditions = ' OR '.join(
            '(fmv_usd >= %s AND fmv_usd <= %s)' for _ in fmv_ranges
        )
        range_params = [v for lo, hi in fmv_ranges for v in (lo, hi)]

        # Find the tx_time of the most recent qualifying pull
        c.execute(
            f'SELECT tx_time FROM pulls WHERE pack_id = %s AND ({conditions}) '
            f'ORDER BY tx_time DESC LIMIT 1',
            [pack_id] + range_params,
        )
        row = c.fetchone()
        if not row or not row[0]:
            return None  # tier never pulled

        last_time = row[0]

        # Count pulls strictly newer than that timestamp
        c.execute(
            'SELECT COUNT(*) FROM pulls WHERE pack_id = %s AND tx_time > %s',
            (pack_id, last_time),
        )
        return c.fetchone()[0]
    finally:
        conn.close()
