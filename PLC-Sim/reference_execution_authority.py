"""Facility-issued execution grants, distinct from self-asserted executor IDs.

Only hashes of bearer credentials are stored. The environment owns locking,
transactions, real controller stops and material commits; this module never
commits or makes a device call on its own.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

# 兼容完整包名和既有源码目录入口，复用相同的公共校验。
if __package__:
    from .reference_operation_gate import canonical, identifier
    from .reference_execution_scope import execution_scope, validate_execution_identity
else:
    from reference_operation_gate import canonical, identifier
    from reference_execution_scope import execution_scope, validate_execution_identity


SCHEMA = 'labos.plate-authority/v1'
DISPATCH_SCHEMA = 'labos.plate-authority/v2'




def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()




class AuthorityError(ValueError):
    def __init__(self, code, status=403):
        self.code, self.status = code, status
        super().__init__(code)


class Authority:
    def __init__(self, db, admin_token, *, create=False, dispatch_identities=False):
        if not isinstance(admin_token, str) or len(admin_token) < 32:
            raise ValueError('authority_admin_token_too_short')
        if type(dispatch_identities) is not bool:
            raise ValueError('invalid_authority_identity_mode')
        self._dispatch_identities = dispatch_identities
        self.schema = DISPATCH_SCHEMA if dispatch_identities else SCHEMA
        self.db, self._admin = db, admin_token
        if create:
            # Individual DDL statements retain the caller's transaction boundary.
            for sql in (
                'CREATE TABLE authority_state(id INTEGER PRIMARY KEY CHECK(id=1),schema TEXT NOT NULL,admin_sha256 TEXT NOT NULL,generation INTEGER NOT NULL,active_grant_id TEXT)',
                'CREATE TABLE authority_grants(grant_id TEXT PRIMARY KEY,token_sha256 TEXT UNIQUE NOT NULL,generation INTEGER NOT NULL,status TEXT NOT NULL,issued_at REAL NOT NULL,revoked_at REAL)',
                'CREATE TABLE authority_transitions(sequence INTEGER PRIMARY KEY AUTOINCREMENT,transition_id TEXT UNIQUE NOT NULL,fingerprint TEXT NOT NULL,request_json TEXT NOT NULL,response_json TEXT NOT NULL)',
                'CREATE TABLE operation_kinds(operation_id TEXT PRIMARY KEY,kind TEXT NOT NULL)',
                'CREATE TABLE operation_resource_states(operation_id TEXT PRIMARY KEY,before_json TEXT NOT NULL,after_json TEXT)',
                'CREATE TABLE rejection_resource_states(rejection_sequence INTEGER PRIMARY KEY,state_json TEXT NOT NULL)',
                'CREATE TABLE operation_authority(operation_id TEXT PRIMARY KEY,grant_id TEXT NOT NULL,generation INTEGER NOT NULL,executor_id TEXT NOT NULL,epoch INTEGER NOT NULL,origin_instance_id TEXT NOT NULL)',
            ):
                if dispatch_identities and sql.startswith('CREATE TABLE operation_authority('):
                    sql = sql.replace('origin_instance_id TEXT NOT NULL', 'origin_instance_id TEXT')
                db.execute(sql)
            if dispatch_identities:
                db.execute('CREATE TABLE operation_execution_scopes(operation_id TEXT PRIMARY KEY,identity_json TEXT NOT NULL,scope_json TEXT NOT NULL)')
            db.execute('INSERT INTO authority_state VALUES(1,?,?,0,NULL)', (self.schema, digest(admin_token)))
        row = db.execute('SELECT schema,admin_sha256 FROM authority_state WHERE id=1').fetchone()
        if row != (self.schema, digest(admin_token)):
            raise RuntimeError('incompatible_existing_authority')

    def current(self):
        generation, grant_id = self.db.execute('SELECT generation,active_grant_id FROM authority_state WHERE id=1').fetchone()
        return {'grant_id': grant_id, 'generation': generation}

    def known(self, token):
        if not isinstance(token, str):
            return None
        row = self.db.execute('SELECT grant_id,generation,status FROM authority_grants WHERE token_sha256=?', (digest(token),)).fetchone()
        return None if row is None else {'grant_id': row[0], 'generation': row[1], 'status': row[2]}

    def authenticate(self, token):
        known = self.known(token)
        if known is None:
            raise AuthorityError('unauthorized')
        active = self.current()
        if known['status'] != 'active' or (known['grant_id'], known['generation']) != (active['grant_id'], active['generation']):
            raise AuthorityError('authority_revoked')
        return active

    def validate_transition(self, request, world_id):
        if not isinstance(request, dict) or not isinstance(request.get('admin_token'), str) or not hmac.compare_digest(request['admin_token'], self._admin):
            raise AuthorityError('authority_admin_unauthorized')
        action = request.get('action')
        fields = {'admin_token', 'world_id', 'action'}
        if action in ('activate', 'revoke'):
            fields |= {'transition_id', 'expected_generation'}
        if action == 'activate':
            fields |= {'grant_id', 'grant_token'}
        if action not in ('inspect', 'activate', 'revoke') or set(request) != fields:
            raise AuthorityError('invalid_authority_request', 400)
        if request['world_id'] != world_id:
            raise AuthorityError('world_mismatch', 409)
        if action == 'inspect':
            return None, None
        if not identifier(request['transition_id']) or type(request['expected_generation']) is not int or request['expected_generation'] < 0:
            raise AuthorityError('invalid_authority_transition', 400)
        safe = {k: v for k, v in request.items() if k not in ('admin_token', 'grant_token')}
        if action == 'activate':
            token = request['grant_token']
            if not identifier(request['grant_id']) or not isinstance(token, str) or len(token) < 32 or hmac.compare_digest(token, self._admin):
                raise AuthorityError('invalid_authority_grant', 400)
            safe['grant_token_sha256'] = digest(token)
        raw = canonical(safe)
        row = self.db.execute('SELECT fingerprint,response_json FROM authority_transitions WHERE transition_id=?', (request['transition_id'],)).fetchone()
        if row:
            if row[0] != digest(raw):
                raise AuthorityError('authority_transition_conflict', 409)
            return safe, json.loads(row[1])
        if request['expected_generation'] != self.current()['generation']:
            raise AuthorityError('authority_generation_conflict', 409)
        if action == 'activate' and self.db.execute('SELECT 1 FROM authority_grants WHERE grant_id=? OR token_sha256=?', (request['grant_id'], safe['grant_token_sha256'])).fetchone():
            raise AuthorityError('authority_grant_already_used', 409)
        return safe, None

    def apply(self, safe):
        previous = self.current()
        now, generation = time.time(), previous['generation'] + 1
        if previous['grant_id'] is not None:
            self.db.execute("UPDATE authority_grants SET status='revoked',revoked_at=? WHERE grant_id=?", (now, previous['grant_id']))
        new_grant = safe.get('grant_id') if safe['action'] == 'activate' else None
        if new_grant is not None:
            self.db.execute("INSERT INTO authority_grants VALUES (?,?,?,'active',?,NULL)",
                            (new_grant, safe['grant_token_sha256'], generation, now))
        self.db.execute('UPDATE authority_state SET generation=?,active_grant_id=? WHERE id=1', (generation, new_grant))
        return previous, self.current()

    def record_operation(self, operation_id, executor_id, epoch, identity):
        active = self.current()
        if self._dispatch_identities:
            validate_execution_identity(identity)
            scope = execution_scope(identity)
            origin = scope.get('origin_instance_id')
        else:
            # 原v1入口及原始表的语义保持；不自动升级旧账本。
            origin = identity['origin_instance_id']
        self.db.execute('INSERT INTO operation_authority VALUES (?,?,?,?,?,?)',
                        (operation_id, active['grant_id'], active['generation'], executor_id, epoch, origin))
        if self._dispatch_identities:
            self.db.execute('INSERT INTO operation_execution_scopes VALUES (?,?,?)',
                            (operation_id, canonical(identity), canonical(scope)))

    def revocation_scopes(self, grant_id):
        if self._dispatch_identities:
            return [json.loads(row[0]) for row in self.db.execute(
                'SELECT s.scope_json FROM operation_execution_scopes s JOIN operation_authority a USING(operation_id) WHERE a.grant_id=? ORDER BY a.operation_id',
                (grant_id,))]
        return [{'kind': 'legacy_origin', 'origin_instance_id': row[0]} for row in self.db.execute(
            'SELECT origin_instance_id FROM operation_authority WHERE grant_id=? ORDER BY operation_id', (grant_id,))]

    def record_transition(self, safe, response):
        raw = canonical(safe)
        self.db.execute('INSERT INTO authority_transitions(transition_id,fingerprint,request_json,response_json) VALUES(?,?,?,?)',
                        (safe['transition_id'], digest(raw), raw, canonical(response)))

    def checkpoint(self):
        # This redacted inventory is committed with every world checkpoint.
        return {'schema': self.schema, **self.current(),
                **({'execution_scopes_sha256': digest(canonical(self.db.execute('SELECT * FROM operation_execution_scopes ORDER BY operation_id').fetchall()))} if self._dispatch_identities else {}),
                'grants': [{'grant_id': row[0], 'generation': row[1], 'status': row[2], 'token_sha256': row[3]}
                           for row in self.db.execute('SELECT grant_id,generation,status,token_sha256 FROM authority_grants ORDER BY generation')],
                'operation_authority_sha256': digest(canonical(self.db.execute('SELECT * FROM operation_authority ORDER BY operation_id').fetchall())),
                'transitions_sha256': digest(canonical(self.db.execute('SELECT * FROM authority_transitions ORDER BY sequence').fetchall())),
                'transition_count': self.db.execute('SELECT count(*) FROM authority_transitions').fetchone()[0],
                'operation_count': self.db.execute('SELECT count(*) FROM operation_authority').fetchone()[0],
                'resource_tables_sha256': {table: digest(canonical(self.db.execute('SELECT * FROM '+table+' ORDER BY '+key).fetchall()))
                    for table, key in [('operation_kinds','operation_id'), ('operation_resource_states','operation_id'),
                                       ('rejection_resource_states','rejection_sequence')]}}
