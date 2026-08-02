from app.game_action_catalog import GAME_ACTIONS, match_game_action


def test_contains_all_unique_game_actions_from_source():
    assert len(GAME_ACTIONS) == 299
    assert GAME_ACTIONS[1]["trigger"] == "катон_гокаку"
    assert GAME_ACTIONS[96]["trigger"] == "тренировка"
    assert GAME_ACTIONS[151]["trigger"] == "тобирама_водный_дракон"
    assert GAME_ACTIONS[300]["trigger"] == "даттебайо"
    assert 277 not in GAME_ACTIONS  # duplicate of source command 138


def test_syntax_supports_slash_underscore_and_plain_words():
    assert match_game_action("/катон_гокаку @user")[1]["number"] == 1
    assert match_game_action("катон_гокаку @user")[1]["number"] == 1
    assert match_game_action("катон гокаку @user")[1]["number"] == 1


def test_accent_normalization_and_force_game_prefix():
    match = match_game_action("/кава́рими @user")
    assert match and match[1]["number"] == 51
    forced = match_game_action("игра расен сюрикен @user")
    assert forced and forced[1]["number"] == 12 and forced[3] is True


def test_premium_and_regular_actions_are_split():
    premium = [item for item in GAME_ACTIONS.values() if item["premium"]]
    regular = [item for item in GAME_ACTIONS.values() if not item["premium"]]
    assert premium and regular


def test_bot_username_after_slash_command_is_supported():
    match = match_game_action("/катон_гокаку@AniGuardBot @user")
    assert match and match[1]["number"] == 1


def test_new_commands_support_plain_words_and_underscores():
    assert match_game_action('/тобирама_водный_дракон @user')[1]['number'] == 151
    assert match_game_action('тобирама водный дракон @user')[1]['number'] == 151
    assert match_game_action('игра даттебайо @user')[1]['number'] == 300
