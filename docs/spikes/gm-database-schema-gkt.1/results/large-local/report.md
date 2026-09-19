## large scale, local mode

### The machine

| | |
| --- | --- |
| Label | Docker Desktop VM on Apple M1 Pro (2 vCPU, 7.7 GiB) |
| Platform | Linux-6.8.0-117-generic-aarch64-with-glibc2.41 |
| CPU count | 2 |
| RAM | 7.74 GiB |
| SHA-256 single thread | 460,438 ops/s |
| SHA-256 all cores | 830,836 ops/s |
| Memory read | 39,798 MB/s |
| Disk sequential write | 534 MB/s |
| Disk random read 4k | 484,114 IOPS |
| Python sort 1M floats | 0.1811 s |

PostgreSQL: `PostgreSQL 19beta3 on aarch64-unknown-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit`

Neo4j: `Neo4j Kernel 2026.07.1 community`

### Shortest path, median

| Case | d | `pf.find_path` p50 | `pf.find_path_deg` p50 | Neo4j p50 |
| --- | --- | --- | --- | --- |
| `d1-artist-artist` | 1 | 4.65 | 4.82 | **4.38** |
| `d1-artist-release` | 1 | 4.66 | 4.82 | **4.39** |
| `d2-artist-artist` | 2 | 13.2 | 18.6 | **7.41** |
| `d3-artist-artist` | 3 | 66.2 | 39.1 | **5.89** |
| `d4-artist-artist` | 4 | 55.5 | 74.1 | **4.24** |
| `d5-artist-artist` | 5 | 1,535 | 313.0 | **40.0** |
| `d6-artist-artist` | 6 | 1,551 | 2,711 | **36.8** |
| `unreachable` | — | 5.79 | 4.93 | **2.22** |

### Shortest path, p95 — the Verdict gate is 1000 ms

| Case | d | `pf.find_path` p95 | `pf.find_path_deg` p95 | Neo4j p95 |
| --- | --- | --- | --- | --- |
| `d1-artist-artist` | 1 | 4.81 | 5.40 | **5.76** |
| `d1-artist-release` | 1 | 4.94 | 5.32 | **6.67** |
| `d2-artist-artist` | 2 | 16.4 | 21.8 | **9.60** |
| `d3-artist-artist` | 3 | 71.7 | 40.6 | **8.10** |
| `d4-artist-artist` | 4 | 61.7 | 76.6 | **6.00** |
| `d5-artist-artist` | 5 | 1,586 | 328.3 | **44.8** |
| `d6-artist-artist` | 6 | 2,379 | 3,442 | **43.9** |
| `unreachable` | — | 6.43 | 5.22 | **2.71** |

### Work done

| Case | Vertices expanded | Touch probes | Seen rows | PostgreSQL buffers | Neo4j DbHits |
| --- | --- | --- | --- | --- | --- |
| `d1-artist-artist` | 0 | 1 | 2 | 321 | 13 |
| `d1-artist-release` | 0 | 1 | 2 | 244 | 72 |
| `d2-artist-artist` | 1 | 2 | 57 | 10,196 | 9,184 |
| `d3-artist-artist` | 2 | 3 | 6,393 | 57,396 | 6,657 |
| `d4-artist-artist` | 57 | 58 | 2,609 | 27,172 | 2,900 |
| `d5-artist-artist` | 11 | 12 | 148,597 | 1,665,900 | 150,184 |
| `d6-artist-artist` | 18 | 19 | 148,604 | 1,700,928 | 150,151 |
| `unreachable` | 2 | 2 | 57 | 629 | 66 |

### Depth cap against the miss

| Depth cap | `pf.find_path` p50 | `pf.find_path_deg` p50 | Neo4j p50 |
| --- | --- | --- | --- |
| 1 | 7.14 | 6.70 | **2.28** |
| 2 | 7.64 | 6.91 | **2.36** |
| 3 | 7.26 | 6.74 | **2.43** |
| 4 | 7.49 | 7.27 | **2.35** |
| 6 | 8.17 | 7.01 | **2.33** |
| 8 | 6.47 | 6.76 | **2.19** |
| 10 | 5.79 | 4.93 | **2.22** |

### Bounded explore traversal

| Hops | `pf.explore` p50 | `pf.explore` p95 | Neo4j p50 | Neo4j DbHits | Rows |
| --- | --- | --- | --- | --- | --- |
| `*1..1` | 4.56 | 4.96 | **3.20** | 123 | 2 |
| `*1..2` | 13.9 | 15.0 | **8.29** | 3,007 | 270 |
| `*1..3` | 13.9 | 14.9 | **3,052** | 24,902,813 | 2261 |

### The IS edge class, for information only

| Case | With `IS` p50 | Without `IS` p50 | Ratio |
| --- | --- | --- | --- |
| `d1-artist-artist` | 4.65 | 4.60 | 1.01x |
| `d1-artist-release` | 4.66 | 4.54 | 1.03x |
| `d2-artist-artist` | 13.2 | 13.5 | 0.97x |
| `d3-artist-artist` | 66.2 | 67.1 | 0.99x |
| `d4-artist-artist` | 55.5 | 37.9 | 1.47x |
| `d5-artist-artist` | 1,535 | 13.4 | 114.60x |
| `d6-artist-artist` | 1,551 | 126.2 | 12.29x |
| `unreachable` | 5.79 | 8.14 | 0.71x |
| `explore *1..1` | 4.56 | 4.52 | 1.01x |
| `explore *1..2` | 13.9 | 10.4 | 1.34x |
| `explore *1..3` | 13.9 | 12.0 | 1.16x |

### The seen set as a relation against an hstore

| Case | Relation seen set p50 | hstore seen set p50 |
| --- | --- | --- |
| `d1-artist-artist` | 4.65 | 1.05 |
| `d1-artist-release` | 4.66 | 0.74 |
| `d2-artist-artist` | 13.2 | 2.92 |
| `d3-artist-artist` | 66.2 | 234.6 |
| `d4-artist-artist` | 55.5 | 75.7 |
| `d5-artist-artist` | 1,535 | **QueryCanceled: canceling statement due to ** |
| `d6-artist-artist` | 1,551 | **QueryCanceled: canceling statement due to ** |
| `unreachable` | 5.79 | 1.70 |

### Verdict gate

```json
{
  "scale": "large",
  "mode": "local",
  "answers_match": true,
  "disagreements": [],
  "gate_p95_ms": 1000.0,
  "path": {
    "pf-vaat": {
      "worst_p95_ms": 2379.233,
      "over_gate": [
        {
          "workload": "path/d6-artist-artist/pf-vaat",
          "p95_ms": 2379.233,
          "case": "d6-artist-artist"
        },
        {
          "workload": "path/d5-artist-artist/pf-vaat",
          "p95_ms": 1585.657,
          "case": "d5-artist-artist"
        }
      ],
      "path_go": false,
      "go": false
    },
    "pf-vaat-deg": {
      "worst_p95_ms": 3442.133,
      "over_gate": [
        {
          "workload": "path/d6-artist-artist/pf-vaat-deg",
          "p95_ms": 3442.133,
          "case": "d6-artist-artist"
        }
      ],
      "path_go": false,
      "go": false
    }
  },
  "explore": {
    "worst_p95_ms": 14.933,
    "over_gate": [],
    "go": true
  },
  "variants_passing": [],
  "measured_ceiling": {
    "variant": "pf-vaat",
    "worst_p95_ms": 2379.233,
    "cases_over_gate": [
      "d6-artist-artist",
      "d5-artist-artist"
    ]
  },
  "go": false
}
```
