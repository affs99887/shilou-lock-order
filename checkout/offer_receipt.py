"""Bounded, redacted history of shared-hint acceptance, separate from settings."""
import json
import time

from checkout_service import atomic_json


OUTCOMES = frozenset({
    'queued', 'not_armed', 'paused', 'busy', 'review', 'stopping',
    'order_review', 'blocked', 'activating', 'profile_required',
    'cooldown', 'ineligible', 'empty', 'queue_full',
})
MAX_EVENTS = 120


def sanitized(event):
    if (not isinstance(event, dict) or not isinstance(event.get('outcome'), str) or
            event['outcome'] not in OUTCOMES):
        return None
    stamp, count, selected = event.get('atMs'), event.get('candidates'), event.get('selectedIndex')
    if type(stamp) is not int or stamp < 0 or type(count) is not int or not 0 <= count <= 1000:
        return None
    if event['outcome'] == 'queued':
        if type(selected) is not int or not 0 <= selected < count:
            return None
    elif selected is not None:
        return None
    if any(type(event.get(field)) is not bool for field in ('armed', 'busy', 'stopping')):
        return None
    return {key: event.get(key) for key in (
        'atMs', 'outcome', 'candidates', 'selectedIndex', 'armed', 'busy', 'stopping')}


class OfferHistory:
    def __init__(self, directory):
        self.path = directory / 'checkout-offers.json'
        self.events = []
        try:
            if self.path.stat().st_size > 65536:
                return
            stored = json.loads(self.path.read_text(encoding='utf-8'))
            rows = stored.get('events', []) if isinstance(stored, dict) else []
            if isinstance(rows, list):
                self.events = [safe for row in rows[-MAX_EVENTS:]
                               if (safe := sanitized(row)) is not None]
        except (OSError, ValueError, TypeError):
            pass  # Diagnostics must never prevent starting or stopping checkout.

    def record(self, outcome, count, selected, status):
        event = sanitized(dict(atMs=int(time.time() * 1000), outcome=outcome,
                               candidates=count, selectedIndex=selected,
                               **{key: status[key] for key in ('armed', 'busy', 'stopping')}))
        if event is None:
            return
        self.events = (self.events + [event])[-MAX_EVENTS:]
        try:
            atomic_json(self.path, {'events': self.events})
        except OSError:
            pass  # A log failure must not change acceptance or replay an offer.
