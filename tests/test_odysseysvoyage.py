import unittest

from cogs.odysseysvoyage import (
    CardKind,
    GameMode,
    PlayedCard,
    Suit,
    VoyageCard,
    build_deck,
    captured_rank_14_bonus_cards,
    rank_14_bonus,
    resolve_trick,
)


def card(name, kind, suit=None, rank=0):
    return VoyageCard(id=name.lower().replace(" ", "-"), name=name, kind=kind, suit=suit, rank=rank)


def played(*cards):
    return [
        PlayedCard(player_id=index, player_name=f"Player {index}", card=card_)
        for index, card_ in enumerate(cards, start=1)
    ]


CREW = card("Eurylochus", CardKind.CREW)
ODYSSEUS = card("King", CardKind.ODYSSEUS)
WOMAN = card("Sirens", CardKind.WOMAN)
CIRCE = card("Circe", CardKind.WOMAN)
POSEIDON = card("Poseidon's Wrath", CardKind.DIVINE)
NOBODY = card("Nobody", CardKind.ODYSSEUS)
WIND = card("Wind Bag", CardKind.WIND)


class OdysseyVoyageResolverTests(unittest.TestCase):
    def assert_winner_name(self, expected_name, *cards_to_play, mode=GameMode.VOYAGE):
        result = resolve_trick(played(*cards_to_play), mode=mode)
        self.assertEqual(result.winner.card.name, expected_name)

    def test_order_based_counter_examples(self):
        self.assert_winner_name("Sirens", CREW, ODYSSEUS, WOMAN)
        self.assert_winner_name("King", WOMAN, CREW, ODYSSEUS)
        self.assert_winner_name("Eurylochus", WOMAN, ODYSSEUS, CREW)
        self.assert_winner_name("Sirens", ODYSSEUS, CREW, WOMAN)
        self.assert_winner_name("Eurylochus", ODYSSEUS, WOMAN, CREW)
        self.assert_winner_name("King", CREW, WOMAN, ODYSSEUS)

    def test_circe_exception(self):
        self.assert_winner_name("Circe", CREW, CIRCE)
        self.assert_winner_name("Circe", ODYSSEUS, CIRCE)
        self.assert_winner_name("Circe", card("Sea 20", CardKind.NUMBERED, Suit.SEA, 20), CIRCE)

    def test_poseidons_wrath_beats_odysseus_cards(self):
        self.assert_winner_name("Poseidon's Wrath", ODYSSEUS, POSEIDON)
        self.assert_winner_name("Poseidon's Wrath", NOBODY, POSEIDON, mode=GameMode.EPIC)

    def test_first_crew_wins_without_later_counter(self):
        second_crew = card("Polites", CardKind.CREW)
        self.assert_winner_name("Eurylochus", CREW, second_crew)

    def test_first_wind_bag_wins_if_all_wind(self):
        second_wind = card("Wind Bag", CardKind.WIND)
        result = resolve_trick(played(WIND, second_wind))
        self.assertEqual(result.winner.player_id, 1)

    def test_numbered_trump_and_led_suit(self):
        sea_12 = card("Sea 12", CardKind.NUMBERED, Suit.SEA, 12)
        sea_14 = card("Sea 14", CardKind.NUMBERED, Suit.SEA, 14)
        war_20 = card("War 20", CardKind.NUMBERED, Suit.WAR, 20)
        olympus_1 = card("Olympus 1", CardKind.NUMBERED, Suit.OLYMPUS, 1)

        self.assert_winner_name("Sea 14", sea_12, war_20, sea_14)
        self.assert_winner_name("Olympus 1", sea_12, war_20, olympus_1)

    def test_deck_has_enough_cards_and_two_divine_interventions(self):
        deck = build_deck(12, GameMode.EPIC, cards_needed=12 * sum(range(1, 11)))
        self.assertGreaterEqual(len(deck), 12 * sum(range(1, 11)))
        self.assertEqual(sum(1 for card_ in deck if card_.kind is CardKind.DIVINE), 2)

    def test_card_labels_include_character_category(self):
        self.assertEqual(CREW.short_label(), "Eurylochus (crew)")
        self.assertEqual(ODYSSEUS.short_label(), "King (ody)")
        self.assertEqual(WOMAN.short_label(), "Sirens (women)")
        self.assertEqual(NOBODY.short_label(GameMode.EPIC, reveal=False), "💨 Wind Bag")

    def test_rank_14_capture_bonuses_apply_to_any_captured_fourteen(self):
        sea_14 = card("Sea 14", CardKind.NUMBERED, Suit.SEA, 14)
        war_14 = card("War 14", CardKind.NUMBERED, Suit.WAR, 14)
        journey_14 = card("Journey 14", CardKind.NUMBERED, Suit.JOURNEY, 14)
        olympus_14 = card("Olympus 14", CardKind.NUMBERED, Suit.OLYMPUS, 14)
        sea_13 = card("Sea 13", CardKind.NUMBERED, Suit.SEA, 13)

        bonus_cards = captured_rank_14_bonus_cards(played(sea_13, sea_14, war_14, journey_14, olympus_14))

        self.assertEqual([card_.name for card_ in bonus_cards], ["Sea 14", "War 14", "Journey 14", "Olympus 14"])
        self.assertEqual(sum(rank_14_bonus(card_) for card_ in bonus_cards), 50)


if __name__ == "__main__":
    unittest.main()
