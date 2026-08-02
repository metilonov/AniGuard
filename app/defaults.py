from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.command_catalog import BUILTIN_COMMANDS, default_builtin_commands


BASIC_MODERATION_COMMANDS: dict[str, dict[str, Any]] = {}



PREMIUM_SETTING_KEYS: set[str] = {
    "premium_quarantine",
    "premium_cases",
    "premium_schedule",
    "premium_stats",
    "smart_spam_enabled",
    "hidden_link_filter_enabled",
    "edited_message_filter_enabled",
    "obfuscation_filter_enabled",
    "phishing_filter_enabled",
    "domain_whitelist_enabled",
    "image_text_filter_enabled",
    "coordinated_spam_enabled",
    "account_risk_filter_enabled",
    "suspicious_profile_filter_enabled",
    "auto_quarantine_enabled",
    "punishment_ladder_enabled",
    "adaptive_protection_enabled",
    "raid_lockdown_enabled",
    "short_link_filter_enabled",
    "media_duplicate_filter_enabled",
    "mixed_alphabet_filter_enabled",
    "financial_spam_filter_enabled",
    "fake_giveaway_filter_enabled",
    "newcomer_media_filter_enabled",
    "custom_emoji_flood_enabled",
    "night_protection_enabled",
    "auto_chat_close_enabled",
    "newcomer_quarantine_seconds",
    "ladder_mute_seconds",
    "adaptive_trigger_count",
    "adaptive_window_seconds",
}


BASIC_MODERATION_COMMANDS = BUILTIN_COMMANDS


def default_basic_commands() -> dict[str, dict[str, Any]]:
    return default_builtin_commands()


