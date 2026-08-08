from app.moderation_parser import parse_moderation_command


DEFAULT = 7 * 24 * 60 * 60


def parse(text: str):
    result = parse_moderation_command(text, default_duration_seconds=DEFAULT)
    assert result is not None
    return result


def test_inline_reason_without_duration_uses_default() -> None:
    result = parse("бан @username флуд")
    assert result.action == "ban"
    assert result.target_token == "@username"
    assert result.duration_seconds == DEFAULT
    assert result.duration_was_explicit is False
    assert result.reason == "флуд"


def test_multiline_reason_without_duration() -> None:
    result = parse("бан @username\nфлуд")
    assert result.target_token == "@username"
    assert result.duration_seconds == DEFAULT
    assert result.reason == "флуд"


def test_explicit_duration_and_reason() -> None:
    result = parse("мут @username 30 минут за флуд")
    assert result.action == "mute"
    assert result.duration_seconds == 1800
    assert result.duration_was_explicit is True
    assert result.reason == "флуд"


def test_reply_style_command_has_no_target_token() -> None:
    result = parse("мут 2 часа\nоскорбления")
    assert result.target_token is None
    assert result.duration_seconds == 7200
    assert result.reason == "оскорбления"


def test_permanent_ban() -> None:
    result = parse("бан @username навсегда реклама")
    assert result.duration_seconds == 0
    assert result.reason == "реклама"


def test_restriction_alias() -> None:
    result = parse("запретить ссылки @username 1 день реклама")
    assert result.action == "restrict_links"
    assert result.duration_seconds == 86400
    assert result.reason == "реклама"


def test_slash_command_remains_compatible_with_russian_duration() -> None:
    result = parse("/mute @username 10 минут флуд")
    assert result.action == "mute"
    assert result.duration_seconds == 600
    assert result.reason == "флуд"


def test_kick_and_unquarantine_aliases() -> None:
    kick = parse("кик @username флуд")
    assert kick.action == "kick"
    assert kick.reason == "флуд"
    unquarantine = parse("снять карантин @username")
    assert unquarantine.action == "unquarantine"
