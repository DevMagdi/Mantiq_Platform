"""Object-storage keys cannot escape the storage root (defence in depth around signed URLs)."""

from __future__ import annotations

import pytest
from app.integrations.storage import LocalStorage


def test_a_key_cannot_escape_the_root(tmp_path) -> None:
    store = LocalStorage(root=tmp_path / "objects")
    evil = tmp_path / "objects_evil"
    evil.mkdir()
    (evil / "secret.txt").write_text("do not read me", encoding="utf-8")

    # the old guard compared string prefixes, so a *sibling* directory whose name starts with the
    # root's name passed. The component-wise check refuses it.
    with pytest.raises(ValueError):
        store.put_object("../objects_evil/secret.txt", b"x", "text/plain")
    with pytest.raises(ValueError):
        store.get_object("../objects_evil/secret.txt")
    with pytest.raises(ValueError):
        store.get_object("/etc/passwd")


def test_a_normal_key_still_works(tmp_path) -> None:
    store = LocalStorage(root=tmp_path / "objects")
    store.put_object("media/recording.wav", b"RIFF", "audio/wav")
    assert store.get_object("media/recording.wav") == b"RIFF"
    with pytest.raises(FileNotFoundError):
        store.get_object("media/missing.wav")
