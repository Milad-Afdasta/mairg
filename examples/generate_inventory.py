"""Authoring helper: emits editable JSON graphs, never runtime callbacks."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent / "inventory"
ITEMS = "CREATE TABLE items(id INTEGER PRIMARY KEY,name TEXT NOT NULL CHECK(length(trim(name))>0),stock INTEGER NOT NULL CHECK(stock>=0))"
RESERVATIONS = "CREATE TABLE reservations(id INTEGER PRIMARY KEY,item_id INTEGER NOT NULL REFERENCES items(id),quantity INTEGER NOT NULL CHECK(quantity>0),note TEXT NOT NULL)"
SCHEMA = [ITEMS, RESERVATIONS, "PRAGMA user_version=1"]
FUNCTIONS: list[dict[str, Any]] = []
EXPORTS: dict[str, int] = {}


class Function:
    def __init__(self, params: list[str], result: str, export: str | None = None):
        self.doc: dict[str, Any] = {"id": len(FUNCTIONS), "params": params, "result": result, "nodes": [], "return": 0}
        FUNCTIONS.append(self.doc)
        if export is not None:
            EXPORTS[export] = self.doc["id"]
        self.params = [self.node("param", index=index) for index in range(len(params))]

    @property
    def id(self) -> int:
        return int(self.doc["id"])

    def node(self, op: str, *args: int, **extra: Any) -> int:
        after = extra.pop("after", None)
        ident = len(self.doc["nodes"])
        value = {"id": ident, "op": op, "args": list(args), **extra}
        if after:
            value["after"] = after
        self.doc["nodes"].append(value)
        return ident

    def const(self, value: Any) -> int:
        return self.node("const", value=value)

    def field(self, record: int, key: str) -> int:
        return self.node("field", record, self.const(key))

    def call(self, target: Function, *args: int, after: list[int] | None = None) -> int:
        return self.node("call", *args, target=target.id, after=after)

    def host(self, binding: str, sql: str, params: list[int], after: list[int] | None = None) -> int:
        return self.node("host", self.const(sql), self.node("list", *params), binding=binding, after=after)

    def done(self, result: int) -> Function:
        self.doc["return"] = result
        return self


true = Function(["str", "str"], "bool")
true.done(true.const(True))
false = Function(["str", "str"], "bool")
false.done(false.node("fail", *false.params))
require = Function(["bool", "str", "str"], "bool")
require.done(require.node("choose", *require.params, then=true.id, **{"else": false.id}))


def guard(f: Function, condition: int, code: str, message: str) -> int:
    return f.call(require, condition, f.const(code), f.const(message))


def valid_name(f: Function, value: int) -> int:
    return guard(f, f.node("gt", f.node("length", f.node("text.trim", value)), f.const(0)),
                 "invalid_name", "Name must contain non-whitespace text")


def valid_stock(f: Function, value: int) -> int:
    return guard(f, f.node("ge", value, f.const(0)), "invalid_stock", "Stock must be nonnegative")


ready = Function([], "record")
ready.done(ready.const({"ready": True, "version": 1}))
startup = Function([], "record", "startup")
version = startup.host("query", "PRAGMA user_version", [])
version_ok = guard(startup, startup.node("eq", version, startup.const([{"user_version": 1}])),
                   "unsupported_schema", "Database schema version must be 1")
schema = startup.host("query", "SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name", [], after=[version_ok])
canonical = startup.node("json.parse", startup.node("asset.text", startup.const("schema.json")))
expected = startup.node("list", *[
    startup.node("record", startup.const(name), startup.node("index", canonical, startup.const(index)), keys=["name", "sql"])
    for index, name in enumerate(("items", "reservations"))
])
schema_ok = guard(startup, startup.node("eq", schema, expected),
                  "unsupported_schema", "Database tables do not match canonical schema")
startup.done(startup.call(ready, after=[schema_ok]))

migration_step = Function(["any", "str"], "any")
migration_step.done(migration_step.node("host", migration_step.params[1], migration_step.const([]), binding="execute"))
migrate = Function([], "record")
empty = migrate.host("query", "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'", [])
empty_ok = guard(migrate, migrate.node("eq", empty, migrate.const([])), "unsupported_schema", "Initial database must have no application tables")
asset = migrate.node("asset.text", migrate.const("migrations/001.json"))
statements = migrate.node("json.parse", asset)
fold = migrate.node("fold", statements, migrate.const(None), target=migration_step.id, after=[empty_ok])
migrate.done(migrate.call(startup, after=[fold]))
init_region = Function([], "record")
ver = init_region.host("query", "PRAGMA user_version", [])
init_region.done(init_region.node("choose", init_region.node("eq", ver, init_region.const([{"user_version": 0}])),
                                  then=migrate.id, **{"else": startup.id}))
init = Function([], "record", "init")
init.done(init.node("transaction", target=init_region.id))

list_items = Function([], "list", "list_items")
list_items.done(list_items.host("query", "SELECT id,name,stock FROM items ORDER BY id", []))
list_reservations = Function([], "list", "list_reservations")
list_reservations.done(list_reservations.host("query", "SELECT id,item_id,quantity,note FROM reservations ORDER BY id", []))
first_present = Function(["list"], "record")
first_present.done(first_present.node("index", first_present.params[0], first_present.const(0)))
not_found = Function(["list"], "record")
not_found.done(not_found.node("fail", not_found.const("not_found"), not_found.const("Item does not exist")))
first = Function(["list"], "record")
first.done(first.node("choose", first.node("gt", first.node("length", first.params[0]), first.const(0)), first.params[0],
                      then=first_present.id, **{"else": not_found.id}))
get_item = Function(["int"], "record", "get_item")
get_item.done(get_item.call(first, get_item.host("query", "SELECT id,name,stock FROM items WHERE id=?", get_item.params)))


def mutation(export: str, params: list[str], sql: str, validations: list[tuple[int, str]]) -> Function:
    region = Function(params, "record")
    checks = [valid_name(region, region.params[index]) if kind == "name" else valid_stock(region, region.params[index])
              for index, kind in validations]
    result = region.host("execute", sql, region.params, after=checks)
    region.done(region.call(first, region.field(result, "rows")))
    function = Function(params, "record", export)
    return function.done(function.node("transaction", *function.params, target=region.id))


create_item = mutation("create_item", ["str", "int"], "INSERT INTO items(name,stock) VALUES(?,?) RETURNING id,name,stock", [(0, "name"), (1, "stock")])
# Parameter order stays visible in the graph rather than being rewritten by the host.
update_region = Function(["int", "str", "int"], "record")
checks = [valid_name(update_region, update_region.params[1]), valid_stock(update_region, update_region.params[2])]
updated = update_region.host("execute", "UPDATE items SET name=?,stock=? WHERE id=? RETURNING id,name,stock",
                             [update_region.params[1], update_region.params[2], update_region.params[0]], after=checks)
update_region.done(update_region.call(first, update_region.field(updated, "rows")))
update_item = Function(["int", "str", "int"], "record", "update_item")
update_item.done(update_item.node("transaction", *update_item.params, target=update_region.id))
delete_item = mutation("delete_item", ["int"], "DELETE FROM items WHERE id=? RETURNING id,name,stock", [])

insert_reservation = Function(["int", "int", "str"], "record")
inserted = insert_reservation.host("execute", "INSERT INTO reservations(item_id,quantity,note) VALUES(?,?,?) RETURNING id,item_id,quantity,note", insert_reservation.params)
insert_reservation.done(insert_reservation.node("index", insert_reservation.field(inserted, "rows"), insert_reservation.const(0)))
no_stock = Function(["int", "int", "str"], "record")
no_stock.done(no_stock.node("fail", no_stock.const("insufficient_stock"), no_stock.const("Item is missing or has insufficient stock")))
reserve_region = Function(["int", "int", "str"], "record")
quantity_ok = guard(reserve_region, reserve_region.node("gt", reserve_region.params[1], reserve_region.const(0)),
                    "invalid_quantity", "Quantity must be positive")
decrement = reserve_region.host("execute", "UPDATE items SET stock=stock-? WHERE id=? AND stock>=? RETURNING id,stock",
                                [reserve_region.params[1], reserve_region.params[0], reserve_region.params[1]], after=[quantity_ok])
reserve_region.done(reserve_region.node("choose", reserve_region.node("gt", reserve_region.node("length", reserve_region.field(decrement, "rows")), reserve_region.const(0)),
                                        *reserve_region.params, then=insert_reservation.id, **{"else": no_stock.id}))
reserve = Function(["int", "int", "str"], "record", "reserve")
reserve.done(reserve.node("transaction", *reserve.params, target=reserve_region.id))
fail_region = Function(["int", "int", "str"], "record")
reserved = fail_region.call(reserve_region, *fail_region.params)
fail_region.done(fail_region.node("fail", fail_region.const("deliberate_failure"), fail_region.const("Rollback proof"), after=[reserved]))
fail_reservation = Function(["int", "int", "str"], "record", "fail_reservation")
fail_reservation.done(fail_reservation.node("transaction", *fail_reservation.params, target=fail_region.id))

success_response = Function(["record"], "record")
success_response.done(success_response.node("record", success_response.const(200), success_response.const({}), success_response.field(success_response.params[0], "value"),
                                            keys=["status", "headers", "body"]))
status404 = Function(["str"], "int")
status404.done(status404.const(404))
status409 = Function(["str"], "int")
status409.done(status409.const(409))
status400 = Function(["str"], "int")
status400.done(status400.const(400))
other_error = Function(["str"], "int")
other_error.done(other_error.node("choose", other_error.node("eq", other_error.params[0], other_error.const("insufficient_stock")), *other_error.params,
                                  then=status409.id, **{"else": status400.id}))
error_response = Function(["record"], "record")
error = error_response.field(error_response.params[0], "error")
code = error_response.field(error, "code")
status = error_response.node("choose", error_response.node("eq", code, error_response.const("not_found")), code,
                             then=status404.id, **{"else": other_error.id})
error_response.done(error_response.node("record", status, error_response.const({}), error_response.node("record", error_response.node("record", code, error_response.field(error, "message"), keys=["code", "message"]), keys=["error"]), keys=["status", "headers", "body"]))
respond = Function(["record"], "record")
respond.done(respond.node("choose", respond.field(respond.params[0], "ok"), *respond.params,
                          then=success_response.id, **{"else": error_response.id}))


def route(export: str, target: Function, fields: list[str] | None = None, item_id: bool = False) -> None:
    action = Function(["record"], target.doc["result"])
    arguments: list[int] = []
    if item_id:
        arguments.append(action.node("parse_int", action.field(action.field(action.params[0], "params"), "id")))
    if fields:
        body = action.field(action.params[0], "body")
        arguments.extend(action.field(body, name) for name in fields)
    action.done(action.call(target, *arguments))
    handler = Function(["record"], "record", export)
    handler.done(handler.call(respond, handler.node("attempt", *handler.params, target=action.id)))


route("http_list_items", list_items)
route("http_create_item", create_item, ["name", "stock"])
route("http_get_item", get_item, item_id=True)
route("http_update_item", update_item, ["name", "stock"], item_id=True)
route("http_delete_item", delete_item, item_id=True)
route("http_list_reservations", list_reservations)
route("http_reserve", reserve, ["item_id", "quantity", "note"])

manifest = {"format": "mairg-project-1", "name": "inventory", "program": "program.json",
            "bindings": {"query": "sqlite.query", "execute": "sqlite.execute"}, "database": "inventory.sqlite3",
            "assets": ["schema.json", "migrations/001.json"], "startup": "startup", "tests": ["tests.json"],
            "routes": [{"method": method, "path": path, "export": export} for method, path, export in [
                ("GET", "/items", "http_list_items"), ("POST", "/items", "http_create_item"),
                ("GET", "/items/{id}", "http_get_item"), ("PUT", "/items/{id}", "http_update_item"),
                ("DELETE", "/items/{id}", "http_delete_item"),
                ("GET", "/reservations", "http_list_reservations"), ("POST", "/reservations", "http_reserve")]]}
fixture = {"files": {}, "cases": [
    {"export": "init", "args": [], "expect": {"ready": True, "version": 1}},
    {"export": "startup", "args": [], "expect": {"ready": True, "version": 1}},
    {"export": "create_item", "args": ["O'Reilly café", 5], "expect": {"id": 1, "name": "O'Reilly café", "stock": 5}},
    {"export": "create_item", "args": ["  ", 5], "error": "invalid_name"},
    {"export": "create_item", "args": ["bad", -1], "error": "invalid_stock"},
    {"export": "reserve", "args": [1, 2, "customer's order"], "expect": {"id": 1, "item_id": 1, "quantity": 2, "note": "customer's order"}},
    {"export": "fail_reservation", "args": [1, 1, "rollback"], "error": "deliberate_failure"},
    {"export": "get_item", "args": [1], "expect": {"id": 1, "name": "O'Reilly café", "stock": 3}},
    {"export": "reserve", "args": [1, 4, "no"], "error": "insufficient_stock"},
    {"export": "reserve", "args": [1, 0, "no"], "error": "invalid_quantity"},
    {"export": "list_reservations", "args": [], "expect": [{"id": 1, "item_id": 1, "quantity": 2, "note": "customer's order"}]},
    {"export": "get_item", "args": [99], "error": "not_found"},
    {"export": "create_item", "args": ["temporary", 0], "expect": {"id": 2, "name": "temporary", "stock": 0}},
    {"export": "update_item", "args": [2, "updated", 4], "expect": {"id": 2, "name": "updated", "stock": 4}},
    {"export": "delete_item", "args": [2], "expect": {"id": 2, "name": "updated", "stock": 4}},
    {"export": "list_items", "args": [], "expect": [{"id": 1, "name": "O'Reilly café", "stock": 3}]},
]}


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    for name, data in (("mairg.json", manifest), ("program.json", {"format": "mairg-app-1", "functions": FUNCTIONS, "exports": EXPORTS}),
                       ("schema.json", SCHEMA), ("migrations/001.json", SCHEMA), ("tests.json", fixture)):
        path = ROOT / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
