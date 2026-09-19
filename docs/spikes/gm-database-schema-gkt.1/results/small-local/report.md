## small scale, local mode

### The machine

| | |
| --- | --- |
| Label | Docker Desktop VM (local mode) |
| Platform | Linux-6.8.0-117-generic-aarch64-with-glibc2.41 |
| CPU count | 2 |
| RAM | 7.74 GiB |
| SHA-256 single thread | 461,097 ops/s |
| SHA-256 all cores | 723,310 ops/s |
| Memory read | 31,606 MB/s |
| Disk sequential write | 1,303 MB/s |
| Disk random read 4k | 455,631 IOPS |
| Python sort 1M floats | 0.1919 s |

PostgreSQL: `PostgreSQL 19beta3 on aarch64-unknown-linux-musl, compiled by gcc (Alpine 15.2.0) 15.2.0, 64-bit`

Neo4j: `Neo4j Kernel 2026.07.1 community`

### Shortest path, median

| Case | d | `pf.find_path` p50 | `pf.find_path_deg` p50 | Neo4j p50 |
| --- | --- | --- | --- | --- |
| `d1-artist-artist` | 1 | 5.99 | 6.82 | **4.88** |
| `d1-artist-release` | 1 | 5.25 | 6.25 | **5.57** |
| `d2-artist-artist` | 2 | 11.8 | 9.62 | **3.83** |
| `d3-artist-artist` | 3 | 13.4 | 21.4 | **4.10** |
| `d4-artist-artist` | 4 | 17.3 | 25.0 | **3.44** |
| `d5-artist-artist` | 5 | 23.7 | 22.3 | **3.44** |
| `d6-artist-artist` | 6 | 24.0 | 30.6 | **4.88** |
| `unreachable` | — | 12.2 | 7.84 | **2.04** |

### Shortest path, p95 — the Verdict gate is 1000 ms

| Case | d | `pf.find_path` p95 | `pf.find_path_deg` p95 | Neo4j p95 |
| --- | --- | --- | --- | --- |
| `d1-artist-artist` | 1 | 7.92 | 7.47 | **6.99** |
| `d1-artist-release` | 1 | 6.58 | 6.81 | **8.55** |
| `d2-artist-artist` | 2 | 12.4 | 10.4 | **6.53** |
| `d3-artist-artist` | 3 | 14.2 | 23.0 | **6.30** |
| `d4-artist-artist` | 4 | 18.4 | 27.7 | **5.09** |
| `d5-artist-artist` | 5 | 25.0 | 25.6 | **5.68** |
| `d6-artist-artist` | 6 | 25.4 | 31.7 | **6.61** |
| `unreachable` | — | 14.7 | 8.43 | **3.41** |

### Work done

| Case | Vertices expanded | Touch probes | Seen rows | PostgreSQL buffers | Neo4j DbHits |
| --- | --- | --- | --- | --- | --- |
| `d1-artist-artist` | 0 | 1 | 2 | 827 | 18 |
| `d1-artist-release` | 0 | 1 | 2 | 414 | 665 |
| `d2-artist-artist` | 1 | 2 | 636 | 4,968 | 670 |
| `d3-artist-artist` | 5 | 6 | 699 | 5,754 | 780 |
| `d4-artist-artist` | 17 | 18 | 731 | 6,544 | 823 |
| `d5-artist-artist` | 10 | 11 | 1,499 | 14,071 | 1,646 |
| `d6-artist-artist` | 14 | 15 | 1,507 | 14,250 | 1,633 |
| `unreachable` | 2 | 2 | 636 | 4,964 | 659 |

### Depth cap against the miss

| Depth cap | `pf.find_path` p50 | `pf.find_path_deg` p50 | Neo4j p50 |
| --- | --- | --- | --- |
| 1 | 11.2 | 5.91 | **3.19** |
| 2 | 11.0 | 6.57 | **3.09** |
| 3 | 11.4 | 7.16 | **3.22** |
| 4 | 11.2 | 6.79 | **2.53** |
| 6 | 11.6 | 6.84 | **2.71** |
| 8 | 12.0 | 6.93 | **2.18** |
| 10 | 12.2 | 7.84 | **2.04** |

### Bounded explore traversal

| Hops | `pf.explore` p50 | `pf.explore` p95 | Neo4j p50 | Neo4j DbHits | Rows |
| --- | --- | --- | --- | --- | --- |
| `*1..1` | 11.8 | 12.3 | **6.20** | 1,751 | 88 |
| `*1..2` | 12.5 | 14.1 | **14.7** | 31,176 | 1276 |
| `*1..3` | 13.2 | 16.8 | **187.9** | 1,726,462 | 1996 |

### The IS edge class, for information only

| Case | With `IS` p50 | Without `IS` p50 | Ratio |
| --- | --- | --- | --- |
| `d1-artist-artist` | 5.99 | 5.57 | 1.08x |
| `d1-artist-release` | 5.25 | 4.83 | 1.09x |
| `d2-artist-artist` | 11.8 | 11.1 | 1.06x |
| `d3-artist-artist` | 13.4 | 12.9 | 1.03x |
| `d4-artist-artist` | 17.3 | 14.4 | 1.20x |
| `d5-artist-artist` | 23.7 | 12.0 | 1.98x |
| `d6-artist-artist` | 24.0 | 14.4 | 1.66x |
| `unreachable` | 12.2 | 11.2 | 1.09x |
| `explore *1..1` | 11.8 | 11.4 | 1.04x |
| `explore *1..2` | 12.5 | 12.3 | 1.02x |
| `explore *1..3` | 13.2 | 11.8 | 1.12x |

### The seen set as a relation against an hstore

| Case | Relation seen set p50 | hstore seen set p50 |
| --- | --- | --- |
| `d1-artist-artist` | 5.99 | 1.32 |
| `d1-artist-release` | 5.25 | 1.01 |
| `d2-artist-artist` | 11.8 | 4.38 |
| `d3-artist-artist` | 13.4 | 4.09 |
| `d4-artist-artist` | 17.3 | 8.39 |
| `d5-artist-artist` | 23.7 | 15.8 |
| `d6-artist-artist` | 24.0 | 17.4 |
| `unreachable` | 12.2 | 4.34 |

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
      "worst_p95_ms": 25.375,
      "over_gate": [],
      "path_go": true,
      "go": true
    },
    "pf-vaat-deg": {
      "worst_p95_ms": 31.67,
      "over_gate": [],
      "path_go": true,
      "go": true
    }
  },
  "explore": {
    "worst_p95_ms": 16.784,
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
