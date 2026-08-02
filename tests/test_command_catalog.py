from app.command_catalog import BUILTIN_COMMANDS, match_builtin_command


def test_catalog_contains_all_requested_anime_commands():
    anime = [key for key in BUILTIN_COMMANDS if key.startswith('anime_')]
    assert len(anime) == 180
    assert 'anime_1' in BUILTIN_COMMANDS
    assert 'anime_100' in BUILTIN_COMMANDS
    assert 'anime_101' in BUILTIN_COMMANDS
    assert 'anime_180' in BUILTIN_COMMANDS


def test_command_syntax_accepts_slash_underscore_and_plain_words():
    assert match_builtin_command('/расен_сюрикен @user')[0] == 'anime_101'
    assert match_builtin_command('расен сюрикен @user')[0] == 'anime_101'
    assert match_builtin_command('расен_сюрикен @user')[0] == 'anime_101'


def test_ordinary_russian_and_english_aliases_share_action():
    russian = match_builtin_command('бан @user')
    english = match_builtin_command('/ban @user')
    assert russian and english
    assert russian[1]['action'] == english[1]['action'] == 'ban'


def test_accents_and_mixed_boruto_aliases_are_supported():
    assert match_builtin_command('/режим_ита́чи')[0] == 'anime_114'
    assert match_builtin_command('/режим_борuto')[0] == 'anime_120'


def test_first_hundred_moderation_commands_are_available():
    assert match_builtin_command('/расенган @user')[0] == 'anime_1'
    assert match_builtin_command('гендзюцу @user')[0] == 'anime_11'
    assert match_builtin_command('печать снята @user')[0] == 'anime_43'
    assert match_builtin_command('даттебайо')[0] == 'anime_91'
