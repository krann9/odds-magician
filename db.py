import sqlite3
import json
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'odds_magician.db')


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS pulls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pack_id TEXT NOT NULL,
        collectible_id TEXT NOT NULL,
        title TEXT,
        fmv_usd REAL NOT NULL,
        tx_time TEXT,
        image_url TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(pack_id, collectible_id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS odds_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pack_id TEXT NOT NULL,
        pack_title TEXT,
        price REAL,
        buckets_json TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now'))
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS ev_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pack_id TEXT NOT NULL,
        ev_usd REAL NOT NULL,
        ev_ratio REAL NOT NULL,
        overall_confidence REAL,
        total_obs INTEGER,
        details_json TEXT,
        created_at TEXT DEFAULT (datetime('now'))
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
        conn.execute(
            '''INSERT OR IGNORE INTO pulls (pack_id, collectible_id, title, fmv_usd, tx_time, image_url)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (pack_id, collectible_id, title, fmv_usd, tx_time, image_url)
        )
        inserted = conn.total_changes > 0
        conn.commit()
        return inserted
    finally:
        conn.close()


def save_odds_snapshot(pack_id, price, odds, pack_title=None):
    conn = get_conn()
    try:
        conn.execute(
            '''INSERT INTO odds_snapshots (pack_id, pack_title, price, buckets_json)
               VALUES (?, ?, ?, ?)''',
            (pack_id, pack_title, price, json.dumps(odds))
        )
        conn.commit()
    finally:
        conn.close()


def save_ev_snapshot(pack_id, ev_result):
    conn = get_conn()
    try:
        conn.execute(
            '''INSERT INTO ev_snapshots (pack_id, ev_usd, ev_ratio, overall_confidence, total_obs, details_json)
               VALUES (?, ?, ?, ?, ?, ?)''',
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
        row = conn.execute(
            '''SELECT * FROM ev_snapshots WHERE pack_id=? ORDER BY created_at DESC LIMIT 1''',
            (pack_id,)
        ).fetchone()
        if row:
            d = dict(row)
            if d.get('details_json'):
                d.update(json.loads(d['details_json']))
                del d['details_json']
            return d
        return None
    finally:
        conn.close()


def get_ev_history(pack_id, limit=500):
    conn = get_conn()
    try:
        rows = conn.execute(
            '''SELECT ev_ratio, ev_usd, overall_confidence, total_obs, created_at
               FROM ev_snapshots WHERE pack_id=?
               ORDER BY created_at DESC LIMIT ?''',
            (pack_id, limit)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()


def get_recent_pulls(pack_id, limit=50):
    conn = get_conn()
    try:
        rows = conn.execute(
            '''SELECT * FROM pulls WHERE pack_id=? ORDER BY tx_time DESC LIMIT ?''',
            (pack_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_pulls_for_calibration(pack_id, limit=2000):
    conn = get_conn()
    try:
        rows = conn.execute(
            '''SELECT fmv_usd, tx_time FROM pulls WHERE pack_id=? ORDER BY tx_time DESC LIMIT ?''',
            (pack_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_latest_odds(pack_id):
    conn = get_conn()
    try:
        row = conn.execute(
            '''SELECT * FROM odds_snapshots WHERE pack_id=? ORDER BY created_at DESC LIMIT 1''',
            (pack_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_pull_count(pack_id):
    conn = get_conn()
    try:
        row = conn.execute(
            'SELECT COUNT(*) as cnt FROM pulls WHERE pack_id=?', (pack_id,)
        ).fetchone()
        return row['cnt'] if row else 0
    finally:
        conn.close()
