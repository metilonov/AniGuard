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