def default_chat_settings() -> dict[str, Any]:
    return {
        # Message frequency and text volume.
        "anti_flood_enabled": True,
        "flood_limit": 6,
        "flood_window_seconds": 10,
        "flood_action": "delete_warn",
        "slow_mode_seconds": 0,
        "duplicate_filter_enabled": True,
        "duplicate_limit": 3,
        "duplicate_window_seconds": 30,
        "line_flood_enabled": False,
        "line_limit": 18,
        "long_message_filter_enabled": False,
        "max_message_length": 3500,
        "emoji_flood_enabled": False,
        "emoji_limit": 20,
        "hashtag_flood_enabled": False,
        "hashtag_limit": 8,
        "mass_mentions_enabled": True,
        "mass_mentions_limit": 5,
        "command_flood_enabled": False,
        "command_flood_limit": 6,
        "command_flood_window_seconds": 20,

        # Text and content.
        "caps_filter_enabled": False,
        "caps_ratio_percent": 75,
        "caps_min_letters": 12,
        "word_filter_enabled": True,
        "blocked_words": ["реклама", "спам", "обман", "накрутка"],
        "symbol_replacement_check": True,
        "invisible_symbols_filter_enabled": True,
        "edited_message_filter_enabled": False,
        "obfuscation_filter_enabled": False,
        "smart_spam_enabled": False,
        "mixed_alphabet_filter_enabled": False,
        "financial_spam_filter_enabled": False,
        "fake_giveaway_filter_enabled": False,

        # Links.
        "link_filter_enabled": True,
        "links_newbie_hours": 24,
        "allowed_domains": ["youtube.com", "youtu.be", "t.me"],
        "invite_link_filter_enabled": True,
        "short_link_filter_enabled": False,
        "hidden_link_filter_enabled": False,
        "phishing_filter_enabled": False,
        "domain_whitelist_enabled": False,

        # Media and Telegram message types.
        "forward_filter_enabled": False,
        "channel_sender_filter_enabled": False,
        "media_filter_enabled": False,
        "sticker_flood_enabled": False,
        "sticker_limit": 5,
        "sticker_window_seconds": 20,
        "voice_flood_enabled": False,
        "voice_limit": 4,
        "voice_window_seconds": 60,
        "dangerous_file_filter_enabled": True,
        "dangerous_extensions": ["exe", "scr", "bat", "cmd", "com", "msi", "apk", "jar", "js", "vbs", "ps1"],
        "contact_location_filter_enabled": False,
        "image_text_filter_enabled": False,
        "media_duplicate_filter_enabled": False,
        "poll_filter_enabled": False,
        "game_filter_enabled": False,
        "custom_emoji_flood_enabled": False,
        "custom_emoji_limit": 12,

        # Cross-account and newcomer protection.
        "coordinated_spam_enabled": False,
        "coordinated_spam_users": 3,
        "coordinated_spam_window_seconds": 60,
        "captcha_enabled": True,
        "captcha_timeout_seconds": 60,
        "captcha_attempts": 3,
        "captcha_failure_action": "kick",
        "captcha_image_set": "random",
        "captcha_message": (
            "{user}, подтвердите, что вы человек. Выберите смайл, соответствующий изображению. "
            "Время: {time} сек., попыток: {attempts}."
        ),
        "raid_join_limit": 8,
        "raid_window_seconds": 30,
        "newcomer_window_hours": 24,
        "account_risk_filter_enabled": False,
        "suspicious_profile_filter_enabled": False,
        "auto_quarantine_enabled": False,
        "newcomer_media_filter_enabled": False,
        "newcomer_quarantine_seconds": 3600,

        # Automatic punishment and emergency protection.
        "auto_warn_enabled": True,
        "auto_cleanup_enabled": False,
        "auto_cleanup_count": 10,
        "warn_threshold": 3,
        "punishment_ladder_enabled": False,
        "ladder_mute_seconds": 3600,
        "adaptive_protection_enabled": False,
        "adaptive_trigger_count": 5,
        "adaptive_window_seconds": 60,
        "raid_lockdown_enabled": False,
        "auto_chat_close_enabled": False,
        "night_protection_enabled": False,
        "night_start_hour": 23,
        "night_end_hour": 7,
        "night_flood_limit": 4,

        # Default moderation actions.
        "default_mute_seconds": 604800,
        "default_ban_seconds": 604800,
        "default_quarantine_seconds": 604800,
        "default_restrict_media_seconds": 604800,
        "default_restrict_links_seconds": 604800,
        "default_restrict_commands_seconds": 604800,
        "default_reason": "Причина не указана",
        "show_moderation_duration": True,
        "show_moderation_reason": True,
        "warnings_expire_days": 30,
        "basic_moderation_commands": default_basic_commands(),

        # Group rules and welcome message.
        "group_rules": [
            "Уважайте других участников.",
            "Запрещены флуд и массовый спам.",
            "Реклама допускается только с разрешения администрации.",
            "Не публикуйте личные данные других людей.",
            "Соблюдайте решения модераторов.",
        ],
        "welcome_enabled": True,
        "welcome_text": (
            "Добро пожаловать, {user}, в группу «{group}»! "
            "Ознакомьтесь с правилами и приятного общения."
        ),
        "welcome_photo_path": "",
        "welcome_photo_name": "",
        "welcome_after_captcha": True,
        "owner_user_id": None,

        # Reports and chat tools.
        "reports_enabled": True,
        "report_hide_threshold": 3,
        "chat_locked": False,
        "auto_ban_newcomers": False,
        "whitelist_user_ids": [],
        "dangerous_user_ids": [],
        "mute_immunity_user_ids": [],

        # Game and RP modules.
        "rp_enabled": True,
        "rp_default_cooldown": 5,
        "anime_enabled": True,
        "anime_replies": True,
        "anime_style": "shinobi",
        "ranks_enabled": True,
        "economy_enabled": True,
        "xp_per_message": 2,
        "coins_per_message": 1,

        # Backward-compatible Premium aliases.
        "premium_quarantine": False,
        "premium_cases": False,
        "premium_schedule": False,
        "premium_stats": False,
        "premium_smart_spam_enabled": False,
        "premium_hidden_links_enabled": False,
        "premium_suspicious_newcomers_enabled": False,
        "premium_auto_quarantine_enabled": False,
        "premium_punishment_ladder_enabled": False,
        "premium_adaptive_protection_enabled": False,
        "premium_raid_lockdown_enabled": False,
        "premium_newcomer_quarantine_seconds": 3600,
        "premium_ladder_mute_seconds": 3600,
        "premium_adaptive_trigger_count": 5,
        "premium_adaptive_window_seconds": 60,

        # Internal service values.
        "last_message_id": 0,
    }
