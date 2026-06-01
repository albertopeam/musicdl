from __future__ import annotations

import pytest

from musicdl.genre.normalizer import classify, normalise_tag


class TestNormaliseTag:
    @pytest.mark.parametrize("raw,expected", [
        ("House",           "house"),
        ("TECHNO",          "techno"),
        ("Drum & Bass",     "drum & bass"),
        ("  deep  house  ", "deep house"),
        ("trip-hop",        "trip-hop"),
        ("",                ""),
        ("A" * 10,          "a" * 10),
    ])
    def test_normalises_correctly(self, raw: str, expected: str) -> None:
        assert normalise_tag(raw) == expected


class TestClassify:
    def test_matches_first_known_tag(self) -> None:
        result = classify(["house", "deep house"])
        assert result == ("electronic", "house")

    def test_skips_noise_tags(self) -> None:
        result = classify(["seen live", "favorite", "techno"])
        assert result == ("electronic", "techno")

    def test_returns_none_when_no_match(self) -> None:
        assert classify(["seen live", "favorite", "awesome"]) is None

    def test_returns_none_for_empty_list(self) -> None:
        assert classify([]) is None

    def test_dnb_alias(self) -> None:
        result = classify(["dnb"])
        assert result == ("electronic", "drum and bass")

    def test_case_insensitive_via_normalise(self) -> None:
        result = classify(["TECHNO"])
        assert result == ("electronic", "techno")

    def test_hip_hop_with_space(self) -> None:
        result = classify(["hip hop"])
        assert result == ("hip-hop", "hip-hop")
