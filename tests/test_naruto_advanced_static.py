from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "app" / "naruto_game"


class NarutoAdvancedStaticTests(unittest.TestCase):
    def test_advanced_files_parse(self) -> None:
        for name in ["advanced.py", "advanced_router.py", "models.py", "integration.py"]:
            ast.parse((ROOT / name).read_text(encoding="utf-8"))

    def test_living_world_tables_exist(self) -> None:
        text = (ROOT / "models.py").read_text(encoding="utf-8")
        for table in [
            "naruto_territories",
            "naruto_village_wars",
            "naruto_village_war_contributions",
            "naruto_npc_relations",
            "naruto_dynamic_missions",
            "naruto_world_events",
            "naruto_technique_research",
            "naruto_legend_records",
        ]:
            self.assertIn(table, text)

    def test_advanced_router_commands_exist(self) -> None:
        text = (ROOT / "advanced_router.py").read_text(encoding="utf-8")
        for command in [
            'Command("territory")',
            'Command("worldwar")',
            'Command("mobilize")',
            'Command("front")',
            'Command("npc")',
            'Command("livemission")',
            'Command("events")',
            'Command("path")',
            'Command("research")',
            'Command("legend")',
            'Command("legacy")',
        ]:
            self.assertIn(command, text)

    def test_core_scenario_mechanics_are_implemented(self) -> None:
        text = (ROOT / "advanced.py").read_text(encoding="utf-8")
        for symbol in [
            "TERRITORY_DEFS",
            "NPCS",
            "MISSION_TWISTS",
            "WORLD_EVENT_TEMPLATES",
            "territory_expedition",
            "village_war_declare",
            "npc_memory_add",
            "npc_promise_resolve",
            "dynamic_mission_choose",
            "ensure_world_event",
            "research_start",
            "research_train",
            "legend_status",
            "successor_prepare",
        ]:
            self.assertIn(symbol, text)

    def test_advanced_router_is_before_social_router(self) -> None:
        text = (ROOT / "integration.py").read_text(encoding="utf-8")
        self.assertIn("advanced_router", text)
        self.assertLess(text.index("naruto_router.include_router(advanced_router)"), text.index("naruto_router.include_router(social_router)"))

    def test_natural_naruto_aliases_exist(self) -> None:
        text = (ROOT / "advanced_router.py").read_text(encoding="utf-8").lower()
        for phrase in ["карта мира", "события", "что мне делать", "мой путь", "живая миссия", "отношения"]:
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
