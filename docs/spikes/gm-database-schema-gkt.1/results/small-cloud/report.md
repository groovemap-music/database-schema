## small scale, cloud mode

### The machine

| | |
| --- | --- |
| Label | IBM Cloud VPC bx2-8x32 (postgres) |
| Platform | Linux-6.8.0-1062-ibm-x86_64-with-glibc2.39 |
| CPU count | 8 |
| RAM | 31.39 GiB |
| SHA-256 single thread | 102,658 ops/s |
| SHA-256 all cores | 80,964 ops/s |
| Memory read | 29,796 MB/s |
| Disk sequential write | 52 MB/s |
| Disk random read 4k | 405,735 IOPS |
| Python sort 1M floats | 0.2513 s |

PostgreSQL: `PostgreSQL 19beta3 on x86_64-pc-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit`

Neo4j: `Neo4j Kernel 2026.07.1 community`

### Shortest path, median

| Case | d | `pf.find_path` p50 | `pf.find_path_deg` p50 | Neo4j p50 |
| --- | --- | --- | --- | --- |
| `d1-artist-artist` | 1 | 13.6 | 13.6 | **4.49** |
| `d1-artist-release` | 1 | 13.2 | 13.1 | **4.77** |
| `d2-artist-artist` | 2 | 30.6 | 20.4 | **3.85** |
| `d3-artist-artist` | 3 | 36.5 | 74.1 | **3.96** |
| `d4-artist-artist` | 4 | 49.5 | 60.5 | **3.43** |
| `d5-artist-artist` | 5 | 63.5 | 56.2 | **3.52** |
| `d6-artist-artist` | 6 | 66.6 | 72.8 | **3.80** |
| `unreachable` | — | 38.1 | 21.4 | **2.33** |

### Shortest path, p95 — the Verdict gate is 1000 ms

| Case | d | `pf.find_path` p95 | `pf.find_path_deg` p95 | Neo4j p95 |
| --- | --- | --- | --- | --- |
| `d1-artist-artist` | 1 | 14.5 | 14.0 | **6.14** |
| `d1-artist-release` | 1 | 13.8 | 13.4 | **6.57** |
| `d2-artist-artist` | 2 | 31.6 | 21.3 | **4.16** |
| `d3-artist-artist` | 3 | 37.6 | 76.0 | **5.86** |
| `d4-artist-artist` | 4 | 50.9 | 61.5 | **3.94** |
| `d5-artist-artist` | 5 | 65.8 | 57.7 | **3.94** |
| `d6-artist-artist` | 6 | 70.4 | 77.1 | **4.45** |
| `unreachable` | — | 38.8 | 21.9 | **2.85** |

### Work done

| Case | Vertices expanded | Touch probes | Seen rows | PostgreSQL buffers | Neo4j DbHits |
| --- | --- | --- | --- | --- | --- |
| `d1-artist-artist` | 0 | 1 | 2 | 889 | 18 |
| `d1-artist-release` | 0 | 1 | 2 | 634 | 665 |
| `d2-artist-artist` | 1 | 2 | 636 | 5,134 | 670 |
| `d3-artist-artist` | 5 | 6 | 699 | 5,971 | 772 |
| `d4-artist-artist` | 17 | 18 | 731 | 6,736 | 826 |
| `d5-artist-artist` | 10 | 11 | 1,499 | 14,446 | 1,657 |
| `d6-artist-artist` | 14 | 15 | 1,507 | 14,260 | 1,652 |
| `unreachable` | 2 | 2 | 636 | 4,965 | 659 |

### Depth cap against the miss

| Depth cap | `pf.find_path` p50 | `pf.find_path_deg` p50 | Neo4j p50 |
| --- | --- | --- | --- |
| 1 | 28.2 | 13.1 | **2.58** |
| 2 | 29.6 | 13.6 | **2.67** |
| 3 | 29.6 | 21.9 | **2.66** |
| 4 | 37.7 | 21.9 | **2.42** |
| 6 | 38.7 | 21.5 | **2.52** |
| 8 | 38.5 | 21.4 | **2.39** |
| 10 | 38.1 | 21.4 | **2.33** |

### Bounded explore traversal

| Hops | `pf.explore` p50 | `pf.explore` p95 | Neo4j p50 | Neo4j DbHits | Rows |
| --- | --- | --- | --- | --- | --- |
| `*1..1` | 30.3 | 31.8 | **7.93** | 1,751 | 88 |
| `*1..2` | 40.8 | 42.6 | **21.6** | 31,176 | 1276 |
| `*1..3` | 33.0 | 34.3 | **354.6** | 1,726,462 | 1996 |

### The IS edge class, for information only

| Case | With `IS` p50 | Without `IS` p50 | Ratio |
| --- | --- | --- | --- |
| `d1-artist-artist` | 13.6 | 13.2 | 1.03x |
| `d1-artist-release` | 13.2 | 13.1 | 1.01x |
| `d2-artist-artist` | 30.6 | 30.5 | 1.01x |
| `d3-artist-artist` | 36.5 | 35.2 | 1.04x |
| `d4-artist-artist` | 49.5 | 41.7 | 1.19x |
| `d5-artist-artist` | 63.5 | 33.2 | 1.91x |
| `d6-artist-artist` | 66.6 | 37.2 | 1.79x |
| `unreachable` | 38.1 | 28.6 | 1.33x |
| `explore *1..1` | 30.3 | 29.8 | 1.02x |
| `explore *1..2` | 40.8 | 40.7 | 1.00x |
| `explore *1..3` | 33.0 | 39.3 | 0.84x |

### The seen set as a relation against an hstore

| Case | Relation seen set p50 | hstore seen set p50 |
| --- | --- | --- |
| `d1-artist-artist` | 13.6 | 1.36 |
| `d1-artist-release` | 13.2 | 0.63 |
| `d2-artist-artist` | 30.6 | 9.69 |
| `d3-artist-artist` | 36.5 | 10.2 |
| `d4-artist-artist` | 49.5 | 20.1 |
| `d5-artist-artist` | 63.5 | 38.2 |
| `d6-artist-artist` | 66.6 | 42.3 |
| `unreachable` | 38.1 | 9.63 |

### Verdict gate

```json
{
  "scale": "small",
  "mode": "cloud",
  "answers_match": true,
  "disagreements": [],
  "gate_p95_ms": 1000.0,
  "path": {
    "pf-vaat": {
      "worst_p95_ms": 70.394,
      "over_gate": [],
      "path_go": true,
      "go": true
    },
    "pf-vaat-deg": {
      "worst_p95_ms": 77.129,
      "over_gate": [],
      "path_go": true,
      "go": true
    }
  },
  "explore": {
    "worst_p95_ms": 34.32,
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
