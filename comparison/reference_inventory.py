"""Independent ordinary-Python implementation of the same inventory application.

Business logic uses sqlite3 directly; it does not import MAIRG. Schema assets
are shared inputs. Waitress is the common HTTP server in the comparison harness.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class Failure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _typed(value: Any, kind: type) -> Any:
    if type(value) is not kind:
        names = {str: 'str', int: 'int'}
        raise Failure('type_error', f'Expected {names[kind]}, got {type(value).__name__}')
    return value


class Inventory:
    def __init__(self, source: Path, state: Path):
        self.source, self.state = Path(source), Path(state)
        self.manifest = json.loads((self.source / 'mairg.json').read_text())
        self.path = self.state / 'db' / self.manifest['database']
        self.schema = json.loads((self.source / 'schema.json').read_text())

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, isolation_level=None, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA synchronous=FULL')
        connection.execute('PRAGMA busy_timeout=2000')
        return connection

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            yield connection
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def rows(connection, sql, parameters=()):
        return [dict(row) for row in connection.execute(sql, parameters).fetchall()]

    def verify(self, connection):
        if connection.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise Failure('unsupported_schema', 'Database schema version must be 1')
        actual = self.rows(connection, "SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        expected = [{'name': name, 'sql': self.schema[index]}
                    for index, name in enumerate(('items', 'reservations'))]
        if actual != expected:
            raise Failure('unsupported_schema', 'Database tables do not match canonical schema')
        return {'ready': True, 'version': 1}

    def init(self):
        with self.transaction() as connection:
            if connection.execute('PRAGMA user_version').fetchone()[0] == 0:
                if self.rows(connection, "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                    raise Failure('unsupported_schema', 'Initial database must have no application tables')
                statements = json.loads((self.source / 'migrations/001.json').read_text())
                for statement in statements:
                    connection.execute(statement)
            return self.verify(connection)

    def startup(self):
        connection = self.connect()
        try:
            return self.verify(connection)
        finally:
            connection.close()

    @staticmethod
    def name_stock(name, stock):
        _typed(name, str)
        _typed(stock, int)
        if not name.strip():
            raise Failure('invalid_name', 'Name must contain non-whitespace text')
        if stock < 0:
            raise Failure('invalid_stock', 'Stock must be nonnegative')

    @staticmethod
    def first(rows):
        if not rows:
            raise Failure('not_found', 'Item does not exist')
        return rows[0]

    def list_items(self):
        connection = self.connect()
        try:
            return self.rows(connection, 'SELECT id,name,stock FROM items ORDER BY id')
        finally:
            connection.close()

    def list_reservations(self):
        connection = self.connect()
        try:
            return self.rows(connection, 'SELECT id,item_id,quantity,note FROM reservations ORDER BY id')
        finally:
            connection.close()

    def get_item(self, item_id):
        _typed(item_id, int)
        connection = self.connect()
        try:
            return self.first(self.rows(connection, 'SELECT id,name,stock FROM items WHERE id=?', [item_id]))
        finally:
            connection.close()

    def create_item(self, name, stock):
        self.name_stock(name, stock)
        with self.transaction() as connection:
            return self.first(self.rows(connection, 'INSERT INTO items(name,stock) VALUES(?,?) RETURNING id,name,stock', [name, stock]))

    def update_item(self, item_id, name, stock):
        _typed(item_id, int)
        self.name_stock(name, stock)
        with self.transaction() as connection:
            return self.first(self.rows(connection, 'UPDATE items SET name=?,stock=? WHERE id=? RETURNING id,name,stock', [name, stock, item_id]))

    def delete_item(self, item_id):
        _typed(item_id, int)
        with self.transaction() as connection:
            return self.first(self.rows(connection, 'DELETE FROM items WHERE id=? RETURNING id,name,stock', [item_id]))

    def reserve(self, item_id, quantity, note, *, fail=False):
        _typed(item_id, int)
        _typed(quantity, int)
        _typed(note, str)
        if quantity <= 0:
            raise Failure('invalid_quantity', 'Quantity must be positive')
        with self.transaction() as connection:
            rows = self.rows(connection, 'UPDATE items SET stock=stock-? WHERE id=? AND stock>=? RETURNING id,stock', [quantity, item_id, quantity])
            if not rows:
                raise Failure('insufficient_stock', 'Item is missing or has insufficient stock')
            row = self.rows(connection, 'INSERT INTO reservations(item_id,quantity,note) VALUES(?,?,?) RETURNING id,item_id,quantity,note', [item_id, quantity, note])[0]
            if fail:
                raise Failure('deliberate_failure', 'Rollback proof')
            return row

    def call(self, export, arguments):
        if export == 'fail_reservation':
            return self.reserve(*arguments, fail=True)
        methods = {'init': self.init, 'startup': self.startup, 'list_items': self.list_items,
                   'list_reservations': self.list_reservations, 'get_item': self.get_item,
                   'create_item': self.create_item, 'update_item': self.update_item,
                   'delete_item': self.delete_item, 'reserve': self.reserve}
        try:
            return methods[export](*arguments)
        except sqlite3.Error as error:
            raise Failure('sql_error', str(error)) from error

    def wsgi(self):
        self.startup()

        def application(environ, start_response):
            status = 200
            try:
                method, path = environ['REQUEST_METHOD'], environ['PATH_INFO']
                size = int(environ.get('CONTENT_LENGTH') or 0)
                if size > 1024 * 1024:
                    raise Failure('invalid_request', 'request body exceeds limit')
                body = json.loads(environ['wsgi.input'].read(size)) if size else None
                if path == '/items' and method == 'GET':
                    result = self.list_items()
                elif path == '/items' and method == 'POST':
                    result = self.call('create_item', [body['name'], body['stock']])
                elif path == '/reservations' and method == 'GET':
                    result = self.list_reservations()
                elif path == '/reservations' and method == 'POST':
                    result = self.call('reserve', [body['item_id'], body['quantity'], body['note']])
                elif (match := re.fullmatch(r'/items/(-?[0-9]+)', path)):
                    item_id = int(match[1])
                    if method == 'GET':
                        result = self.get_item(item_id)
                    elif method == 'PUT':
                        result = self.call('update_item', [item_id, body['name'], body['stock']])
                    elif method == 'DELETE':
                        result = self.call('delete_item', [item_id])
                    else:
                        status, result = 405, {'error': {'code': 'method_not_allowed'}}
                else:
                    status, result = 404, {'error': {'code': 'not_found'}}
            except Failure as error:
                status = 404 if error.code == 'not_found' else 409 if error.code == 'insufficient_stock' else 400
                result = {'error': {'code': error.code, 'message': error.message}}
            except sqlite3.Error as error:
                status, result = 400, {'error': {'code': 'sql_error', 'message': str(error)}}
            except (KeyError, TypeError, ValueError):
                status, result = 400, {'error': {'code': 'invalid_request'}}
            data = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
            start_response(f'{status} Response', [('Content-Type', 'application/json'), ('Content-Length', str(len(data)))])
            return [data]
        return application
