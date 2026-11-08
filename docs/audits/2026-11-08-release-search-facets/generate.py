"""Load a Discogs-shaped `releases` sample with genres, country, year and an ADR 0007 media block.

Usage: generate.py "<libpq DSN of a disposable database>" FIRST_ID COUNT [SEED]

The documents are fi4a's (`../2026-11-07-release-title-search-vector/generate.py`), toasted like
lux's, with one to three genres and lux's shape of years (a JSON number, missing in 12%) and countries (missing in 3%). Each
row also gets a `media` block shaped like the one discogs-sql-loader writes (about 0.6 KB, as on
lux), built from the document's formats.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import psycopg


_FI4A = Path(__file__).resolve().parent.parent / "2026-11-07-release-title-search-vector" / "generate.py"
_spec = importlib.util.spec_from_file_location("fi4a_generate", _FI4A)
assert _spec is not None and _spec.loader is not None
fi4a = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fi4a)

GENRES = ["Rock", "Electronic", "Pop", "Jazz", "Funk / Soul", "Hip Hop", "Classical", "Folk, World, & Country", "Reggae", "Latin", "Blues"]
MEDIUMS = {
    "Vinyl": [("vinyl", "vinyl_12", 12), ("vinyl", "vinyl_7", 7), ("vinyl", "vinyl_10", 10)],
    "CD": [("optical", "cd", None)],
    "Cassette": [("tape", "cassette", None)],
    "File": [("digital", "file", None)],
}


def media_block(rng: Any, formats: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    for fmt in formats:
        family, medium, size = rng.choice(MEDIUMS[fmt["name"]])
        items.append(
            {
                "qty": 1,
                "codec": None,
                "family": family,
                "medium": medium,
                "source": {"name": fmt["name"], "text": "", "provider": "discogs", "descriptions": fmt["descriptions"]},
                "channels": rng.choice([None, "stereo"]),
                "position": None,
                "variants": [],
                "speed_rpm": 45 if medium == "vinyl_7" else None,
                "appearance": [],
                "size_inches": size,
                "track_count": None,
            }
        )
    return {
        "flags": [],
        "items": items,
        "traits": [],
        "edition": [],
        "families": sorted({item["family"] for item in items}),
        "unmapped": {"formats": [], "descriptions": []},
        "container": None,
        "packaging": None,
        "release_kind": "album",
        "taxonomy_version": "1",
    }


def main(dsn: str, first: int, count: int, seed: int) -> None:
    generator = fi4a.Generator(seed)
    rng = generator.rng
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext('generate.py releases'))")
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS releases (data_id VARCHAR PRIMARY KEY, hash VARCHAR NOT NULL, data JSONB NOT NULL, "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), gm_item_id UUID, media JSONB)"
        )
        connection.commit()
        with cursor.copy("COPY releases (data_id, hash, data, media) FROM STDIN") as copy:
            for data_id in range(first, first + count):
                document = generator.document(data_id)
                document["genres"] = rng.sample(GENRES, rng.choice([1, 1, 1, 2, 2, 3]))
                if rng.random() < 0.12:
                    del document["year"]
                else:
                    document["year"] = int(document["year"])  # lux stores a JSON number
                if rng.random() < 0.03:
                    del document["country"]
                if rng.random() < 0.15:
                    document["formats"].append({"name": rng.choice(list(MEDIUMS)), "qty": "1", "descriptions": []})
                media = None if rng.random() < 0.01 else json.dumps(media_block(rng, document["formats"]), separators=(",", ":"))
                text = json.dumps(document, separators=(",", ":"))
                copy.write_row((str(data_id), hashlib.sha256(text.encode()).hexdigest(), text, media))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else int(sys.argv[2]))
