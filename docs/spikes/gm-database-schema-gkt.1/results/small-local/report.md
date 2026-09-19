## small scale, local mode

### The machine

| | |
| --- | --- |
| Label | Docker Desktop VM (local mode) |
| Platform | Linux-6.8.0-117-generic-aarch64-with-glibc2.41 |
| CPU count | 2 |
| RAM | 7.74 GiB |
| SHA-256 single thread | 476,484 ops/s |
| SHA-256 all cores | 747,848 ops/s |
| Memory read | 38,827 MB/s |
| Disk sequential write | 1,284 MB/s |
| Disk random read 4k | 492,277 IOPS |
| Python sort 1M floats | 0.1774 s |

PostgreSQL: `PostgreSQL 19beta3 on aarch64-unknown-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit`

Neo4j: `Neo4j Kernel 2026.07.1 community`

### Shortest path, median

| Case | d | `pf.find_path` p50 | `pf.find_path_deg` p50 | Neo4j p50 |
| --- | --- | --- | --- | --- |
| `d1-artist-artist` | 1 | 4.78 | 4.92 | **4.38** |
| `d1-artist-release` | 1 | 4.49 | 4.51 | **4.26** |
| `d2-artist-artist` | 2 | 12.1 | 7.71 | **3.97** |
| `d3-artist-artist` | 3 | 13.6 | 19.2 | **4.10** |
| `d4-artist-artist` | 4 | 17.0 | 19.5 | **3.39** |
| `d5-artist-artist` | 5 | 22.7 | 16.8 | **3.85** |
| `d6-artist-artist` | 6 | 23.9 | 27.5 | **3.68** |
| `unreachable` | — | 11.5 | 4.58 | **3.00** |

*30 timed iterations per row, after discarded warm-ups.*


### Shortest path, p95 — the Verdict gate is 1000 ms

| Case | d | `pf.find_path` p95 | `pf.find_path_deg` p95 | Neo4j p95 |
| --- | --- | --- | --- | --- |
| `d1-artist-artist` | 1 | 5.11 | 5.32 | **6.15** |
| `d1-artist-release` | 1 | 5.04 | 4.81 | **6.38** |
| `d2-artist-artist` | 2 | 13.3 | 8.17 | **6.05** |
| `d3-artist-artist` | 3 | 14.8 | 20.9 | **5.52** |
| `d4-artist-artist` | 4 | 22.8 | 21.6 | **4.66** |
| `d5-artist-artist` | 5 | 25.6 | 19.3 | **7.15** |
| `d6-artist-artist` | 6 | 25.6 | 30.0 | **4.89** |
| `unreachable` | — | 12.9 | 4.75 | **3.85** |

*30 timed iterations per row, after discarded warm-ups.*


### Work done

| Case | Vertices expanded | Touch probes | Seen rows | PostgreSQL buffers | Neo4j DbHits |
| --- | --- | --- | --- | --- | --- |
| `d1-artist-artist` | 0 | 1 | 2 | 830 | 18 |
| `d1-artist-release` | 0 | 1 | 2 | 416 | 639 |
| `d2-artist-artist` | 1 | 2 | 636 | 4,974 | 670 |
| `d3-artist-artist` | 5 | 6 | 699 | 5,772 | 788 |
| `d4-artist-artist` | 17 | 18 | 731 | 6,550 | 823 |
| `d5-artist-artist` | 10 | 11 | 1,499 | 14,084 | 1,690 |
| `d6-artist-artist` | 14 | 15 | 1,507 | 14,260 | 1,634 |
| `unreachable` | 2 | 2 | 636 | 4,964 | 659 |

### Depth cap against the miss

| Depth cap | `pf.find_path` p50 | `pf.find_path_deg` p50 | Neo4j p50 |
| --- | --- | --- | --- |
| 1 | 11.1 | 4.65 | **2.70** |
| 2 | 10.7 | 5.00 | **2.94** |
| 3 | 10.7 | 4.59 | **2.77** |
| 4 | 12.3 | 4.64 | **2.49** |
| 6 | 11.1 | 5.23 | **2.62** |
| 8 | 10.9 | 4.66 | **2.50** |
| 10 | 11.5 | 4.60 | **2.34** |

