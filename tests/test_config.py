from app.config import Settings


def make(admin_ids):
    return Settings(BOT_TOKEN="123:token", ADMIN_IDS=admin_ids)


def test_admin_ids_accept_single_number() -> None:
    assert make(123456789).admin_ids == {123456789}


def test_admin_ids_accept_bracket_string() -> None:
    assert make("[123456789, 987654321]").admin_ids == {123456789, 987654321}


def test_admin_ids_accept_plain_csv() -> None:
    assert make("123456789,987654321").admin_ids == {123456789, 987654321}


def test_admin_ids_from_csv_environment(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123:token")
    monkeypatch.setenv("ADMIN_IDS", "123456789,987654321")
    assert Settings(_env_file=None).admin_ids == {123456789, 987654321}


def test_recovery_chat_ids_accept_negative_ids() -> None:
    settings = Settings(
        BOT_TOKEN="123:token",
        ADMIN_IDS="123456789",
        RECOVERY_CHAT_IDS="[-1001111111111,-1002222222222]",
        _env_file=None,
    )
    assert settings.recovery_chat_ids == {-1001111111111, -1002222222222}


def test_legacy_sqlite_url_is_mapped_to_persistent_data(monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", "/tmp/aniguard-data")
    settings = Settings(
        BOT_TOKEN="123:token",
        ADMIN_IDS="123456789",
        DATABASE_URL="sqlite+aiosqlite:///./aniguard.db",
        _env_file=None,
    )
    assert settings.database_url == "sqlite+aiosqlite:////tmp/aniguard-data/aniguard.db"
