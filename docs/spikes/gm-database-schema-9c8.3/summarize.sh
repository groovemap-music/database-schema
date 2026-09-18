#!/usr/bin/env bash
# Collapse a results directory into the medians the spike document reports.
#
# Median rather than mean, and every run kept beside it, for the reason
# gm-database-schema-9c8.1 gives: a mean over a handful of runs on a laptop is at
# the mercy of whatever else the machine did during run three.
set -euo pipefail

out="${1:?usage: summarize.sh <results-dir>}"

for file in "$out"/timings.tsv; do
    [[ -f "$file" ]] || continue
    echo "== $file"
    awk -F'\t' '
        NR == 1 { next }
        { key = $1 FS $2 FS $3; runs[key] = runs[key] " " $5; if ($5 + 0 > 0) { v[key, ++n[key]] = $5 + 0 } }
        END {
            for (key in runs) {
                c = n[key]
                if (c == 0) { printf "%s\t%s\t%s\n", key, "-", runs[key]; continue }
                for (i = 1; i <= c; i++) { a[i] = v[key, i] }
                for (i = 2; i <= c; i++) { x = a[i]; j = i - 1
                    while (j > 0 && a[j] > x) { a[j + 1] = a[j]; j-- }
                    a[j + 1] = x }
                median = (c % 2) ? a[(c + 1) / 2] : (a[c / 2] + a[c / 2 + 1]) / 2
                printf "%s\t%.1f\t%s\n", key, median, runs[key]
                delete a
            }
        }' "$file" | sort -t$'\t' -k1,1 | column -t -s$'\t'
done

if [[ -f "$out/answers.csv" ]]; then
    echo
    echo "== $out/answers.csv"
    column -t -s, "$out/answers.csv"
fi
