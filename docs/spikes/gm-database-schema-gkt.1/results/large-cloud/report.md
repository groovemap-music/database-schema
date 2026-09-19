## large scale, cloud mode

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
| `d1-artist-artist` | 1 | 14.2 | 14.3 | **4.22** |
| `d1-artist-release` | 1 | 13.7 | 14.6 | **3.77** |
| `d2-artist-artist` | 2 | 27.2 | 46.0 | **6.06** |
| `d3-artist-artist` | 3 | 170.9 | 97.7 | **6.22** |
| `d4-artist-artist` | 4 | 144.1 | 174.1 | **4.78** |
| `d5-artist-artist` | 5 | 3,875 | 719.1 | **90.9** |
| `d6-artist-artist` | 6 | 3,911 | 4,479 | **64.7** |
| `unreachable` | — | 24.6 | 22.0 | **2.53** |

*30 timed iterations per row, after discarded warm-ups.*


### Shortest path, p95 — the Verdict gate is 1000 ms

| Case | d | `pf.find_path` p95 | `pf.find_path_deg` p95 | Neo4j p95 |
| --- | --- | --- | --- | --- |
| `d1-artist-artist` | 1 | 14.5 | 14.5 | **5.39** |
| `d1-artist-release` | 1 | 13.9 | 14.8 | **4.51** |
| `d2-artist-artist` | 2 | 28.0 | 47.0 | **9.86** |
| `d3-artist-artist` | 3 | 172.4 | 99.5 | **6.71** |
| `d4-artist-artist` | 4 | 145.5 | 177.4 | **5.13** |
| `d5-artist-artist` | 5 | 4,059 | 729.1 | **101.0** |
| `d6-artist-artist` | 6 | 4,131 | 4,518 | **72.9** |
| `unreachable` | — | 25.9 | 22.3 | **2.99** |

*30 timed iterations per row, after discarded warm-ups.*


### Work done

| Case | Vertices expanded | Touch probes | Seen rows | PostgreSQL buffers | Neo4j DbHits |
| --- | --- | --- | --- | --- | --- |
| `d1-artist-artist` | 0 | 1 | 2 | 297 | 13 |
| `d1-artist-release` | 0 | 1 | 2 | 248 | 72 |
| `d2-artist-artist` | 1 | 2 | 57 | 10,199 | 4,842 |
| `d3-artist-artist` | 2 | 3 | 6,393 | 57,462 | 6,496 |
| `d4-artist-artist` | 57 | 58 | 2,609 | 27,208 | 3,213 |
| `d5-artist-artist` | 11 | 12 | 148,597 | 1,665,891 | 150,135 |
| `d6-artist-artist` | 18 | 19 | 148,604 | 1,700,919 | 150,164 |
| `unreachable` | 2 | 2 | 57 | 628 | 66 |

### Depth cap against the miss

| Depth cap | `pf.find_path` p50 | `pf.find_path_deg` p50 | Neo4j p50 |
| --- | --- | --- | --- |
| 1 | 23.1 | 22.0 | **2.41** |
| 2 | 24.5 | 22.4 | **2.72** |
| 3 | 24.6 | 22.0 | **2.56** |
| 4 | 24.1 | 21.9 | **2.35** |
| 6 | 24.2 | 22.1 | **2.29** |
| 8 | 24.3 | 21.8 | **2.32** |
| 10 | 24.5 | 22.2 | **2.29** |

*30 timed iterations per row, after discarded warm-ups.*


### Bounded explore traversal

| Hops | `pf.explore` p50 | `pf.explore` p95 | Neo4j p50 | Neo4j DbHits | Rows |
| --- | --- | --- | --- | --- | --- |
| `*1..1` | 23.4 | 24.1 | **2.72** | 123 | 2 |
| `*1..2` | 46.5 | 48.0 | **11.7** | 3,017 | 270 |
| `*1..3` | 46.9 | 48.7 | **5,295** | 24,902,823 | 2261 |

*Timed iterations per row, after discarded warm-ups: 10 for 1 row, 30 for 5 rows.*


### The IS edge class, for information only

| Case | With `IS` p50 | Without `IS` p50 | Ratio |
| --- | --- | --- | --- |
| `d1-artist-artist` | 14.2 | 13.4 | 1.06x |
| `d1-artist-release` | 13.7 | 13.6 | 1.01x |
| `d2-artist-artist` | 27.2 | 26.8 | 1.02x |
| `d3-artist-artist` | 170.9 | 174.3 | 0.98x |
| `d4-artist-artist` | 144.1 | 101.2 | 1.42x |
| `d5-artist-artist` | 3,875 | 42.3 | 91.69x |
| `d6-artist-artist` | 3,911 | 311.2 | 12.57x |
| `unreachable` | 24.6 | 22.8 | 1.08x |
| `explore *1..1` | 23.4 | 22.7 | 1.03x |
| `explore *1..2` | 46.5 | 39.3 | 1.18x |
| `explore *1..3` | 46.9 | 38.6 | 1.22x |

*30 timed iterations per row, after discarded warm-ups.*


### The seen set as a relation against an hstore

| Case | Relation seen set p50 | hstore seen set p50 |
| --- | --- | --- |
| `d1-artist-artist` | 14.2 | 1.28 |
| `d1-artist-release` | 13.7 | 0.64 |
| `d2-artist-artist` | 27.2 | 3.90 |
| `d3-artist-artist` | 170.9 | 618.9 |
| `d4-artist-artist` | 144.1 | 141.7 |
| `d5-artist-artist` | 3,875 | **QueryCanceled: canceling statement due to ** |
| `d6-artist-artist` | 3,911 | **QueryCanceled: canceling statement due to ** |
| `unreachable` | 24.6 | 2.95 |

*Timed iterations per row, after discarded warm-ups: 5 for 6 rows, 30 for 8 rows.*


### Verdict gate

```json
{
  "scale": "large",
  "mode": "cloud",
  "answers_match": true,
  "disagreements": [],
  "gate_p95_ms": 1000.0,
  "path": {
    "pf-vaat": {
      "worst_p95_ms": 4131.292,
      "over_gate": [
        {
          "workload": "path/d6-artist-artist/pf-vaat",
          "p95_ms": 4131.292,
          "case": "d6-artist-artist"
        },
        {
          "workload": "path/d5-artist-artist/pf-vaat",
          "p95_ms": 4058.712,
          "case": "d5-artist-artist"
        }
      ],
      "path_go": false,
      "go": false
    },
    "pf-vaat-deg": {
      "worst_p95_ms": 4518.238,
      "over_gate": [
        {
          "workload": "path/d6-artist-artist/pf-vaat-deg",
          "p95_ms": 4518.238,
          "case": "d6-artist-artist"
        }
      ],
      "path_go": false,
      "go": false
    }
  },
  "explore": {
    "worst_p95_ms": 48.713,
    "over_gate": [],
    "go": true
  },
  "variants_passing": [],
  "measured_ceiling": {
    "variant": "pf-vaat",
    "worst_p95_ms": 4131.292,
    "cases_over_gate": [
      "d6-artist-artist",
      "d5-artist-artist"
    ]
  },
  "go": false
}
```
