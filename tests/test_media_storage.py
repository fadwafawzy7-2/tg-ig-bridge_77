from pathlib import Path

import pytest

from app.media.storage import safe_relative_path


def test_safe_relative_path_stays_inside_root(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    resolved = safe_relative_path(root, "123/456/file.mp4")
    assert resolved == (root / "123/456/file.mp4").resolve()


def test_safe_relative_path_rejects_escape(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    with pytest.raises(ValueError):
        safe_relative_path(root, "../../secret.txt")
