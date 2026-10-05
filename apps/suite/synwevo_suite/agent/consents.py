"""Person-approved repeated chat spending, separate from business-tool authority.

An accepted run consumes one explicitly allowed run, not another wallet hold. Its charge is
still the trusted actual usage under the existing quote/ledger contract. A consent is never
inferred from a period limit, a balance, a prior send or a model response.
"""
from __future__ import annotations

import json
from synwevo_core.billing import BillingError
from synwevo_core.billing.models import MAX_INTEGER, integer
from synwevo_core.store import new_id
from .gateway import Route, _json


def off_peak_rates(sale):
    """Off-peak rates of a stored time-banded sale tariff as decimal strings; None when flat."""
    band = sale.get("off_peak")
    return None if band is None else {key: {"numerator": str(band[key]["numerator"]), "denominator": str(band[key]["denominator"])}
                                      for key in ("input_uncached", "input_cached", "output")}


class ModelConsents:
    def __init__(self, quotes):
        self.quotes, self.store, self.ledger = quotes, quotes.store, quotes.ledger

    def _transaction(self):
        with self.store.conn() as t:
            if not t.in_transaction or not getattr(self.store._local, 'depth', 0):
                raise BillingError('consent_requires_transaction')
            return t

    def _row(self, user_id, consent_id):
        with self.store.conn() as t:
            self.ledger._active_user(t, user_id)
        row = self.store.one('SELECT * FROM agent_model_consents WHERE id=? AND user_id=? '
            'AND environment=? AND namespace=?',
            (consent_id, user_id, self.ledger.environment, self.ledger.namespace))
        if not row:
            raise BillingError('consent_not_found')
        return row

    def _used(self, consent_id):
        return self.store.one('SELECT COUNT(*) AS n FROM agent_quote_consents WHERE consent_id=?',
                              (consent_id,))['n']

    def _status(self, row):
        if row['revoked_at'] is not None:
            return 'revoked'
        if row['expires_at'] is not None and row['expires_at'] <= self.ledger._now():
            return 'expired'
        conv = self.store.one('SELECT user_id,status,scope_json FROM agent_conversations WHERE id=?',
                              (row['conversation_id'],))
        if not conv or conv['user_id'] != row['user_id'] or conv['status'] != 'active':
            return 'conversation_unavailable'
        if conv['scope_json'] != row['scope_json']:
            return 'scope_changed'
        try:
            route = Route(**json.loads(row['terms_json'])['route'])
            if route.valid_until <= self.ledger._now() or self.quotes.current_route() != route:
                return 'model_changed'
        except BillingError:
            return 'model_changed'
        return 'active'

    def create(self, user_id, quote_id, *, max_runs, expires_at, until_revoked):
        self._transaction()
        integer(max_runs, positive=True, maximum=1000)
        if type(until_revoked) is not bool or ((expires_at is None) != until_revoked):
            raise BillingError('consent_expiry_required')
        now = self.ledger._now()
        if expires_at is not None:
            integer(expires_at, positive=True)
            if expires_at <= now or expires_at > now + 366 * 86400:
                raise BillingError('consent_invalid_expiry')
        basis = self.quotes._row(user_id, quote_id)
        if basis['revoked_at'] is not None or basis['expires_at'] <= now or basis['accepted_turn_id']:
            raise BillingError('quote_expired_or_revoked')
        conv = self.quotes._conversation(user_id, basis['conversation_id'])
        if conv['scope_json'] is None:
            raise BillingError('consent_explicit_scope_required')
        if conv['scope_json'] != basis['scope_json']:
            raise BillingError('quote_context_changed')
        terms = json.loads(basis['terms_json'])
        if self.quotes.current_route() != Route(**terms['route']):
            raise BillingError('model_configuration_changed')
        if terms['route']['valid_until'] <= now:
            raise BillingError('model_route_unavailable')
        if terms['budget'] * max_runs > MAX_INTEGER:
            raise BillingError('consent_total_too_large')
        # Lifetimes and terms are immutable; replacing a consent means explicit revoke + review.
        prior = self.store.one('SELECT 1 FROM agent_model_consents WHERE user_id=? AND conversation_id=? '
            'AND environment=? AND namespace=? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>?)',
            (user_id, conv['id'], self.ledger.environment, self.ledger.namespace, now))
        if prior:
            raise BillingError('consent_already_exists')
        count = self.store.one('SELECT COUNT(*) AS n FROM agent_model_consents WHERE user_id=? '
            'AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>?)', (user_id, now))['n']
        if count >= 100:
            raise BillingError('consent_limit_exceeded')
        terms['run_seconds'] = terms['deadline'] - basis['created_at']
        consent_id = new_id('consent')
        self.store.execute('INSERT INTO agent_model_consents '
            '(id,environment,namespace,user_id,conversation_id,basis_quote_id,scope_json,terms_json,max_runs,created_at,expires_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?)', (consent_id, self.ledger.environment, self.ledger.namespace,
                user_id, conv['id'], quote_id, conv['scope_json'], _json(terms), max_runs, now, expires_at))
        # This reviewed quote became the consent basis, not a second independent grant.
        # Retire it in the same transaction so a late single-run acceptance cannot escape
        # the count or revocation of the consent the person actually approved.
        self.store.execute('UPDATE agent_model_quotes SET revoked_at=? WHERE id=?', (now, quote_id))
        return self.get(user_id, consent_id)

    def get(self, user_id, consent_id):
        row = self._row(user_id, consent_id)
        terms = json.loads(row['terms_json'])
        sale, route = terms['sale'], terms['route']
        used = self._used(consent_id)
        status = self._status(row)
        if status == 'active' and used >= row['max_runs']:
            status = 'exhausted'
        return {'id': row['id'], 'environment': row['environment'], 'conversation_id': row['conversation_id'],
            'scope': json.loads(row['scope_json']), 'max_runs': row['max_runs'], 'used_runs': used,
            'remaining_runs': max(0, row['max_runs'] - used), 'status': status,
            'created_at': row['created_at'], 'expires_at': row['expires_at'], 'until_revoked': row['expires_at'] is None,
            'model': route['requested_model'], 'confirmed_route': route['confirmed_route'], 'route_version': route['version'],
            'price_id': sale['price_id'], 'unit': sale['unit'], 'terms_ref': sale['terms_ref'],
            'rates': {key: {name: str(sale[key][name]) for name in ('numerator', 'denominator')}
                      for key in ('input_uncached', 'input_cached', 'output')},
            'off_peak_rates': off_peak_rates(sale), 'per_tokens': sale['per_tokens'], 'per_call_hold': str(terms['per_call_hold']),
            'budget': str(terms['budget']), 'total_maximum': str(terms['budget'] * row['max_runs']),
            'max_calls': terms['max_calls'], 'run_seconds': terms['run_seconds'],
            'payer': self.quotes.payer_of(terms) | ({'id': user_id} if self.quotes.payer_of(terms)['kind'] == 'user' else {})}

    def list(self, user_id, conversation_id):
        with self.store.tx() as t:
            self.ledger._active_user(t, user_id)
            rows = t.execute('SELECT id FROM agent_model_consents WHERE user_id=? AND conversation_id=? '
                'AND environment=? AND namespace=? ORDER BY created_at DESC,rowid DESC LIMIT 20',
                (user_id, conversation_id, self.ledger.environment, self.ledger.namespace)).fetchall()
            return {'consents': [self.get(user_id, row['id']) for row in rows]}

    def revoke(self, user_id, consent_id):
        self._transaction()
        row = self._row(user_id, consent_id)
        if row['revoked_at'] is None:
            self.store.execute('UPDATE agent_model_consents SET revoked_at=? WHERE id=?',
                                (self.ledger._now(), consent_id))
        return self.get(user_id, consent_id)

    def issue(self, user_id, consent_id, conversation, *, text, parent_id, regenerate_of):
        """Same transaction as the new quote, its binding and the Agent turn insert."""
        self._transaction()
        row = self._row(user_id, consent_id)
        if row['conversation_id'] != conversation['id']:
            raise BillingError('consent_not_found')
        status = self._status(row)
        if status != 'active':
            raise BillingError('consent_' + status)
        used = self._used(consent_id)
        if used >= row['max_runs']:
            raise BillingError('consent_exhausted')
        terms = json.loads(row['terms_json'])
        # A run may be shorter than approved, never longer than the consent or the route allows.
        now = self.ledger._now()
        seconds = min(terms['run_seconds'], terms['route']['valid_until'] - now)
        if row['expires_at'] is not None:
            seconds = min(seconds, row['expires_at'] - now)
        payer = self.quotes.payer_of(terms)
        quote = self.quotes.issue(user_id, conversation, text=text, parent_id=parent_id,
            regenerate_of=regenerate_of, budget=terms['budget'], max_calls=terms['max_calls'], run_seconds=seconds,
            payer=payer if payer['kind'] == 'organization' else None)
        issued = json.loads(self.quotes._row(user_id, quote['id'])['terms_json'])
        if any(issued[key] != terms[key] for key in ('route', 'sale', 'budget', 'max_calls', 'per_call_hold')):
            raise BillingError('consent_model_changed')
        if self.quotes.payer_of(issued) != self.quotes.payer_of(terms):
            raise BillingError('consent_model_changed')
        self.store.execute('INSERT INTO agent_quote_consents VALUES (?,?,?)',
                            (quote['id'], consent_id, used + 1))
        return quote['id']

    def authorize(self, transaction, user_id, quote_id):
        linked = transaction.execute('SELECT consent_id FROM agent_quote_consents WHERE quote_id=?', (quote_id,)).fetchone()
        if not linked:
            return True  # Existing single-run quote contract is unchanged.
        row = self._row(user_id, linked['consent_id'])
        # Exhaustion blocks another run, not already accepted runs within their fixed budgets.
        return self._status(row) == 'active'
