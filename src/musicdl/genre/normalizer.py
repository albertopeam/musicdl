from __future__ import annotations

import re
import unicodedata

from musicdl.genre.taxonomy import GENRE_MAP, NOISE_TAGS

_PUNCT_RE = re.compile(r"[^\w\s&\-]")


def normalise_tag(raw: str) -> str:
    tag = unicodedata.normalize("NFKC", raw)
    tag = tag.lower().strip()
    tag = _PUNCT_RE.sub("", tag)
    tag = re.sub(r"\s+", " ", tag).strip()
    return tag


def classify(tags: list[str]) -> tuple[str, str] | None:
    """
    Given tags in descending weight order, return (primary, subgenre)
    for the first tag that matches GENRE_MAP, skipping NOISE_TAGS.
    Returns None if no match is found.
    """
    for raw in tags:
        norm = normalise_tag(raw)
        if norm in NOISE_TAGS:
            continue
        if norm in GENRE_MAP:
            return GENRE_MAP[norm]
    return None
