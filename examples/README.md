# Telemetry analyzer application

```sh
python3 -m mairg run examples/telemetry.mairg.json examples/telemetry.jsonl
python3 -m mairg run examples/telemetry.mairg.json examples/telemetry.jsonl --engine reference
python3 -m mairg plan examples/telemetry.mairg.json
```

The sample's expected output is in `expected.json`. The host only parses JSONL and transports results. The graph owns all seven application formulas: request count, server-error count (`status >= 500`), slow-request count (`latency_us >= 1000`), total bytes, server-error bytes, maximum latency, and the modulo-2^64 sum of `latency_us * 3 + bytes`.

Fields are `status`, `latency_us`, `bytes` (strict unsigned 64-bit integers), and `cached` (strict boolean). Every row must contain exactly those fields. Names define the external schema; opaque node IDs define dependency edges. The deliberately reversed declaration order, duplicated threshold expression, and unused valid nodes demonstrate compiler-owned ordering, constant folding, shared-expression elimination, and dead-node removal.

Generate a larger synthetic input:

```sh
python3 examples/generate.py --rows 10000 --seed 17 --output /tmp/mairg-events.jsonl
python3 -m mairg run examples/telemetry.mairg.json /tmp/mairg-events.jsonl
```

`simple.mairg.json` sums bytes. `selective.mairg.json` counts uncached server errors and sums their bytes. Their independent C++ implementations are included in the reproducible benchmark.
