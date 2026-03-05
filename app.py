"""
Odds Magician — Courtyard.io Mystery Pack EV Tracker
Backend: Flask + APScheduler + SQLite
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

import calibration
import db

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)
logger = logging.getLogger('odds_magician')

# ─── Config ──────────────────────────────────────────────────────────────────

COURTYARD_API = 'https://api.courtyard.io'
POLL_INTERVAL_SECONDS = 60
PULL_FETCH_LIMIT = 100   # max pulls per poll
SERVER_PORT = int(os.environ.get('PORT', 5001))  # Railway sets PORT

# Headers that mimic a real browser request (avoids 403s from the API)
REQUEST_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/122.0.0.0 Safari/537.36'
    ),
    'Origin':  'https://courtyard.io',
    'Referer': 'https://courtyard.io/',
    'Accept':  'application/json, text/plain, */*',
}

# Tracking Pokémon Starter ($25) and Pro ($50) packs
TRACKED_PACKS = ['pkmn-basic-pack', 'pkmn-starter-pack', 'pkmn-pro-pack', 'pkmn-master-pack']

# Phase 2 (add more packs here):
# TRACKED_PACKS = [
#   'pkmn-basic-pack', 'pkmn-starter-pack', 'pkmn-pro-pack',
#   'pkmn-master-pack', 'pkmn-platinum-pack', 'pkmn-diamond-pack',
# ]

_last_poll: dict[str, str] = {}   # pack_id → ISO timestamp of last poll
_poll_errors: dict[str, str] = {} # pack_id → last error message

# ─── Courtyard API helpers ────────────────────────────────────────────────────

def _get(url: str, **params) -> dict | list | None:
    try:
        r = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        logger.error(f'HTTP {e.response.status_code} fetching {url}')
    except Exception as e:
        logger.error(f'Error fetching {url}: {e}')
    return None


def fetch_pack_config(pack_id: str) -> dict | None:
    return _get(f'{COURTYARD_API}/vending-machines/{pack_id}')


def fetch_recent_pulls(pack_id: str, limit: int = PULL_FETCH_LIMIT) -> list:
    data = _get(
        f'{COURTYARD_API}/index/query/recent-pulls',
        limit=limit,
        vendingMachineIds=pack_id,
        includeRaw='true',
    )
    return (data or {}).get('assets', [])


def fetch_all_vending_machines() -> list:
    data = _get(f'{COURTYARD_API}/vending-machines')
    return (data or {}).get('vendingMachines', [])


# ─── Polling worker ───────────────────────────────────────────────────────────

def poll_pack(pack_id: str) -> None:
    """Fetch latest config + pulls for one pack, persist, compute EV."""
    logger.info(f'Polling {pack_id}…')
    config = fetch_pack_config(pack_id)

    if config:
        price = config.get('saleDetails', {}).get('salePriceUsd')
        odds = config.get('odds', {})
        pack_title = config.get('title', pack_id)
        # Always save a snapshot so price/title are recorded even for no-odds packs
        db.save_odds_snapshot(pack_id, price, odds or {}, pack_title=pack_title)

    pulls = fetch_recent_pulls(pack_id)
    new_count = 0
    for asset in pulls:
        cid = asset.get('collectible_id')
        fmv = asset.get('fmv_estimate_usd')
        if not cid or fmv is None:
            continue
        title = asset.get('title', '')
        tx_time = asset.get('tx_time', '')
        # prefer cropped_image, fallback to first asset_picture
        image = asset.get('cropped_image') or (
            asset.get('asset_pictures') or ['']
        )[0]
        if db.save_pull(pack_id, cid, title, fmv, tx_time, image):
            new_count += 1

    if new_count:
        logger.info(f'  Stored {new_count} new pulls for {pack_id}')

    if config:
        ev = calibration.compute_ev(pack_id, config)
        if ev:
            db.save_ev_snapshot(pack_id, ev)
            sign = '+' if ev['positive_ev'] else ''
            logger.info(
                f'  EV ratio: {ev["ev_ratio"]:.4f}  '
                f'EV $: ${ev["ev_usd"]:.2f}  '
                f'obs: {ev["total_obs"]}  '
                f'conf: {ev["overall_confidence"]:.2f}'
            )

    _last_poll[pack_id] = datetime.now(timezone.utc).isoformat()
    _poll_errors.pop(pack_id, None)


def poll_all() -> None:
    for pack_id in TRACKED_PACKS:
        try:
            poll_pack(pack_id)
        except Exception as e:
            logger.exception(f'Unhandled error polling {pack_id}')
            _poll_errors[pack_id] = str(e)


# ─── Flask app ────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder='static', static_url_path='')
CORS(app)


@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/api/status')
def status():
    return jsonify({
        'tracked_packs': TRACKED_PACKS,
        'last_poll': _last_poll,
        'errors': _poll_errors,
        'poll_interval_seconds': POLL_INTERVAL_SECONDS,
    })


@app.route('/api/packs')
def get_packs():
    result = []
    for pack_id in TRACKED_PACKS:
        ev = db.get_latest_ev(pack_id)
        odds = db.get_latest_odds(pack_id)
        result.append({
            'id': pack_id,
            'title': odds['pack_title'] if odds else pack_id,
            'price': odds['price'] if odds else None,
            'ev': ev,
            'total_pulls': db.get_pull_count(pack_id),
            'last_poll': _last_poll.get(pack_id),
        })
    return jsonify(result)


@app.route('/api/packs/<pack_id>/ev')
def get_ev(pack_id):
    ev = db.get_latest_ev(pack_id)
    return jsonify(ev or {})


@app.route('/api/packs/<pack_id>/ev/history')
def get_ev_history(pack_id):
    limit = request.args.get('limit', 500, type=int)
    return jsonify(db.get_ev_history(pack_id, limit))


@app.route('/api/packs/<pack_id>/pulls')
def get_pulls(pack_id):
    limit = request.args.get('limit', 50, type=int)
    return jsonify(db.get_recent_pulls(pack_id, limit))


@app.route('/api/packs/<pack_id>/calibration')
def get_calibration(pack_id):
    odds_row = db.get_latest_odds(pack_id)
    if not odds_row:
        return jsonify({'error': 'No data yet'}), 404
    stored = json.loads(odds_row['buckets_json'])
    config = {
        'odds': stored if stored else None,
        'saleDetails': {'salePriceUsd': odds_row['price']},
    }
    result = calibration.compute_ev(pack_id, config, detailed=True)
    return jsonify(result or {})


@app.route('/api/packs/<pack_id>/odds')
def get_odds(pack_id):
    row = db.get_latest_odds(pack_id)
    if not row:
        return jsonify({'error': 'No odds data yet'}), 404
    return jsonify({
        **dict(row),
        'buckets': json.loads(row['buckets_json']).get('buckets', []),
    })


# Manual poll trigger (useful for testing)
@app.route('/api/poll', methods=['POST'])
def trigger_poll():
    pack_id = request.json.get('pack_id') if request.json else None
    if pack_id and pack_id in TRACKED_PACKS:
        threading.Thread(target=poll_pack, args=(pack_id,), daemon=True).start()
    else:
        threading.Thread(target=poll_all, daemon=True).start()
    return jsonify({'status': 'polling started'})


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    db.init_db()
    logger.info('Database initialised.')

    # Initial poll on startup
    logger.info('Running initial poll…')
    poll_all()

    # Background scheduler: poll every 60 seconds
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(poll_all, 'interval', seconds=POLL_INTERVAL_SECONDS, id='poll_all')
    scheduler.start()
    logger.info(f'Scheduler started — polling every {POLL_INTERVAL_SECONDS}s.')

    app.run(host='0.0.0.0', port=SERVER_PORT, debug=False, use_reloader=False)
