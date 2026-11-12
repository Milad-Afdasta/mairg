# Practical POC comparison

The POC builds useful applications, but this implementation has **no demonstrated speed advantage**. The measured inventory endpoint was about **4.06 times slower than direct Python**. This is a result for the current Python-hosted implementation and workload, not a limit on graph languages.

Measured 2026-11-12 at source commit [`003d949`](https://github.com/Milad-Afdasta/mairg/commit/003d94949a5fbe561fecc092aadd52c1bd370559). [Raw results and individual samples](results/poc-macos-arm64.json) include source hashes and environment metadata. Later CI-interpreter and documentation corrections do not change the measured runtime or application.

## Results

| Measurement | MAIRG | Direct Python |
|---|---:|---:|
| HTTP median | 2.174 ms | 0.535 ms |
| HTTP empirical p95 | 2.471 ms | 0.713 ms |
| HTTP throughput, four clients | 435 requests/s | 2,551 requests/s |
| Fresh-process list invocation median | 131.45 ms | 76.29 ms |
| Process start to readiness | 120.97 ms | 72.52 ms |
| Server peak RSS after requests | 33.38 MiB | 29.48 MiB |

Both implementations matched exactly on 15 HTTP status/decoded-JSON cases covering CRUD, validation, Unicode/quotes, insufficient stock, and a foreign-key failure. A deliberately failed multi-write reservation left both tables unchanged. Committed data checks passed across service restart. Results were compared without normalization.

The MAIRG inventory directory build took a median **179.78 ms** over three fresh output directories. It contains **276,414 bytes in 28 files**, including the manifest, runtime, Waitress, licenses and assets, and excluding the Python interpreter and mutable database. The CLI's payload counter excludes its own manifest and reports273,839bytes. [Build samples and method](results/poc-build-macos-arm64.json) are recorded at bbdfc76. Build time includes interpreter launch, validation/compilation, copying, hashing, smoke execution and publication. No equivalent standalone Python packaging measurement was made; the reference runs directly from Python source with Waitress installed, so a size/build advantage is not claimed.

## What was measured

`comparison/reference_inventory.py` implements the inventory business logic directly in ordinary Python, without MAIRG imports. Both applications use the same canonical schema asset, parameterized SQL, SQLite foreign keys, FULL synchronous mode, a two-second busy timeout, explicit transaction boundaries, and Waitress 3.0.2 with four threads. Each gets its own fresh state directory and loopback port.

After functional checks, each database contains 31 items. Five warm-up GET requests precede 60 sequential `GET /items` samples per implementation, with alternating backend order. Timings include HTTP/client work, server dispatch, application execution, SQLite reading, JSON serialization, and client decoding. The throughput phase sends 60 requests with four clients to each server. Each result is checked. Three additional processes measure list invocation including process startup; these are CLI-equivalent application calls through the common harness, not measurements of CLI argument parsing. Server readiness and RSS are from the restarted server processes.

Environment: macOS 15.6.1 arm64, Python 3.14.7, SQLite 3.53.4. RSS is the server process's own peak, not cumulative child-process memory. Empirical p95 uses sorted sample index `min(n-1, floor(0.95*n))`. The small sample sizes and one machine do not establish confidence intervals or general performance rankings. OS caches were not flushed; a fresh process is not a cold machine. Concurrent phases are sequential by backend and can be affected by host load.

The Python reference shares tested business/storage/server behavior. It does not implement every graph type check, operation budget, metadata capability, or malformed-input diagnostic. This is a practical whole-application overhead comparison, not a proof of equivalence for every input or a controlled isolation of one language feature. It times a read endpoint; write durability, saturation, network deployment, and larger data sets need separate studies. No optimization was introduced to improve this score.

## What this proves

Two different applications can express their business logic in reusable graphs using the public project tools: an inventory/reservation HTTP service and an independently authored CSV-to-Markdown release-note generator. Both produce standalone directories that work outside the checkout. Internal node names, declaration order, classes, handwritten loops, and manual schedules are unnecessary for these applications.

AI generation success, token cost, debugging efficiency, and maintenance gains remain **unmeasured hypotheses**. We did not conduct controlled authoring trials, and source byte counts would not substitute for them. The POC demonstrates practical programmability and identifies implementation overhead; it does not establish that removing human-oriented conventions makes programs faster or cheaper to generate.

## Reproduce

From a checkout with Python 3.11+:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python comparison/run.py --requests 60 --output /tmp/mairg-poc-comparison.json
```

The harness creates and removes only its own temporary state, uses the active interpreter, asserts correctness before reporting timings, and shuts down its owned server processes. Hardware/runtime differences will change the results. See the [harness guide](../comparison/README.md) and [validation record](VALIDATION.md).