*30 timed iterations per row, after discarded warm-ups.*


### What a depth cap costs on a pair that is actually connected

| Cap | `pf.find_path` p50 / p95 | `pf.find_path_deg` p50 / p95 | Neo4j p50 / p95 | Answer |
| --- | --- | --- | --- | --- |
| 2 | 4.79 / 5.20 | 5.43 / 5.89 | **2.17 / 2.85** | no path |
| 3 | 5.42 / 5.91 | 5.52 / 5.89 | **2.04 / 2.55** | no path |
| 4 | 6.64 / 6.97 | 6.91 / 7.95 | **2.22 / 3.02** | no path |
| 6 | 23.6 / 26.6 | 27.3 / 29.2 | **3.17 / 4.15** | found, d = 6 |
| 10 | 24.4 / 26.9 | 27.0 / 28.8 | **2.55 / 3.67** | found, d = 6 |

*30 timed iterations per row, after discarded warm-ups.*


### Bounded explore traversal

| Hops | `pf.explore` p50 | `pf.explore` p95 | Neo4j p50 | Neo4j DbHits | Rows |
| --- | --- | --- | --- | --- | --- |
| `*1..1` | 11.6 | 12.2 | **5.90** | 1,751 | 88 |
| `*1..2` | 12.7 | 13.6 | **16.5** | 31,153 | 1276 |
| `*1..3` | 14.0 | 18.9 | **189.5** | 1,726,439 | 1996 |

*30 timed iterations per row, after discarded warm-ups.*


### The IS edge class, for information only

| Case | With `IS` p50 | Without `IS` p50 | Ratio |
| --- | --- | --- | --- |
| `d1-artist-artist` | 4.78 | 4.33 | 1.10x |
| `d1-artist-release` | 4.49 | 4.12 | 1.09x |
| `d2-artist-artist` | 12.1 | 11.5 | 1.05x |
| `d3-artist-artist` | 13.6 | 12.7 | 1.07x |
| `d4-artist-artist` | 17.0 | 14.2 | 1.20x |
| `d5-artist-artist` | 22.7 | 11.6 | 1.95x |
| `d6-artist-artist` | 23.9 | 13.6 | 1.76x |
| `unreachable` | 11.5 | 9.96 | 1.16x |
| `explore *1..1` | 11.6 | 11.7 | 0.99x |
| `explore *1..2` | 12.7 | 12.3 | 1.03x |
| `explore *1..3` | 14.0 | 12.5 | 1.12x |

*30 timed iterations per row, after discarded warm-ups.*


### The seen set as a relation against an hstore

| Case | Relation seen set p50 | hstore seen set p50 |
| --- | --- | --- |
| `d1-artist-artist` | 4.78 | 1.04 |
| `d1-artist-release` | 4.49 | 0.83 |
| `d2-artist-artist` | 12.1 | 3.37 |
| `d3-artist-artist` | 13.6 | 3.46 |
| `d4-artist-artist` | 17.0 | 7.49 |
| `d5-artist-artist` | 22.7 | 14.5 |
| `d6-artist-artist` | 23.9 | 17.7 |
| `unreachable` | 11.5 | 4.96 |

*Timed iterations per row, after discarded warm-ups: 5 for 8 rows, 30 for 8 rows.*


### Verdict gate

```json
{
  "scale": "small",
  "mode": "local",
  "answers_match": true,
  "disagreements": [],
  "gate_p95_ms": 1000.0,
  "path": {
    "pf-vaat": {
      "worst_p95_ms": 25.584,
      "over_gate": [],
      "path_go": true,
      "go": true
    },
    "pf-vaat-deg": {
      "worst_p95_ms": 30.041,
      "over_gate": [],
      "path_go": true,
      "go": true
    }
  },
  "explore": {
    "worst_p95_ms": 18.868,
    "over_gate": [],
    "go": true
  },
  "variants_passing": [
    "pf-vaat",
    "pf-vaat-deg"
  ],
  "measured_ceiling": null,
  "go": true
}
```
