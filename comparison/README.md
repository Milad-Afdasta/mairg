# Whole-application comparison

Run from the repository root after installing MAIRG:

```sh
.venv/bin/python comparison/run.py --requests 60 --output /tmp/mairg-poc-comparison.json
```

`reference_inventory.py` is an ordinary Python implementation of the graph inventory application's business behavior. `run.py` supplies the common process/server/measurement harness. It asserts exact HTTP status and decoded JSON for 15 cases, transaction rollback, and retained data across restart before timing a 31-item read endpoint. No output normalization is used.

Both sides use separate fresh SQLite state, the same schema, explicit transactions and durability settings, and Waitress with four threads. The report stores all sequential and fresh-process samples, environment details, source hashes, and Git commit. Temporary state and owned processes are cleaned up. Failed assertions produce no successful comparison report.

This compares complete implementations on tested behavior, not every possible input or every language capability. The direct Python reference has no generic graph-operation budgets or metadata system. Current MAIRG has measurable overhead; AI productivity and token efficiency have not been tested. See [results, method, and limitations](../docs/COMPARISON.md).
