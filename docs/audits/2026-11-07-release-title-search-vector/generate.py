"""Load a Discogs-shaped `releases` sample whose documents are big enough to be toasted.

Usage: generate.py "<libpq DSN of a disposable database>" FIRST_ID COUNT [SEED]

About 2.5 KB of toast and 0.6 KB of heap per row (lux: 54 GB of toast over 19.6M rows, 2.75 KB),
and 'love' in about 2.54% of titles (lux: 497k of 19.6M). Run four copies over disjoint id ranges
to load in parallel; 2M rows took about two and a half minutes on a laptop.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import sys
from typing import Any

import psycopg


COMMON = [
    "the",
    "of",
    "in",
    "you",
    "my",
    "me",
    "night",
    "day",
    "time",
    "heart",
    "life",
    "world",
    "dance",
    "blue",
    "man",
    "girl",
    "baby",
    "dream",
    "fire",
    "way",
    "home",
    "light",
    "live",
    "soul",
    "rock",
    "sun",
    "song",
    "music",
    "rain",
    "city",
    "star",
    "mix",
    "remix",
    "version",
    "original",
    "dub",
    "club",
    "edit",
    "part",
    "vol",
    "volume",
    "best",
    "greatest",
    "hits",
    "collection",
    "sessions",
    "at",
    "from",
    "to",
    "for",
    "on",
    "with",
    "no",
    "one",
    "two",
    "all",
    "good",
    "new",
    "old",
    "black",
    "white",
    "red",
    "gold",
    "summer",
    "winter",
    "moon",
    "sky",
    "sea",
    "river",
    "road",
    "street",
    "boy",
    "woman",
    "lady",
    "king",
    "queen",
    "god",
    "angel",
    "devil",
    "paradise",
    "heaven",
    "hell",
    "blood",
    "tears",
    "kiss",
    "sweet",
    "cry",
    "lost",
    "found",
    "free",
    "wild",
    "dark",
    "deep",
    "high",
    "low",
    "young",
    "last",
    "first",
    "forever",
    "never",
    "always",
    "tonight",
    "tomorrow",
    "yesterday",
    "again",
    "away",
    "back",
    "down",
    "up",
    "out",
    "over",
    "under",
    "inside",
    "outside",
]
LOVE_SHARE = 0.0254


class Generator:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)  # noqa: S311 -- synthetic data, not security
        letters = "abcdefghijklmnopqrstuvwxyz"
        self.vocab = COMMON + ["".join(self.rng.choice(letters) for _ in range(self.rng.randint(3, 10))) for _ in range(6000)]

    def word(self) -> str:
        return self.vocab[int((self.rng.random() ** 2.2) * len(self.vocab))]

    def words(self, count: int) -> str:
        return " ".join(self.word() for _ in range(max(count, 1)))

    def name(self) -> str:
        return f"{self.word().title()} {self.word().title()}"

    def lognormal(self, scale: float) -> int:
        return int(math.exp(self.rng.random() * scale))

    def token(self) -> str:
        return hashlib.md5(str(self.rng.random()).encode(), usedforsecurity=False).hexdigest()

    def title(self) -> str:
        if self.rng.random() < LOVE_SHARE:
            if self.rng.random() < 0.5:
                return self.words(self.rng.randint(1, 3)).title() + " Love"
            return ("Love " + self.words(self.rng.randint(0, 3)).title()).strip()
        return self.words(self.rng.randint(1, 5)).title().replace("Love", "Lov")

    def track(self, position: int) -> dict[str, Any]:
        track: dict[str, Any] = {
            "position": f"{chr(65 + position // 8)}{1 + position % 8}",
            "title": self.words(self.rng.randint(1, 5)).title(),
            "duration": f"{self.rng.randint(2, 9)}:{self.rng.randint(0, 59):02d}",
        }
        if self.rng.random() < 0.35:
            track["extraartists"] = [
                {"id": self.rng.randint(1, 9_000_000), "name": self.name(), "role": self.word().title()} for _ in range(self.rng.randint(1, 4))
            ]
        return track

    def document(self, data_id: int) -> dict[str, Any]:
        rng = self.rng
        document: dict[str, Any] = {
            "id": data_id,
            "title": self.title(),
            "status": "Accepted",
            "year": str(rng.randint(1950, 2025)),
            "released": f"{rng.randint(1950, 2025)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            "country": rng.choice(["US", "UK", "Germany", "France", "Japan", "Netherlands", "Italy", "Canada", "Europe", "Unknown"]),
            "data_quality": "Needs Vote",
            "artists": [
                {"id": rng.randint(1, 9_000_000), "name": self.name(), "anv": "", "join": ",", "role": "", "tracks": ""}
                for _ in range(rng.randint(1, 2))
            ],
            "labels": [
                {
                    "id": rng.randint(1, 2_000_000),
                    "name": self.words(2).title() + " Records",
                    "catno": f"{self.word()[:3].upper()}-{rng.randint(1, 9999)}",
                }
                for _ in range(rng.randint(1, 2))
            ],
            "formats": [{"name": rng.choice(["Vinyl", "CD", "Cassette", "File"]), "qty": "1", "descriptions": ["LP", "Album", "Stereo"]}],
            "genres": [rng.choice(["Rock", "Electronic", "Pop", "Jazz", "Funk / Soul", "Hip Hop", "Classical", "Folk, World, & Country"])],
            "styles": [self.word().title(), self.word().title()],
            "identifiers": [
                {
                    "type": rng.choice(["Barcode", "Matrix / Runout", "Rights Society", "Label Code"]),
                    "value": self.token().upper(),
                    "description": self.words(2),
                }
                for _ in range(self.lognormal(2.5))
            ],
            "companies": [
                {"id": rng.randint(1, 1_000_000), "name": self.words(2).title() + " Ltd.", "entity_type_name": "Pressed By", "catno": ""}
                for _ in range(self.lognormal(2))
            ],
            "extraartists": [
                {"id": rng.randint(1, 9_000_000), "name": self.name(), "role": self.words(rng.randint(1, 3)).title(), "tracks": ""}
                for _ in range(self.lognormal(3.2))
            ],
            "tracklist": [self.track(position) for position in range(1 + self.lognormal(3.5))],
        }
        if rng.random() < 0.45:
            document["notes"] = self.words(self.lognormal(6.5))
        if rng.random() < 0.6:
            document["master_id"] = rng.randint(1, 2_500_000)
        if rng.random() < 0.3:
            document["videos"] = [
                {
                    "uri": "https://www.youtube.com/watch?v=" + self.token()[:11],
                    "title": self.words(4),
                    "description": self.words(8),
                    "duration": rng.randint(30, 600),
                }
                for _ in range(rng.randint(1, 6))
            ]
        return document


def main(dsn: str, first: int, count: int, seed: int) -> None:
    generator = Generator(seed)
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS releases (data_id VARCHAR PRIMARY KEY, hash VARCHAR NOT NULL, data JSONB NOT NULL, "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), gm_item_id UUID, media JSONB)"
        )
        with cursor.copy("COPY releases (data_id, hash, data) FROM STDIN") as copy:
            for data_id in range(first, first + count):
                text = json.dumps(generator.document(data_id), separators=(",", ":"))
                copy.write_row((str(data_id), hashlib.sha256(text.encode()).hexdigest(), text))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else int(sys.argv[2]))
