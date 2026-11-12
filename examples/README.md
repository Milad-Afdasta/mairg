# Practical applications

- [Inventory and reservations](inventory/README.md): persistent graph-defined HTTP API and CLI, custom SQLite schema, atomic reservations, explicit initialization and startup checks, rollback, concurrency, and restart fixtures.

Run `mairg check examples/inventory`, `mairg test examples/inventory`, then follow the application's README. `generate_inventory.py` emits the editable JSON source as an authoring convenience; no Python business callback is loaded by the application.
