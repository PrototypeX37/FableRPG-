import json
import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path("cogs/quests/campaign_content.py")
SPEC = importlib.util.spec_from_file_location("campaign_content_contract", MODULE_PATH)
campaign_content = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(campaign_content)

CampaignPackageError = campaign_content.CampaignPackageError
quest_record_from_node = campaign_content.quest_record_from_node
validate_package = campaign_content.validate_package


EXAMPLE_PATH = Path("assets/data/campaign_package_example.json")


class TestCampaignContent(unittest.TestCase):
    def load_example(self):
        return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))

    def test_example_package_is_valid(self):
        package = validate_package(self.load_example())
        self.assertEqual("ashen_road", package["campaigns"][0]["key"])
        self.assertEqual(2, len(package["monsters"]))

    def test_quest_conversion_adds_campaign_gate_and_conditions(self):
        package = validate_package(self.load_example())
        campaign = package["campaigns"][0]
        node = campaign["nodes"][2]
        record = quest_record_from_node(campaign, node)

        self.assertEqual("ashen_road_mercy_path", record["quest_key"])
        self.assertEqual("ashen_road", record["access"]["campaign_key"])
        self.assertEqual("mercy_path", record["access"]["campaign_node_key"])
        self.assertEqual(2, len(record["access"]["conditions"]))

    def test_missing_branch_target_is_rejected(self):
        package = self.load_example()
        package["campaigns"][0]["nodes"][0]["next"][0]["target"] = "missing"
        with self.assertRaises(CampaignPackageError) as raised:
            validate_package(package)
        self.assertTrue(any("missing node" in error for error in raised.exception.errors))

    def test_unknown_condition_is_rejected(self):
        package = self.load_example()
        package["campaigns"][0]["requirements"] = [
            {"type": "has_a_cool_hat", "key": "yes"}
        ]
        with self.assertRaises(CampaignPackageError):
            validate_package(package)

    def test_invalid_party_range_is_rejected(self):
        package = self.load_example()
        encounter = package["campaigns"][0]["nodes"][4]["encounter"]
        encounter["party"] = {"min": 5, "max": 2}
        with self.assertRaises(CampaignPackageError):
            validate_package(package)

    def test_v2_nested_condition_groups_and_state_effects_are_valid(self):
        package = self.load_example()
        package["schema_version"] = 2
        campaign = package["campaigns"][0]
        campaign["requirements"] = {
            "any": [
                {"type": "system_unlock", "key": "battle_tower_door_4_freedom"},
                {
                    "all": [
                        {"type": "level", "operator": ">=", "value": 50},
                        {"not": {"type": "state", "key": "world.exiled", "operator": "==", "value": True}},
                    ]
                },
            ]
        }
        campaign["nodes"][0]["effects"] = [
            {
                "type": "state_set",
                "key": "World.Bramblewick.Status",
                "value": "endangered",
            },
            {
                "type": "state_increment",
                "key": "Companion.Mira.Trust",
                "delta": 2,
            },
        ]

        normalized = validate_package(package)
        self.assertIn("any", normalized["campaigns"][0]["requirements"])
        effects = normalized["campaigns"][0]["nodes"][0]["effects"]
        self.assertEqual("world.bramblewick.status", effects[0]["key"])
        self.assertEqual("companion.mira.trust", effects[1]["key"])

    def test_empty_v2_any_group_is_rejected(self):
        package = self.load_example()
        package["schema_version"] = 2
        package["campaigns"][0]["requirements"] = {"any": []}
        with self.assertRaises(CampaignPackageError) as raised:
            validate_package(package)
        self.assertTrue(any("cannot be empty" in error for error in raised.exception.errors))

    def test_unknown_future_schema_is_rejected(self):
        package = self.load_example()
        package["schema_version"] = 3
        with self.assertRaises(CampaignPackageError):
            validate_package(package)

    def test_v2_story_dialogue_and_travel_nodes_are_supported(self):
        package = self.load_example()
        package["schema_version"] = 2
        nodes = package["campaigns"][0]["nodes"]
        nodes[0]["type"] = "scene"
        nodes[1]["type"] = "dialogue"
        nodes[2]["type"] = "travel"
        nodes[2]["location_key"] = "Greyhaven South Gate"

        normalized = validate_package(package)
        normalized_nodes = normalized["campaigns"][0]["nodes"]
        self.assertEqual("scene", normalized_nodes[0]["type"])
        self.assertEqual("dialogue", normalized_nodes[1]["type"])
        self.assertEqual("greyhaven_south_gate", normalized_nodes[2]["location_key"])

    def test_v2_relationship_location_and_war_models_are_valid(self):
        package = self.load_example()
        package["schema_version"] = 2
        campaign = package["campaigns"][0]
        campaign["requirements"] = {
            "all": [
                {
                    "type": "relationship",
                    "key": "Mira Vale",
                    "field": "Trust",
                    "operator": ">=",
                    "value": 5,
                },
                {
                    "type": "location_state",
                    "key": "Bramblewick",
                    "field": "Status",
                    "operator": "==",
                    "value": "safe",
                },
            ]
        }
        campaign["nodes"][0]["effects"] = [
            {
                "type": "relationship_change",
                "key": "Mira Vale",
                "field": "Trust",
                "delta": 2,
            },
            {
                "type": "war_increment",
                "key": "Dawn Crown",
                "field": "Strength",
                "delta": 1,
            },
        ]

        normalized = validate_package(package)
        conditions = normalized["campaigns"][0]["requirements"]["all"]
        self.assertEqual("mira_vale", conditions[0]["key"])
        self.assertEqual("trust", conditions[0]["field"])

    def test_v2_permanent_story_rewards_require_keys(self):
        package = self.load_example()
        package["schema_version"] = 2
        quest = package["campaigns"][0]["nodes"][0]["quest"]
        quest["reward"] = {
            "type": "specialization",
            "key": "wildshape",
            "name": "Wildshape",
        }
        normalized = validate_package(package)
        self.assertEqual(
            "wildshape",
            normalized["campaigns"][0]["nodes"][0]["quest"]["reward"]["key"],
        )

        quest["reward"] = {"type": "title", "name": "Realmfall"}
        with self.assertRaises(CampaignPackageError):
            validate_package(package)

    def test_v2_endings_can_grant_permanent_collection_rewards(self):
        package = self.load_example()
        package["schema_version"] = 2
        ending = package["campaigns"][0]["nodes"][-1]
        ending["effects"] = [
            {
                "type": "fable_reward",
                "reward_type": "title",
                "key": "crownless_king",
                "name": "The Crownless King",
            }
        ]
        normalized = validate_package(package)
        self.assertEqual(
            "crownless_king",
            normalized["campaigns"][0]["nodes"][-1]["effects"][0]["key"],
        )

    def test_v2_unreachable_nodes_are_rejected(self):
        package = self.load_example()
        package["schema_version"] = 2
        package["campaigns"][0]["nodes"].append(
            {
                "id": "forgotten_scene",
                "type": "scene",
                "title": "Forgotten",
                "next": [{"target": "ending"}],
            }
        )
        with self.assertRaises(CampaignPackageError) as raised:
            validate_package(package)
        self.assertTrue(any("unreachable" in error for error in raised.exception.errors))

    def test_v2_staged_scenario_is_validated_and_normalized(self):
        package = self.load_example()
        package["schema_version"] = 2
        node = package["campaigns"][0]["nodes"][0]
        node["type"] = "scenario"
        node["scenario"] = {
            "key": "Sky Fall",
            "start_stage": "Steer Descent",
            "stages": [
                {
                    "id": "Steer Descent",
                    "title": "The Sky Is Burning",
                    "actions": [
                        {
                            "label": "Aim for the river",
                            "outcome": "river_landing",
                            "effects": [
                                {
                                    "type": "relationship_change",
                                    "key": "mira",
                                    "field": "trust",
                                    "delta": 1,
                                }
                            ],
                        }
                    ],
                }
            ],
            "outcomes": [
                {
                    "id": "river_landing",
                    "title": "Cold Water",
                    "target": "roadside_choice",
                }
            ],
        }

        normalized = validate_package(package)
        scenario = normalized["campaigns"][0]["nodes"][0]["scenario"]
        self.assertEqual("sky_fall", scenario["key"])
        self.assertEqual("steer_descent", scenario["start_stage"])
        self.assertEqual("river_landing", scenario["stages"][0]["actions"][0]["outcome"])

        node["scenario"]["stages"][0]["actions"][0]["next_stage"] = "Steer Descent"
        with self.assertRaises(CampaignPackageError):
            validate_package(package)


if __name__ == "__main__":
    unittest.main()
