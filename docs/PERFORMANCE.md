# Performance validation

The scale datasets and measurements below are synthetic acceptance checks. They do not establish a hardware-independent capacity guarantee, real-device parser compatibility, or performance on unmeasured workloads. Configuration complexity, shared segments, override count, source history, browser hardware, and layout algorithm all affect cost.

## Reproduce the connected topology

```sh
.venv/bin/python scripts/generate_scale_samples.py --devices 100 --output /tmp/iptopo-scale-100.zip
.venv/bin/python scripts/generate_scale_samples.py --devices 1000 --output /tmp/iptopo-scale-1000.zip
.venv/bin/python scripts/generate_scale_samples.py --devices 2000 --output /tmp/iptopo-scale-2000.zip
```

The generator uses deterministic synthetic Cisco IOS configurations. Every router has two ring interfaces, one chord to a router in the opposite half of the ring, and one excluded loopback. The routed topology is connected, with three distinct peers per router. One quarter of logical relations additionally carry IPv6. IPv4 /31 and IPv6 /127 addresses are calculated directly; the generator never enumerates network hosts. All contexts start in the explicit project default-domain assumption so the ZIP renders immediately after import.

Each ZIP contains configurations only. A sibling `.manifest.json` records the expected counts, input bytes, and shape. Names, timestamps, and contents are deterministic; no real configuration or credentials are included.

| Devices | Interfaces | IPv4 addresses | IPv6 addresses | Logical relations | Prefix segments | Config bytes | ZIP bytes |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 400 | 400 | 76 | 150 | 188 | 48,371 | 29,893 |
| 1,000 | 4,000 | 4,000 | 750 | 1,500 | 1,875 | 485,363 | 300,908 |
| 2,000 | 8,000 | 8,000 | 1,500 | 3,000 | 3,750 | 973,829 | 602,242 |

These inputs stay within the default 2,500-file / 64 MiB aggregate upload limits. Device count alone is not a memory or rendering limit.

## Real HTTP benchmark

```sh
.venv/bin/python scripts/benchmark_scale_api.py --devices 100 1000 2000 --output /tmp/iptopo-api-scale-results.json
```

For each size, the benchmark starts a new Uvicorn server bound to an inherited ephemeral socket and a new temporary data directory. It submits the ZIP through the multipart HTTP import route, polls the actual spawned import worker, then measures the first GET, a repeated GET, explicit inference, and JSON / CSV / draw.io exports. It verifies device/interface/relation counts and exported draw.io node/edge counts. It does not use the developer server or its database.

Timings include HTTP transfer and decoding on the same host. `decoded_bytes` counts the complete decoded response; `wire_content_length` records a response Content-Length when available. Memory is sampled every approximately 50 ms using process RSS for the server and its descendants. The sum can double-count shared pages and can miss brief peaks; it is a sampled observation, not allocation accounting. The client process is excluded. Every scale starts a fresh server, avoiding cross-scale cache reuse.

Measured on 2026-10-24: macOS / arm64, hardware model `Mac18,5`, CPU reported by the host as `Apple M6`, 12 logical CPUs, 16 GiB RAM, Python 3.12.14. This is one local run per size while the development session was active and browser QA was running concurrently on a separate server. The complete machine-readable observations are in [performance-results-2026-10-24.json](performance-results-2026-10-24.json).

| Devices | Import wall time | First GET | Cached GET | Save all positions | Inference request | JSON export | CSV export | draw.io export |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 0.546 s | 25.8 ms | 8.4 ms | 3.0 ms | 8.0 ms | 22.5 ms | 22.7 ms | 14.4 ms |
| 1,000 | 4.298 s | 296.3 ms | 68.4 ms | 4.2 ms | 69.9 ms | 154.6 ms | 250.6 ms | 175.4 ms |
| 2,000 | 8.279 s | 634.9 ms | 139.4 ms | 6.2 ms | 138.7 ms | 335.1 ms | 564.4 ms | 441.3 ms |

| Devices | Full GET decoded bytes | Full GET wire bytes (gzip) | Sampled server peak RSS | Sampled server + worker tree peak RSS |
| ---: | ---: | ---: | ---: | ---: |
| 100 | 1,088,276 | 159,125 | 94.72 MiB | 157.25 MiB |
| 1,000 | 10,864,440 | 1,643,946 | 293.95 MiB | 311.73 MiB |
| 2,000 | 21,750,290 | 3,289,799 | 543.28 MiB | 560.80 MiB |

The inference request reuses the derived-model cache when its snapshot, overrides, and address-domain settings have not changed. That column measures the endpoint request, not a forced cold inference pass. The first GET includes model derivation and response construction after import. Position saves use the summary response and submit a position for every device; no complete topology is returned by that operation. The subsequent GET after saving positions took 7.6 / 68.7 / 138.4 ms respectively, confirming that geometry changes retain the derived-model cache.

The complete structured project remains larger than the compact canvas graph. At 2,000 devices the decoded GET still contains about 21.75 MB of device/interface/address/evidence details; gzip reduces transfer to about 3.29 MB but does not remove browser JSON parsing or memory costs. The measurements cover one imported snapshot per project and do not validate long-history or multiple active-project memory use.

Separate browser measurements appear below. These backend results must not be described as browser frame-rate or interactive layout guarantees.

## Regression coverage and scalability changes

