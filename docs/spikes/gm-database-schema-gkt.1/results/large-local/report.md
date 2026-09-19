## large scale, local mode

### The machine

| | |
| --- | --- |
| Label | Docker Desktop VM on Apple M1 Pro (2 vCPU, 7.7 GiB) |
| Platform | Linux-6.8.0-117-generic-aarch64-with-glibc2.41 |
| CPU count | 2 |
| RAM | 7.74 GiB |
| SHA-256 single thread | 492,154 ops/s |
| SHA-256 all cores | 840,039 ops/s |
| Memory read | 40,656 MB/s |
| Disk sequential write | 503 MB/s |
| Disk random read 4k | 476,206 IOPS |
| Python sort 1M floats | 0.1765 s |

PostgreSQL: `PostgreSQL 19beta3 on aarch64-unknown-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit`

Neo4j: `Neo4j Kernel 2026.07.1 community`

### Shortest path, median

| Case | d | `pf.find_path` p50 | `pf.find_path_deg` p50 | Neo4j p50 |
| --- | --- | --- | --- | --- |
| `d1-artist-artist` | 1 | 5.23 | 5.22 | **4.05** |
| `d1-artist-release` | 1 | 5.62 | 5.90 | **3.96** |
| `d2-artist-artist` | 2 | 14.6 | 19.0 | **7.14** |
| `d3-artist-artist` | 3 | 67.9 | 42.2 | **6.22** |
| `d4-artist-artist` | 4 | 57.3 | 77.9 | **4.47** |
| `d5-artist-artist` | 5 | 1,774 | 334.3 | **39.9** |
| `d6-artist-artist` | 6 | 1,686 | 2,187 | **39.8** |
| `unreachable` | — | 5.99 | 5.20 | **4.02** |

*30 timed iterations per row, after discarded warm-ups.*


### Shortest path, p95 — the Verdict gate is 1000 ms

| Case | d | `pf.find_path` p95 | `pf.find_path_deg` p95 | Neo4j p95 |
| --- | --- | --- | --- | --- |
| `d1-artist-artist` | 1 | 5.76 | 5.71 | **5.78** |
| `d1-artist-release` | 1 | 6.79 | 6.14 | **5.71** |
| `d2-artist-artist` | 2 | 15.3 | 20.2 | **10.1** |
| `d3-artist-artist` | 3 | 74.8 | 45.1 | **6.96** |
| `d4-artist-artist` | 4 | 59.2 | 83.0 | **5.45** |
| `d5-artist-artist` | 5 | 1,940 | 347.9 | **43.0** |
| `d6-artist-artist` | 6 | 1,761 | 2,246 | **42.7** |
| `unreachable` | — | 6.47 | 7.99 | **7.32** |

*30 timed iterations per row, after discarded warm-ups.*


### Work done

| Case | Vertices expanded | Touch probes | Seen rows | PostgreSQL buffers | Neo4j DbHits |
| --- | --- | --- | --- | --- | --- |
| `d1-artist-artist` | 0 | 1 | 2 | 297 | 13 |
| `d1-artist-release` | 0 | 1 | 2 | 248 | 72 |
| `d2-artist-artist` | 1 | 2 | 57 | 10,199 | 8,328 |
| `d3-artist-artist` | 2 | 3 | 6,393 | 57,464 | 6,545 |
| `d4-artist-artist` | 57 | 58 | 2,609 | 27,208 | 3,125 |
| `d5-artist-artist` | 11 | 12 | 148,597 | 1,665,891 | 150,297 |
| `d6-artist-artist` | 18 | 19 | 148,604 | 1,700,920 | 150,150 |
| `unreachable` | 2 | 2 | 57 | 631 | 66 |

### Depth cap against the miss

| Depth cap | `pf.find_path` p50 | `pf.find_path_deg` p50 | Neo4j p50 |
| --- | --- | --- | --- |
| 1 | 5.73 | 5.36 | **2.83** |
| 2 | 6.16 | 5.37 | **3.20** |
| 3 | 5.96 | 5.04 | **3.22** |
| 4 | 6.10 | 5.46 | **2.71** |
| 6 | 6.32 | 5.25 | **2.66** |
| 8 | 6.38 | 4.99 | **2.48** |
| 10 | 5.96 | 5.08 | **2.66** |

*30 timed iterations per row, after discarded warm-ups.*


### What a depth cap costs on a pair that is actually connected

