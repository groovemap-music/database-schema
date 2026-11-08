"""Load a Discogs-shaped `masters` sample sized and shaped like lux's.

Usage: generate.py "<libpq DSN of a disposable database>" FIRST_ID COUNT [SEED]

Calibrated against a 2% sample of lux's masters (2026-11-08): stored document size p50 771,
p90 1559, p99 2438 bytes, about 2% toasted; 2.4 GB of heap over 2.6M rows (about nine
documents a page); a JSON number year on 93% of masters and a JSON null on the rest; titles of
about 21 characters and three lexemes; 'love' in 2.74% of titles, 'live' in 1.6%, 'rock' in
0.75%, 'dub' in 0.11%. Run several copies over disjoint id ranges to load in parallel.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import sys
from typing import Any

import psycopg


# Terms whose share of lux master titles the sample reproduces, and the share.
SHARES = (("love", 0.0274), ("live", 0.0158), ("rock", 0.0075), ("dub", 0.0011))
GENRES = ("Rock", "Electronic", "Pop", "Jazz", "Funk / Soul", "Hip Hop", "Classical", "Folk, World, & Country", "Reggae", "Latin")
QUALITY = ("Correct", "Needs Vote", "Needs Major Changes", "Complete And Correct")


class Generator:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)  # noqa: S311 -- synthetic data, not security
        letters = "abcdefghijklmnopqrstuvwxyz"
        reserved = {term for term, _share in SHARES}
        words: set[str] = set()
        while len(words) < 20000:
            word = "".join(self.rng.choice(letters) for _ in range(self.rng.randint(3, 9)))
            if word not in reserved and not any(term in word for term in reserved):
                words.add(word)
        self.vocab = sorted(words)

    def word(self) -> str:
        return self.vocab[int((self.rng.random() ** 2.4) * len(self.vocab))]

    def words(self, count: int) -> str:
        return " ".join(self.word() for _ in range(max(count, 1)))

    def lognormal(self, mu: float, sigma: float) -> int:
        return int(math.exp(self.rng.gauss(mu, sigma)))

    def title(self) -> str:
        words = self.words(self.rng.choice((1, 2, 2, 2, 3, 3, 3, 4, 4, 5))).split()
        draw = self.rng.random()
        for term, share in SHARES:
            if draw < share:
                words.insert(self.rng.randrange(len(words) + 1), term)
                break
            draw -= share
        return " ".join(word.title() for word in words)

    def video(self) -> dict[str, Any]:
        token = hashlib.md5(str(self.rng.random()).encode(), usedforsecurity=False).hexdigest()
        return {
            "@src": f"https://www.youtube.com/watch?v={token[:11]}",
            "title": self.words(self.rng.randint(2, 8)).title(),
            "@embed": "true",
            "@duration": str(self.rng.randint(0, 900)),
            "description": self.words(min(self.lognormal(2.0, 0.9), 70)),
        }

    def document(self, data_id: int) -> dict[str, Any]:
        rng = self.rng
        document: dict[str, Any] = {
            "id": str(data_id),
            "type": "data",
            "year": int(1950 + 75 * rng.random() ** 0.7) if rng.random() > 0.069 else None,
            "title": self.title(),
            "genres": rng.sample(GENRES, rng.choice((1, 1, 1, 2, 2, 3))),
            "sha256": hashlib.sha256(str(rng.random()).encode()).hexdigest(),
            "styles": [self.word().title() for _ in range(rng.choice((1, 2, 2, 3)))],
            "artists": [
                {"id": str(rng.randint(1, 9_000_000)), "join": ",", "name": f"{self.word().title()} {self.word().title()}"}
                for _ in range(rng.choice((1, 1, 1, 2)))
            ],
            "data_quality": rng.choice(QUALITY),
            "main_release": str(rng.randint(1, 30_000_000)),
        }
        videos = rng.choices(range(9), weights=(30, 20, 15, 11, 8, 6, 4, 3, 3))[0]
        if videos:
            document["videos"] = {"video": [self.video() for _ in range(videos)]}
        return document


def main(dsn: str, first: int, count: int, seed: int) -> None:
    generator = Generator(seed)
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        # Parallel copies would otherwise race on CREATE TABLE IF NOT EXISTS.
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext('generate.py masters'))")
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS masters (data_id VARCHAR PRIMARY KEY, hash VARCHAR NOT NULL, data JSONB NOT NULL, "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), gm_item_id UUID)"
        )
        connection.commit()
        with cursor.copy("COPY masters (data_id, hash, data) FROM STDIN") as copy:
            for data_id in range(first, first + count):
                text = json.dumps(generator.document(data_id), separators=(",", ":"))
                copy.write_row((str(data_id), hashlib.sha256(text.encode()).hexdigest(), text))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else int(sys.argv[2]))
