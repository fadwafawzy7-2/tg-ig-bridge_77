from app.core.config import Settings
from app.instagram.media_resolver import resolve_media_url
import app.instagram.media_resolver as resolver


def test_public_https_url_is_kept():
    assert resolve_media_url(file_path="https://cdn.example.com/x.mp4") == "https://cdn.example.com/x.mp4"


def test_http_url_is_rejected():
    import pytest
    from app.core.exceptions import ConfigurationError
    with pytest.raises(ConfigurationError):
        resolve_media_url(file_path="http://cdn.example.com/x.mp4")
