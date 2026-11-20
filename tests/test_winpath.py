from __future__ import annotations

from docsearch.winpath import display_path, extended


def test_long_drive_path_gets_the_extended_prefix() -> None:
    long_path = "C:\\" + "a" * 260
    assert extended(long_path).startswith("\\\\?\\C:\\")


def test_short_drive_path_stays_plain() -> None:
    assert not extended("C:\\a\\b.docx").startswith("\\\\?\\")


def test_unc_path_becomes_unc_extended() -> None:
    assert extended("\\\\srv\\partage\\a\\b.docx") == "\\\\?\\UNC\\srv\\partage\\a\\b.docx"


def test_extended_prefix_is_not_doubled() -> None:
    assert extended("\\\\?\\C:\\a") == "\\\\?\\C:\\a"


def test_display_path_removes_the_prefix() -> None:
    assert display_path("\\\\?\\T:\\a\\b.docx") == "T:\\a\\b.docx"
    assert display_path("\\\\?\\UNC\\srv\\partage\\a.docx") == "\\\\srv\\partage\\a.docx"
    assert display_path("T:\\a\\b.docx") == "T:\\a\\b.docx"


def test_linux_paths_are_untouched() -> None:
    assert extended("/tmp/a/b.docx", windows=False) == "/tmp/a/b.docx"
