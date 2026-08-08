from app.captcha import CAPTCHA_TEMPLATES, select_captcha
from app.defaults import default_basic_commands, default_chat_settings


def test_semantic_captcha_has_nine_unique_choices_and_answer() -> None:
    selected = select_captcha("random")
    assert len(selected["options"]) == 9
    assert len(set(selected["options"])) == 9
    assert selected["answer"] in selected["options"]
    assert selected["path"].is_file()


def test_all_captcha_assets_exist() -> None:
    for template in CAPTCHA_TEMPLATES:
        selected = select_captcha(template["category"])
        assert selected["path"].is_file()


def test_default_settings_include_integrated_group_features() -> None:
    settings = default_chat_settings()
    required = {
        "basic_moderation_commands",
        "group_rules",
        "welcome_enabled",
        "welcome_text",
        "captcha_enabled",
        "captcha_timeout_seconds",
        "captcha_attempts",
        "captcha_failure_action",
        "owner_user_id",
    }
    assert required.issubset(settings)


def test_basic_command_triggers_are_unique() -> None:
    commands = default_basic_commands()
    triggers = [str(command["trigger"]).casefold() for command in commands.values()]
    assert len(triggers) == len(set(triggers))
