from app.core.config import Settings
from app.runtime.preflight import validate_settings


def valid_settings(**overrides):
    values = dict(
        APP_ENV="production",
        DEBUG=False,
        POSTGRES_PASSWORD="secret",
        TELEGRAM_API_ID=123,
        TELEGRAM_API_HASH="hash",
        TELEGRAM_BOT_TOKEN="bot",
        TELEGRAM_DASHBOARD_ADMIN_IDS="1",
        AI_API_KEY="ai",
        INSTAGRAM_ACCESS_TOKEN="ig",
        INSTAGRAM_BUSINESS_ACCOUNT_ID="123",
        INSTAGRAM_MEDIA_BASE_URL="https://example.com/media",
        MEDIA_STORAGE_BACKEND="local",
    )
    values.update(overrides)
    return Settings(**values)


def test_production_preflight_accepts_complete_settings():
    result = validate_settings(valid_settings())
    assert result.ok
    assert not result.errors


def test_production_preflight_rejects_non_https_media_url():
    result = validate_settings(valid_settings(INSTAGRAM_MEDIA_BASE_URL="http://example.com/media"))
    assert not result.ok
    assert any("HTTPS" in error for error in result.errors)


def test_production_preflight_rejects_debug():
    result = validate_settings(valid_settings(DEBUG=True))
    assert not result.ok
    assert any("DEBUG" in error for error in result.errors)


def test_database_url_escapes_special_password_and_supports_ssl():
    settings = valid_settings(POSTGRES_PASSWORD="p@ss/word?x", POSTGRES_SSL_MODE="require")
    assert "p%40ss%2Fword%3Fx" in settings.DATABASE_URL
    assert "ssl=require" in settings.DATABASE_URL
    assert "sslmode=require" in settings.SYNC_DATABASE_URL
