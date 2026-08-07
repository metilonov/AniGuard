from __future__ import annotations

import unittest
import random
from types import SimpleNamespace

from app.naruto_game.content import BOSSES, STORY_CHAPTERS, TECHNIQUES, TECHNIQUE_UNLOCKS
from app.naruto_game.engine import (
    apply_player_action,
    arena_league,
    elemental_modifier,
    make_battle_state,
    upgrade_success_chance,
)


class NarutoGameEngineTests(unittest.TestCase):
    def profile(self, level: int = 20) -> SimpleNamespace:
        return SimpleNamespace(
            name="Ren",
            max_hp=900,
            hp=900,
            max_chakra=700,
            chakra=700,
            ninjutsu=130,
            taijutsu=110,
            genjutsu=90,
            defense=100,
            speed=120,
            accuracy=95,
            chakra_control=105,
            crit_chance=0.08,
            primary_element="fire",
            level=level,
        )

    def test_element_cycle(self) -> None:
        self.assertGreater(elemental_modifier("fire", "wind"), 1.0)
        self.assertLess(elemental_modifier("wind", "fire"), 1.0)
        self.assertEqual(elemental_modifier("fire", "fire"), 1.0)

    def test_battle_state_and_turn(self) -> None:
        state = make_battle_state(
            self.profile(),
            BOSSES["rogue"],
            [("basic_strike", 1), ("fireball", 1)],
        )
        result = apply_player_action(state, "tech:fireball")
        self.assertIn("player", result.state)
        self.assertIn("enemy", result.state)
        self.assertLessEqual(result.state["enemy"]["hp"], BOSSES["rogue"]["hp"])

    def test_custom_technique_is_usable(self) -> None:
        random.seed(7)
        custom = {
            "custom_black_moon": {
                "name": "Пламя Чёрной Луны",
                "element": "fire",
                "kind": "ninjutsu",
                "chakra": 180,
                "power": 430,
                "accuracy": 1.0,
                "cooldown": 3,
            }
        }
        state = make_battle_state(
            self.profile(60),
            BOSSES["rogue"],
            [("basic_strike", 1), ("custom_black_moon", 1)],
            custom,
        )
        before = state["enemy"]["hp"]
        result = apply_player_action(state, "tech:custom_black_moon")
        self.assertLess(result.state["enemy"]["hp"], before)

    def test_story_references_existing_bosses(self) -> None:
        for chapter in STORY_CHAPTERS.values():
            self.assertIn(chapter["boss"], BOSSES)

    def test_unlock_references_existing_techniques(self) -> None:
        for key, requirement in TECHNIQUE_UNLOCKS.items():
            self.assertIn(key, TECHNIQUES)
            if requirement.get("requires"):
                self.assertIn(requirement["requires"], TECHNIQUES)


    def test_sharingan_passive_buffs_battle_stats(self) -> None:
        profile = self.profile(50)
        profile.flags = {"dojutsu": "sharingan_3"}
        profile.biju = {}
        state = make_battle_state(profile, BOSSES["rogue"], [("basic_strike", 1)])
        self.assertGreater(state["player"]["accuracy"], profile.accuracy)
        self.assertGreater(state["player"]["speed"], profile.speed)

    def test_eight_gates_form_changes_build(self) -> None:
        profile = self.profile(85)
        profile.flags = {"battle_form": "gates_8"}
        profile.biju = {}
        state = make_battle_state(profile, BOSSES["rogue"], [("basic_strike", 1)])
        self.assertGreater(state["player"]["taijutsu"], profile.taijutsu)
        self.assertGreater(state["player"]["speed"], profile.speed)
        self.assertLess(state["player"]["defense"], profile.defense)


    def test_battle_consumable_changes_state(self) -> None:
        profile = self.profile(40)
        profile.flags = {}
        profile.biju = {}
        state = make_battle_state(profile, BOSSES["rogue"], [("basic_strike", 1)])
        state["player"]["hp"] = 400
        result = apply_player_action(state, "item:medkit")
        self.assertGreater(result.state["player"]["hp"], 400)
        self.assertEqual(result.state.get("consumed_item"), "medkit")

    def test_summon_participates_in_battle(self) -> None:
        random.seed(11)
        profile = self.profile(50)
        profile.flags = {"battle_summon": "toads"}
        profile.biju = {}
        state = make_battle_state(profile, BOSSES["rogue"], [("basic_strike", 1)])
        before = state["enemy"]["hp"]
        result = apply_player_action(state, "defend")
        self.assertLess(result.state["enemy"]["hp"], before)

    def test_upgrade_chance_drops_with_level(self) -> None:
        self.assertGreater(upgrade_success_chance(1), upgrade_success_chance(9))

    def test_arena_leagues(self) -> None:
        self.assertIn("Бронза", arena_league(900))
        self.assertIn("Шесть Путей", arena_league(2500))


if __name__ == "__main__":
    unittest.main()
