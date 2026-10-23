"""Blocked words, checked before anything members type is posted.

Discord's AutoMod doesn't check messages sent through webhooks, and posts and ratings are sent
through webhooks so they show the member's name. So the bot does its own check.

The list lives in config/blocked_words.txt: one word or phrase per line, # for comments.
Matching ignores capital letters and only matches whole words, so "class" never trips "ass".
Edits to the file apply on the next check. No restart needed.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)


class WordFilter:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._loaded_mtime: float | None = None
        self._pattern: re.Pattern[str] | None = None

    def find(self, *texts: str | None) -> list[str]:
        """Blocked words found in the texts, lowercased, without repeats, in the order they appear."""
        pattern = self._current_pattern()
        if pattern is None:
            return []
        found: list[str] = []
        for text in texts:
            for match in pattern.finditer(text or ""):
                word = " ".join(match.group(0).lower().split())  # tidy extra spaces in phrases
                if word not in found:
                    found.append(word)
        return found

    def _current_pattern(self) -> re.Pattern[str] | None:
        try:
            mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            return None
        if mtime != self._loaded_mtime:
            self._pattern = self._compile(self._path.read_text(encoding="utf-8"))
            self._loaded_mtime = mtime
            log.info("Loaded blocked words from %s", self._path.name)
        return self._pattern

    @staticmethod
    def _compile(text: str) -> re.Pattern[str] | None:
        entries = [line.strip() for line in text.splitlines()]
        words = sorted({e.lower() for e in entries if e and not e.startswith("#")}, key=len, reverse=True)
        if not words:
            return None
        # Longest first, so a phrase wins over a word inside it. Whole words only.
        alternatives = "|".join(r"\s+".join(map(re.escape, w.split())) for w in words)
        return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)