| Cap | `pf.find_path` p50 / p95 | `pf.find_path_deg` p50 / p95 | Neo4j p50 / p95 | Answer |
| --- | --- | --- | --- | --- |
| 2 | 5.44 / 5.84 | 5.62 / 5.95 | **1.89 / 2.32** | no path |
| 3 | 6.03 / 6.56 | 6.41 / 6.82 | **1.96 / 2.65** | no path |
| 4 | 10.0 / 10.5 | 10.5 / 11.3 | **2.00 / 2.66** | no path |
| 6 | 1,740 / 1,856 | 2,163 / 2,436 | **37.9 / 43.0** | found, d = 6 |
| 10 | 1,755 / 1,871 | 2,397 / 2,852 | **37.6 / 43.4** | found, d = 6 |

*30 timed iterations per row, after discarded warm-ups.*


### Bounded explore traversal

| Hops | `pf.explore` p50 | `pf.explore` p95 | Neo4j p50 | Neo4j DbHits | Rows |
| --- | --- | --- | --- | --- | --- |
| `*1..1` | 4.95 | 5.74 | **4.26** | 123 | 2 |
| `*1..2` | 16.2 | 18.0 | **7.69** | 3,007 | 270 |
| `*1..3` | 15.3 | 17.3 | **3,207** | 24,902,813 | 2261 |

*30 timed iterations per row, after discarded warm-ups.*


### The IS edge class, for information only

| Case | With `IS` p50 | Without `IS` p50 | Ratio |
| --- | --- | --- | --- |
| `d1-artist-artist` | 5.23 | 4.82 | 1.09x |
| `d1-artist-release` | 5.62 | 5.31 | 1.06x |
| `d2-artist-artist` | 14.6 | 13.8 | 1.06x |
| `d3-artist-artist` | 67.9 | 72.3 | 0.94x |
| `d4-artist-artist` | 57.3 | 41.8 | 1.37x |
| `d5-artist-artist` | 1,774 | 13.9 | 127.73x |
| `d6-artist-artist` | 1,686 | 122.4 | 13.78x |
| `unreachable` | 5.99 | 5.51 | 1.09x |
| `explore *1..1` | 4.95 | 5.45 | 0.91x |
| `explore *1..2` | 16.2 | 11.6 | 1.40x |
| `explore *1..3` | 15.3 | 10.6 | 1.43x |

*30 timed iterations per row, after discarded warm-ups.*


### The seen set as a relation against an hstore

| Case | Relation seen set p50 | hstore seen set p50 |
| --- | --- | --- |
| `d1-artist-artist` | 5.23 | 1.10 |
| `d1-artist-release` | 5.62 | 0.62 |
| `d2-artist-artist` | 14.6 | 2.90 |
| `d3-artist-artist` | 67.9 | 228.4 |
| `d4-artist-artist` | 57.3 | 52.9 |
| `d5-artist-artist` | 1,774 | **QueryCanceled: canceling statement due to ** |
| `d6-artist-artist` | 1,686 | **QueryCanceled: canceling statement due to ** |
| `unreachable` | 5.99 | 1.79 |

*Timed iterations per row, after discarded warm-ups: 5 for 6 rows, 30 for 8 rows.*


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
      "worst_p95_ms": 1939.901,
      "over_gate": [
        {
          "workload": "path/d5-artist-artist/pf-vaat",
          "p95_ms": 1939.901,
          "case": "d5-artist-artist"
        },
        {
          "workload": "path/d6-artist-artist/pf-vaat",
          "p95_ms": 1761.013,
          "case": "d6-artist-artist"
        }
      ],
      "path_go": false,
      "go": false
    },
    "pf-vaat-deg": {
      "worst_p95_ms": 2246.436,
      "over_gate": [
        {
          "workload": "path/d6-artist-artist/pf-vaat-deg",
          "p95_ms": 2246.436,
          "case": "d6-artist-artist"
        }
      ],
      "path_go": false,
      "go": false
    }
  },
  "explore": {
    "worst_p95_ms": 17.285,
    "over_gate": [],
    "go": true
  },
  "variants_passing": [],
  "measured_ceiling": {
    "variant": "pf-vaat",
    "worst_p95_ms": 1939.901,
    "cases_over_gate": [
      "d5-artist-artist",
      "d6-artist-artist"
    ]
  },
  "go": false
}
```
