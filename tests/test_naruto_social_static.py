from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "app" / "naruto_game"


class NarutoSocialStaticTests(unittest.TestCase):
    def test_social_files_parse(self) -> None:
        for name in ["models.py", "social.py", "social_router.py", "integration.py", "router.py"]:
            ast.parse((ROOT / name).read_text(encoding="utf-8"))

    def test_naruto_clan_roles_are_present(self) -> None:
        text = (ROOT / "social.py").read_text(encoding="utf-8")
        expected = [
            "👑 Клан-лидер",
            "⚔️ Джонин-командир",
            "🕵 АНБУ-разведчик",
            "💰 Хранитель свитков и казны",
            "🥷 Элитный шиноби",
            "🎓 Генин",
        ]
        for role in expected:
            self.assertIn(role, text)

    def test_social_tables_are_declared(self) -> None:
        text = (ROOT / "models.py").read_text(encoding="utf-8")
        for table in [
            "naruto_settlements",
            "naruto_friendships",
            "naruto_player_mentorships",
            "naruto_duels",
            "naruto_settlement_wars",
            "naruto_mail",
            "naruto_clan_alliances",
            "naruto_tournaments",
        ]:
            self.assertIn(table, text)

    def test_social_router_has_core_mmo_commands(self) -> None:
        text = (ROOT / "social_router.py").read_text(encoding="utf-8")
        for command in [
            'Command("settlement"',
            'Command("friend"',
            'Command("duel"',
            'Command("student"',
            'Command("chatwar"',
            'Command("tournament"',
            'Command("alliance"',
            'Command("nmail"',
        ]:
            self.assertIn(command, text)

    def test_naruto_address_mode_exists(self) -> None:
        text = (ROOT / "social_router.py").read_text(encoding="utf-8").lower()
        self.assertIn("наруто", text)
        self.assertIn("зарегистрировать чат", text)
        self.assertIn("дружить", text)
        self.assertIn("дуэль", text)

    def test_social_router_is_integrated(self) -> None:
        text = (ROOT / "integration.py").read_text(encoding="utf-8")
        self.assertIn("social_router", text)
        self.assertIn("SocialPresenceMiddleware", text)


if __name__ == "__main__":
    unittest.main()
