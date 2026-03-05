"""
Calibration engine for Odds Magician.

Uses exponential decay weighting to compute calibrated bucket averages,
then sums bucket_ev = P(bucket) * calibrated_avg across all buckets.

Key insight: Courtyard's published odds buckets skew toward the bottom of
their ranges — real averages run ~10-15% below midpoint estimates.
We apply a conservative discount when data is sparse and blend toward
actual weighted averages as observation count grows.
"""

import math
import logging
from datetime import datetime, timezone

import db

logger = logging.getLogger(__name__)

HALF_LIFE_HOURS = 24.0
LAMBDA = math.log(2) / HALF_LIFE_HOURS  # decay constant ≈ 0.02888 per hour

# Courtyard buckets skew low: use 12% below midpoint as prior
CONSERVATIVE_DISCOUNT = 0.88

# At this many observations per bucket we trust the data fully
FULL_TRUST_OBS = 10


def decay_weight(tx_time_str: str) -> float:
    """Exponential decay weight for a pull given its timestamp."""
    if not tx_time_str:
        return 1.0
    try:
        s = tx_time_str.replace('Z', '+00:00')
        tx = datetime.fromisoformat(s)
        if tx.tzinfo is None:
            tx = tx.replace(tzinfo=timezone.utc)
        hours_ago = (datetime.now(timezone.utc) - tx).total_seconds() / 3600.0
        return math.exp(-LAMBDA * max(0.0, hours_ago))
    except Exception:
        return 1.0


def confidence_label(n_obs: int) -> str:
    if n_obs == 0:
        return 'none'
    if n_obs < 3:
        return 'very_low'
    if n_obs < 10:
        return 'low'
    if n_obs < 30:
        return 'medium'
    return 'high'


def confidence_score(n_obs: int) -> float:
    """0.0 → 1.0 confidence score based on observation count."""
    if n_obs == 0:
        return 0.0
    if n_obs < 3:
        return 0.05 + (n_obs / 3.0) * 0.25   # 0.05 – 0.30
    if n_obs < 10:
        return 0.30 + ((n_obs - 3) / 7.0) * 0.35  # 0.30 – 0.65
    if n_obs < 30:
        return 0.65 + ((n_obs - 10) / 20.0) * 0.25  # 0.65 – 0.90
    return min(1.0, 0.90 + (n_obs - 30) / 200.0)    # 0.90 → 1.0


def calibrate_bucket(bucket: dict, pulls: list[dict]) -> dict:
    """
    Compute calibrated average for one odds bucket.

    Args:
        bucket: {minValueUsd, maxValueUsd, oddsPercent, tier}
        pulls:  [{fmv_usd, tx_time}, ...]

    Returns dict with calibrated_avg, n_obs, confidence_*, etc.
    """
    lo = bucket['minValueUsd']
    hi = bucket['maxValueUsd']
    midpoint = (lo + hi) / 2.0
    conservative_prior = midpoint * CONSERVATIVE_DISCOUNT

    # Assign pulls whose FMV falls in [lo, hi]
    in_bucket = [p for p in pulls if lo <= p['fmv_usd'] <= hi]
    n = len(in_bucket)

    cscore = confidence_score(n)
    clabel = confidence_label(n)

    if n == 0:
        return {
            'calibrated_avg': round(conservative_prior, 2),
            'n_obs': 0,
            'confidence_label': 'none',
            'confidence_score': 0.0,
            'weighted_avg': None,
            'midpoint': round(midpoint, 2),
            'conservative_prior': round(conservative_prior, 2),
        }

    # Exponential-decay-weighted average
    total_w = 0.0
    weighted_sum = 0.0
    for p in in_bucket:
        w = decay_weight(p['tx_time'])
        weighted_sum += p['fmv_usd'] * w
        total_w += w

    weighted_avg = weighted_sum / total_w if total_w > 0 else midpoint

    # Blend: 0 obs → pure prior; FULL_TRUST_OBS → pure weighted average
    blend = min(1.0, n / FULL_TRUST_OBS)
    calibrated = blend * weighted_avg + (1.0 - blend) * conservative_prior

    return {
        'calibrated_avg': round(calibrated, 2),
        'n_obs': n,
        'confidence_label': clabel,
        'confidence_score': round(cscore, 3),
        'weighted_avg': round(weighted_avg, 2),
        'midpoint': round(midpoint, 2),
        'conservative_prior': round(conservative_prior, 2),
    }


def compute_ev_no_odds(pack_id: str, price: float, pulls: list[dict]) -> dict | None:
    """
    Fallback EV for packs that don't publish odds buckets.
    Uses the raw decay-weighted average of all observed pulls.
    """
    if not pulls:
        return None

    total_w = 0.0
    weighted_sum = 0.0
    for p in pulls:
        w = decay_weight(p['tx_time'])
        weighted_sum += p['fmv_usd'] * w
        total_w += w

    avg_fmv = weighted_sum / total_w if total_w else 0.0
    n = len(pulls)
    cscore = confidence_score(n)
    ev_ratio = avg_fmv / price

    return {
        'pack_id': pack_id,
        'pack_price': price,
        'ev_usd': round(avg_fmv, 2),
        'ev_ratio': round(ev_ratio, 4),
        'overall_confidence': round(cscore, 3),
        'total_obs': n,
        'positive_ev': ev_ratio >= 1.0,
        'no_odds_data': True,   # flag for the UI
    }


def compute_ev(pack_id: str, config: dict, detailed: bool = False) -> dict | None:
    """
    Compute expected value for a pack.

    Args:
        pack_id:  e.g. 'pkmn-pro-pack'
        config:   vending machine config from API
        detailed: if True, include full per-bucket breakdown

    Returns dict or None on failure.
    """
    odds = config.get('odds') or {}
    buckets = odds.get('buckets', [])
    price = config.get('saleDetails', {}).get('salePriceUsd')

    if not price:
        logger.warning(f'compute_ev: missing price for {pack_id}')
        return None

    pulls = db.get_pulls_for_calibration(pack_id)

    # Packs without published odds: use raw average as fallback
    if not buckets:
        logger.info(f'compute_ev: no odds buckets for {pack_id}, using raw average fallback')
        return compute_ev_no_odds(pack_id, price, pulls)


    total_ev = 0.0
    bucket_results = []

    for bucket in buckets:
        p_bucket = bucket['oddsPercent'] / 100.0
        cal = calibrate_bucket(bucket, pulls)

        bucket_ev = p_bucket * cal['calibrated_avg']
        total_ev += bucket_ev

        bucket_results.append({
            'min_value': bucket['minValueUsd'],
            'max_value': bucket['maxValueUsd'],
            'odds_pct': bucket['oddsPercent'],
            'tier': bucket.get('tier', ''),
            **cal,
            'bucket_ev': round(bucket_ev, 2),
        })

    # Overall confidence: probability-weighted average of bucket confidences
    overall_confidence = sum(
        b['confidence_score'] * b['odds_pct'] / 100.0
        for b in bucket_results
    )
    total_obs = sum(b['n_obs'] for b in bucket_results)
    ev_ratio = total_ev / price

    result = {
        'pack_id': pack_id,
        'pack_price': price,
        'ev_usd': round(total_ev, 2),
        'ev_ratio': round(ev_ratio, 4),
        'overall_confidence': round(overall_confidence, 3),
        'total_obs': total_obs,
        'positive_ev': ev_ratio >= 1.0,
    }

    if detailed:
        result['buckets'] = bucket_results

    return result