- `backend/tests/test_scale_api.py` imports 1,000 devices through the API and checks 4,000 interfaces, 4,750 addresses, 1,500 logical relations, complete graph connectivity, and degree three. Separating the two ring halves into explicit address domains removes all 500 cross-domain chords and the two ring boundaries, leaving exactly 998 relations.
- `backend/tests/test_worker_process.py` runs a real spawned worker, cancels after partial parsing, stops/restarts the server during another import, and verifies that partial facts never replace the previous snapshot.
- Shared-segment links carry only their own endpoint evidence and a group-membership digest. The segment stores the complete group evidence once. The digest preserves stale-review detection when another member changes. The 1,000-member regression bounds serialized payload growth against 100 members, preventing the former quadratic duplication.
- The API caches the derived model for at most two projects using snapshot/override/domain signatures, sends a compact graph for the canvas, and keeps full evidence in the inspectable snapshot. Snapshot/job lists select metadata instead of loading every full model. JSON responses use gzip for transport.
- Import workers batch progress writes rather than serializing the growing result list after each file. Cancellation remains cooperative at a file boundary, with periodic checks and no partial snapshot commit.
- Descriptive corrections to device metadata and interface labels/descriptions validate and update an individual entity without repeating full inference or copying the complete topology for every label. Changes to addresses, domains, membership, or configuration state still recompute their affected topology.
- Dual-stack mismatch diagnostics index links by endpoint instead of scanning every link for every mismatched endpoint.
- Export-filter regressions cover vendor, address-family, domain, prefix/interface/hostname search, combined filters, matching isolated devices, and shared segments with any visible member.

The original single-interface `/31` pairs in `scripts/benchmark.py` remain a small component benchmark. They must not be cited as evidence for a connected 1,000-device rendered topology; use the connected dataset and end-to-end measurements above.

## Real Chrome browser validation

The same connected ZIPs were imported into disposable projects on the actual local service. Chrome ran headlessly at 1536 × 1024 using Playwright; no API routes were mocked. `ready_ms` measures navigation start until Cytoscape contains all expected nodes plus two animation frames. It includes local HTML/JavaScript/API loading and first-render preparation, excludes ZIP parsing, and uses existing saved project views with a warmed derived-model API cache. It is not a cold-import latency or frame-rate guarantee. Search timing runs from filling the hostname field until only the target node remains.

| Devices | Navigation to graph ready | Hostname search | Rendered device rows | File / context rows | Longest observed main-thread task |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 169 ms | 15 ms | 100 | 100 / 100 | none ≥ 50 ms |
| 1,000 | 319 ms | 31 ms | 20 | 18 / 18 | 93 ms |
| 2,000 | 555 ms | 29 ms | 20 | 18 / 18 | 179 ms |

These are single-run observations in an active local development session. The 179 ms task shows that large-graph work is not completely free of main-thread stalls. File/context tab observations in the raw report include a fixed 50 ms observation wait; they must not be interpreted as CPU execution timings. Full, sanitized results: [browser-performance-results-2026-10-24.json](browser-performance-results-2026-10-24.json).

Actual browser checks also verified:

- Importing the 1,000-device ZIP directly into an empty project switches the renderer to pixel ratio 1, hidden viewport edges and viewport textures; filtering to one device preserves that renderer instance.
- Search, vendor/family/domain filters and one-hop focus operate on indexed facts. Focus/reset preserves all original coordinates. When retained global geometry puts several focused nodes on one line, run a local fCoSE layout (up to 150 target nodes) or layered layout to improve label separation. ELK radial produced near-overlapping neighbors in one four-node star during visual QA; use another layout or manual placement in that case.
- Initial missing-node placement uses connected components above 150 nodes. Adding a 1,001st device preserves all 1,000 existing coordinates; only the new node receives a location.
- Device/file/context lists use bounded rows above 150 entries; the address-domain mapping dialog searches and paginates 50 entries.
- A view-only save produces zero Cytoscape data-update events. Position saves keep snapshot/topology object references intact.
- fCoSE rejects more than 150 target nodes; ELK runs in a cancellable Web Worker. Changing filters or switching views cancels stale layouts. A delayed result did not overwrite the newly selected view's pinned coordinates.
- Switching between two differently positioned pinned views and then an unpinned view restores each exact position and lock state. Full-graph layout undo after filtering restores hidden nodes as well.
- The 2,000-device PNG was downloaded and validated: 913,975 bytes, 4096 × 2403 pixels. PNG uses asynchronous blob output with a 4096 × 4096 bound. Very large full-network raster labels remain small; export a focused view or editable draw.io for detailed work.
- The four-vendor demo renders vendor symbols with names below and a separate legend. A 390 × 844 device-detail view has no horizontal document overflow. No page errors were observed during these runs.

To reproduce, install Playwright in a separate test tooling directory and make it available through `NODE_PATH`, with Chrome installed. Generate/import the ZIPs above into disposable projects, then create a JSON file such as `[{"id":"your-test-project-id","n":1000}]` and run:

```sh
NODE_PATH=/path/to/browser-test-tools/node_modules node frontend/tests/browser-scale.cjs /tmp/projects.json /tmp/iptopo-browser-results
```

The script changes filters, positions and views, so use disposable test projects. `npm test` separately runs six deterministic model checks for 100/1,000/2,000-node placement, incremental import, disconnected graphs and filter/focus membership. Browser testing is not run automatically by CI.
