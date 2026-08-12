import ast
import json
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


CATALOG_PATH = Path("cogs/monstermanager/catalog.py")
SPEC = importlib.util.spec_from_file_location("monster_catalog_test_module", CATALOG_PATH)
catalog = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = catalog
SPEC.loader.exec_module(catalog)

MonsterCatalogConflict = catalog.MonsterCatalogConflict
MonsterCatalogError = catalog.MonsterCatalogError
MonsterCatalogRepository = catalog.MonsterCatalogRepository
filter_entries = catalog.filter_entries
flatten_catalog = catalog.flatten_catalog
parse_extra_json = catalog.parse_extra_json
parse_stat_triplet = catalog.parse_stat_triplet


class TestMonsterCatalogHelpers(unittest.TestCase):
    def test_editor_is_locked_to_the_requested_user_id(self):
        tree = ast.parse(Path("cogs/monstermanager/editor.py").read_text(encoding="utf-8"))
        assignment = next(
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "MONSTER_EDITOR_USER_ID"
                for target in node.targets
            )
        )
        self.assertEqual(295173706496475136, ast.literal_eval(assignment.value))

        editor_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "MonsterCatalogEditor"
        )
        command = next(
            node
            for node in editor_class.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "monster_editor"
        )
        self.assertIn("ctx.author.id", ast.unparse(command))
        self.assertIn("MONSTER_EDITOR_USER_ID", ast.unparse(command))

    def test_partial_name_search_is_case_insensitive(self):
        entries = flatten_catalog(
            {
                "1": [{"name": "Dragon Whelp"}],
                "2": [{"name": "Ancient Dragon"}, {"name": "Hydra"}],
            }
        )

        matches = filter_entries(entries, query="dRaGoN")

        self.assertEqual(["Dragon Whelp", "Ancient Dragon"], [
            entry.monster["name"] for entry in matches
        ])

    def test_search_and_tier_filter_can_be_combined(self):
        entries = flatten_catalog(
            {
                "1": [{"name": "Dark Wolf"}],
                "2": [{"name": "Dire Wolf"}, {"name": "Hydra"}],
            }
        )

        matches = filter_entries(entries, query="wolf", tier="2")

        self.assertEqual(["Dire Wolf"], [entry.monster["name"] for entry in matches])

    def test_stats_accept_integer_values_and_unknown_marker(self):
        self.assertEqual((1200, 700, 900), parse_stat_triplet("1200 / 700 / 900"))
        self.assertEqual(("???", "???", "???"), parse_stat_triplet("???, ???, ???"))

    def test_extra_json_cannot_override_editor_fields(self):
        with self.assertRaises(MonsterCatalogError):
            parse_extra_json('{"name": "Injected"}')


class TestMonsterCatalogRepository(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "monsters.json"
        self.original = {
            "1": [
                {
                    "name": "Wolf",
                    "hp": 100,
                    "attack": 90,
                    "defense": 80,
                    "element": "Nature",
                    "url": "https://example.test/wolf.png",
                    "ispublic": True,
                }
            ],
            "2": [],
        }
        self.path.write_text(json.dumps(self.original, indent=4), encoding="utf-8")
        self.repository = MonsterCatalogRepository(self.path)

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_add_writes_catalog_and_preserves_previous_file_as_backup(self):
        added = await self.repository.add(
            "2",
            {
                "name": "Ember Drake",
                "hp": 240,
                "attack": 220,
                "defense": 200,
                "element": "Fire",
                "url": "https://example.test/drake.png",
                "ispublic": False,
                "custom_drop": "ember",
            },
        )

        current = json.loads(self.path.read_text(encoding="utf-8"))
        backup = json.loads(
            self.path.with_suffix(".json.bak").read_text(encoding="utf-8")
        )
        self.assertEqual("2", added.tier)
        self.assertEqual("Ember Drake", current["2"][0]["name"])
        self.assertEqual("ember", current["2"][0]["custom_drop"])
        self.assertEqual(self.original, backup)

    async def test_update_can_move_tiers_and_preserves_optional_fields(self):
        entry = flatten_catalog(await self.repository.read())[0]
        updated = dict(entry.monster)
        updated.update({"name": "Dire Wolf", "hp": 180, "frontier_only": True})

        saved = await self.repository.update(entry.locator, "2", updated)

        current = await self.repository.read()
        self.assertEqual([], current["1"])
        self.assertEqual("Dire Wolf", current["2"][0]["name"])
        self.assertTrue(current["2"][0]["frontier_only"])
        self.assertEqual("2", saved.tier)

    async def test_delete_removes_only_the_exact_duplicate_entry(self):
        duplicate = dict(self.original["1"][0])
        duplicate["url"] = "https://example.test/second-wolf.png"
        await self.repository.add("1", duplicate)
        entries = flatten_catalog(await self.repository.read())

        deleted = await self.repository.delete(entries[1].locator)

        current = await self.repository.read()
        self.assertEqual("https://example.test/second-wolf.png", deleted.monster["url"])
        self.assertEqual(1, len(current["1"]))
        self.assertEqual("https://example.test/wolf.png", current["1"][0]["url"])

    async def test_stale_selection_is_rejected_instead_of_editing_wrong_record(self):
        entry = flatten_catalog(await self.repository.read())[0]
        changed = await self.repository.read()
        changed["1"][0]["name"] = "Changed elsewhere"
        self.path.write_text(json.dumps(changed), encoding="utf-8")

        with self.assertRaises(MonsterCatalogConflict):
            await self.repository.delete(entry.locator)


if __name__ == "__main__":
    unittest.main()
