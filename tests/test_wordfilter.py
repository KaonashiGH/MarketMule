import os
from pathlib import Path

from marketmule.core.config import PROJECT_ROOT
from marketmule.core.wordfilter import WordFilter


def make_filter(tmp_path: Path, text: str) -> WordFilter:
    path = tmp_path / "blocked_words.txt"
    path.write_text(text, encoding="utf-8")
    return WordFilter(path)


def test_matches_whole_words_ignoring_case(tmp_path: Path) -> None:
    words = make_filter(tmp_path, "# comment line\nbadword\n\nawful phrase\n")
    assert words.find("This is a BADWORD.") == ["badword"]
    assert words.find("an Awful   Phrase here") == ["awful phrase"]
    assert words.find("badwords are fine, so is notbadword") == []  # whole words only
    assert words.find("nothing wrong", None) == []


def test_reports_each_word_once_in_order(tmp_path: Path) -> None:
    words = make_filter(tmp_path, "one\ntwo\n")
    assert words.find("two one two", "one") == ["two", "one"]


def test_picks_up_edits_without_restarting(tmp_path: Path) -> None:
    words = make_filter(tmp_path, "first\n")
    assert words.find("first second") == ["first"]
    path = tmp_path / "blocked_words.txt"
    path.write_text("second\n", encoding="utf-8")
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 5))  # make sure the change is noticed
    assert words.find("first second") == ["second"]


def test_missing_or_empty_list_blocks_nothing(tmp_path: Path) -> None:
    assert WordFilter(tmp_path / "missing.txt").find("anything") == []
    assert make_filter(tmp_path, "# only comments\n").find("anything") == []


def test_shipped_list_loads() -> None:
    WordFilter(PROJECT_ROOT / "config" / "blocked_words.txt").find("hello")
