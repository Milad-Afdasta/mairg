# Inventory and reservations

This editable graph application stores inventory and reservations in SQLite. Application rules, schema initialization, version checks, request extraction, response statuses, and transaction boundaries are in `program.json`; Python supplies generic SQL and HTTP transport only. `../generate_inventory.py` is an optional JSON authoring helper, not an application runtime.

From the repository root:

```sh
mairg check examples/inventory
mairg test examples/inventory
mairg run examples/inventory init --args '[]' --state-dir /tmp/inventory-state
mairg run examples/inventory create_item --args '["Coffee",10]' --state-dir /tmp/inventory-state
mairg serve examples/inventory --host 127.0.0.1 --port 8000 --state-dir /tmp/inventory-state
```

`GET /items` lists items. `POST /items` accepts `{"name":"Coffee","stock":10}`. `GET`, `PUT`, and `DELETE /items/{id}` read, replace, and delete an item; PUT takes the same name/stock body. `GET /reservations` lists reservations. `POST /reservations` accepts `{"item_id":1,"quantity":2,"note":"Customer order"}`. Success returns JSON directly with status 200. Missing items return 404, unavailable stock returns 409, and application validation failures return 400 with an error record. SQL foreign-key rejection prevents deleting an item with reservations.

CLI exports are `init`, `startup`, `list_items`, `create_item(name,stock)`, `get_item(id)`, `update_item(id,name,stock)`, `delete_item(id)`, `reserve(item_id,quantity,note)`, and `list_reservations`. `fail_reservation` deliberately fails after both reservation writes inside a transaction to prove rollback. Names require non-whitespace text, stock must be nonnegative, and quantity must be positive. Strings containing quotes remain SQL parameters.

`schema.json` is the canonical initial SQL-statement array. `migrations/001.json` is its initial migration, with identical statements and real SQLite `user_version=1`. `init` applies the migration atomically only to an empty, version-zero database. Existing version-one data is preserved. Startup verifies the version and exact table schema before the HTTP socket becomes ready; unsupported or malformed schema rejects. There is no invented historical version-two migration and no hidden framework initializer.

State is explicit: the database is `STATE_DIR/db/inventory.sqlite3`; generic file state would be under `STATE_DIR/files`. Tests use new isolated state per fixture and never a user's database.

```sh
mairg build examples/inventory --output /tmp/inventory-bundle
cd /tmp
python /tmp/inventory-bundle/run.py list_items --args '[]' --state-dir /tmp/inventory-state
python /tmp/inventory-bundle/run.py serve --host 127.0.0.1 --port 8000 --state-dir /tmp/inventory-state
```

The generated directory includes the compiled application, runtime, assets, and Waitress 3.0.2. It needs Python 3.11+ and no installed MAIRG package. Keep state external to the immutable bundle. This is a practical proof of concept, not a production deployment claim.
