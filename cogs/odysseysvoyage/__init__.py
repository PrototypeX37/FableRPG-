"""
Odyssey's Voyage, a Discord trick-taking minigame inspired by Skull King.
"""

from __future__ import annotations

import asyncio
import io
import random
import struct
import zlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import discord
from discord.ext import commands


MIN_PLAYERS = 4
MAX_PLAYERS = 20
DEFAULT_ROUNDS = 5
MIN_ROUNDS = 1
MAX_ROUNDS = 10
STARTING_HAND_SIZE = 5
PROPHECY_TIMEOUT = 60
TURN_TIMEOUT = 60
CAPTURE_BONUS = 10
ODYSSEY_COLOR = discord.Color.from_rgb(37, 92, 140)
CARD_WIDTH = 160
CARD_HEIGHT = 230
CARD_GAP = 16
CARD_COLUMNS = 5
CARD_BG = (248, 244, 232)
CARD_INK = (36, 33, 30)
CARD_BORDER = (86, 64, 39)
SUIT_COLORS = {
    "olympus": (225, 172, 45),
    "sea": (42, 130, 183),
    "war": (173, 56, 50),
    "journey": (121, 88, 166),
    "special": (70, 78, 96),
    "wind": (125, 153, 166),
}


class Suit(Enum):
    OLYMPUS = ("olympus", "⚡", "Olympus")
    SEA = ("sea", "🌊", "Sea")
    WAR = ("war", "⚔", "War")
    JOURNEY = ("journey", "🏺", "Journey")

    @property
    def emoji(self) -> str:
        return self.value[1]

    @property
    def label(self) -> str:
        return self.value[2]

    def display(self) -> str:
        return f"{self.emoji} {self.label}"


class CardKind(Enum):
    NUMBERED = "numbered"
    WIND = "wind"
    ODYSSEUS = "odysseus"
    CREW = "crew"
    WOMAN = "woman"
    DIVINE = "divine"


class GameMode(Enum):
    VOYAGE = "voyage"
    EPIC = "epic"


SPECIAL_CATEGORY = {
    CardKind.CREW: "crew",
    CardKind.WOMAN: "woman",
    CardKind.ODYSSEUS: "odysseus",
}
CHARACTER_CATEGORY_LABELS = {
    "crew": "⛵ Crew Member",
    "woman": "🌙 Women of the Voyage",
    "odysseus": "🧭 Odysseus",
}
CARD_ART_URLS = {
    "olympus": "https://i.imgur.com/9nCG6Ho.png",
    "sea": "https://i.imgur.com/k7rVoer.png",
    "war": "https://i.imgur.com/TpH46ll.png",
    "journey": "https://i.imgur.com/9xy85qe.png",
    "odysseus": "https://i.imgur.com/lG5qk85.png",
    "woman": "https://i.imgur.com/wlvAvyF.png",
    "crew": "https://i.imgur.com/K95QjtD.png",
}


async def is_game_master(bot, user: discord.abc.User) -> bool:
    from utils.checks import user_is_gm

    return await user_is_gm(bot, user)

COUNTERS = {
    "crew": "woman",
    "woman": "odysseus",
    "odysseus": "crew",
}
CARD_DICTIONARY = [
    {
        "title": "⚡ Olympus Suit",
        "button": "⚡ Olympus",
        "art_url": CARD_ART_URLS["olympus"],
        "entries": [
            ("Cards", "Olympus numbered cards run from 1 to 14."),
            ("Power", "Olympus is the default trump suit. Higher Olympus ranks beat lower Olympus ranks."),
            ("Rank 14 Bonus", "Olympus 14 gives +20 bonus when it wins a trick."),
        ],
    },
    {
        "title": "🌊 Sea Suit",
        "button": "🌊 Sea",
        "art_url": CARD_ART_URLS["sea"],
        "entries": [
            ("Cards", "Sea numbered cards run from 1 to 14."),
            ("Power", "Follow Sea when it is led if you can. Higher Sea ranks beat lower Sea ranks."),
            ("Rank 14 Bonus", "Sea 14 gives +10 bonus when it wins a trick."),
        ],
    },
    {
        "title": "⚔ War Suit",
        "button": "⚔ War",
        "art_url": CARD_ART_URLS["war"],
        "entries": [
            ("Cards", "War numbered cards run from 1 to 14."),
            ("Power", "Follow War when it is led if you can. Higher War ranks beat lower War ranks."),
            ("Rank 14 Bonus", "War 14 gives +10 bonus when it wins a trick."),
        ],
    },
    {
        "title": "🏺 Journey Suit",
        "button": "🏺 Journey",
        "art_url": CARD_ART_URLS["journey"],
        "entries": [
            ("Cards", "Journey numbered cards run from 1 to 14."),
            ("Power", "Follow Journey when it is led if you can. Higher Journey ranks beat lower Journey ranks."),
            ("Rank 14 Bonus", "Journey 14 gives +10 bonus when it wins a trick."),
        ],
    },
    {
        "title": "💨 Wind Bag",
        "button": "💨 Wind",
        "cards": [lambda: VoyageCard("dict-wind", "Wind Bag", CardKind.WIND)],
        "entries": [
            ("Power", "Usually loses and does not set a led suit. Can be played at any time."),
            ("Stands Against", "If every played card is a Wind Bag or inactive intervention, the first played card wins."),
        ],
    },
    {
        "title": "Character Counter Triangle",
        "button": "Counters",
        "cards": [
            lambda: VoyageCard("dict-crew", "Polites", CardKind.CREW),
            lambda: VoyageCard("dict-woman", "Sirens", CardKind.WOMAN),
            lambda: VoyageCard("dict-odysseus", "Trickster", CardKind.ODYSSEUS),
        ],
        "entries": [
            ("Free Play", "Character cards are not tied to suits and can be played at any time."),
            ("⛵ Crew Member", "Beats 🌙 Women of the Voyage. Loses to 🧭 Odysseus."),
            ("🌙 Women of the Voyage", "Beats 🧭 Odysseus. Loses to ⛵ Crew Member."),
            ("🧭 Odysseus", "Beats ⛵ Crew Member. Loses to 🌙 Women of the Voyage."),
            ("Odysseus Limit", "Only one Odysseus card appears in each round."),
            ("Capture Bonus", f"Winning through the triangle gains +{CAPTURE_BONUS} for each captured countered character card."),
            ("Order", "When several character cards appear, the latest card that counters the active character takes control."),
        ],
    },
    {
        "title": "🧭 Odysseus Cards",
        "button": "🧭 Odysseus",
        "art_url": CARD_ART_URLS["odysseus"],
        "entries": [
            ("Cards", "Nobody, Strategist, Trickster, and King. Only one appears each round."),
            ("Power", f"Beats ⛵ Crew Member cards and gains +{CAPTURE_BONUS} for each captured Crew Member. Loses to 🌙 Women of the Voyage cards."),
            ("Epic Effects", "Nobody hides as Wind Bag, Strategist swaps cards, Trickster changes trump, King gains +20 when winning."),
        ],
    },
    {
        "title": "🌙 Women of the Voyage",
        "button": "🌙 Women",
        "art_url": CARD_ART_URLS["woman"],
        "entries": [
            ("Cards", "Sirens, Calypso, and Circe."),
            ("Power", f"Beats 🧭 Odysseus cards and gains +{CAPTURE_BONUS} for each captured Odysseus. Loses to ⛵ Crew Member cards."),
            ("Circe", f"Circe overrules Odysseus and Crew Member cards, gaining +{CAPTURE_BONUS} for each captured character."),
        ],
    },
    {
        "title": "⛵ Crew Member Cards",
        "button": "⛵ Crew",
        "art_url": CARD_ART_URLS["crew"],
        "entries": [
            ("Cards", "Eurylochus, Polites, Elpenor, and Argos."),
            ("Power", f"Beats 🌙 Women of the Voyage cards and gains +{CAPTURE_BONUS} for each captured Woman. Loses to 🧭 Odysseus cards."),
            ("Argos", "If Argos is defeated by Odysseus, Argos' player gains +10 bonus."),
        ],
    },
    {
        "title": "Named Character Cards",
        "button": "Characters",
        "cards": [
            lambda: VoyageCard("dict-nobody", "Nobody", CardKind.ODYSSEUS),
            lambda: VoyageCard("dict-strategist", "Strategist", CardKind.ODYSSEUS),
            lambda: VoyageCard("dict-trickster", "Trickster", CardKind.ODYSSEUS),
            lambda: VoyageCard("dict-king", "King", CardKind.ODYSSEUS),
            lambda: VoyageCard("dict-eurylochus", "Eurylochus", CardKind.CREW),
            lambda: VoyageCard("dict-polites", "Polites", CardKind.CREW),
            lambda: VoyageCard("dict-elpenor", "Elpenor", CardKind.CREW),
            lambda: VoyageCard("dict-argos", "Argos", CardKind.CREW),
            lambda: VoyageCard("dict-sirens", "Sirens", CardKind.WOMAN),
            lambda: VoyageCard("dict-calypso", "Calypso", CardKind.WOMAN),
            lambda: VoyageCard("dict-circe", "Circe", CardKind.WOMAN),
        ],
        "entries": [
            ("🧭 Odysseus", "Nobody, Strategist, Trickster, King. Only one appears each round."),
            ("⛵ Crew Member", "Eurylochus, Polites, Elpenor, Argos."),
            ("🌙 Women of the Voyage", "Sirens, Calypso, Circe."),
        ],
    },
    {
        "title": "Divine Cards (Epic Only)",
        "button": "Divine",
        "cards": [
            lambda: VoyageCard("dict-poseidon", "Poseidon's Wrath", CardKind.DIVINE),
            lambda: VoyageCard("dict-tiresias", "Tiresias' Vision", CardKind.DIVINE),
            lambda: VoyageCard("dict-hermes", "Gift of Hermes", CardKind.DIVINE),
            lambda: VoyageCard("dict-athena", "Athena's Protection", CardKind.DIVINE),
        ],
        "entries": [
            ("Availability", "Divine cards only appear in Epic Mode."),
            ("Poseidon's Wrath", "Beats any 🧭 Odysseus card, including Nobody."),
            ("Tiresias' Vision", "In Epic Mode, adjusts prophecy by +1 or -1."),
            ("Gift of Hermes", "In Epic Mode, may replace the played card before trick resolution."),
            ("Athena's Protection", "In Epic Mode, returns itself and draws a replacement after losing."),
        ],
    },
]


@dataclass(frozen=True)
class VoyageCard:
    id: str
    name: str
    kind: CardKind
    suit: Optional[Suit] = None
    rank: int = 0

    @property
    def category(self) -> Optional[str]:
        return SPECIAL_CATEGORY.get(self.kind)

    @property
    def is_circe(self) -> bool:
        return self.kind is CardKind.WOMAN and self.name == "Circe"

    @property
    def is_poseidon(self) -> bool:
        return self.kind is CardKind.DIVINE and self.name == "Poseidon's Wrath"

    @property
    def is_gift(self) -> bool:
        return self.kind is CardKind.DIVINE and self.name == "Gift of Hermes"

    @property
    def is_athena(self) -> bool:
        return self.kind is CardKind.DIVINE and self.name == "Athena's Protection"

    @property
    def is_tiresias(self) -> bool:
        return self.kind is CardKind.DIVINE and self.name == "Tiresias' Vision"

    def short_label(self, mode: GameMode = GameMode.VOYAGE, reveal: bool = True) -> str:
        if mode is GameMode.EPIC and self.name == "Nobody" and not reveal:
            return "💨 Wind Bag"
        if self.kind is CardKind.NUMBERED and self.suit:
            return f"{self.suit.emoji} {self.rank}"
        if self.kind is CardKind.WIND:
            return "💨 Wind Bag"
        if self.kind is CardKind.CREW:
            return f"{self.name} (crew)"
        if self.kind is CardKind.ODYSSEUS:
            return f"{self.name} (ody)"
        if self.kind is CardKind.WOMAN:
            return f"{self.name} (women)"
        return self.name

    def sort_key(self) -> tuple[int, str, int, str]:
        suit_order = {
            None: 0,
            Suit.SEA: 1,
            Suit.WAR: 2,
            Suit.JOURNEY: 3,
            Suit.OLYMPUS: 4,
        }
        kind_order = {
            CardKind.WIND: 0,
            CardKind.NUMBERED: 1,
            CardKind.CREW: 2,
            CardKind.WOMAN: 3,
            CardKind.ODYSSEUS: 4,
            CardKind.DIVINE: 5,
        }
        return (
            kind_order[self.kind],
            self.suit.label if self.suit else "",
            self.rank,
            self.name,
        )


FONT_5X7 = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10011", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "'": ("00100", "00100", "01000", "00000", "00000", "00000", "00000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
}


class SimplePng:
    def __init__(self, width: int, height: int, bg: tuple[int, int, int]):
        self.width = width
        self.height = height
        self.pixels = bytearray(bg * width * height)

    def rect(self, left: int, top: int, right: int, bottom: int, color: tuple[int, int, int]):
        left = max(0, left)
        top = max(0, top)
        right = min(self.width, right)
        bottom = min(self.height, bottom)
        for y in range(top, bottom):
            row = y * self.width * 3
            for x in range(left, right):
                offset = row + x * 3
                self.pixels[offset:offset + 3] = bytes(color)

    def border(self, left: int, top: int, right: int, bottom: int, color: tuple[int, int, int], width: int = 3):
        self.rect(left, top, right, top + width, color)
        self.rect(left, bottom - width, right, bottom, color)
        self.rect(left, top, left + width, bottom, color)
        self.rect(right - width, top, right, bottom, color)

    def text_width(self, text: str, scale: int) -> int:
        return len(text) * 6 * scale

    def text(self, text: str, x: int, y: int, scale: int, color: tuple[int, int, int], centered_width: Optional[int] = None):
        text = "".join(ch for ch in text.upper() if ch == " " or ch in FONT_5X7)
        if centered_width is not None:
            x += max(0, (centered_width - self.text_width(text, scale)) // 2)
        cursor = x
        for char in text:
            if char == " ":
                cursor += 6 * scale
                continue
            pattern = FONT_5X7.get(char)
            if not pattern:
                cursor += 6 * scale
                continue
            for row_index, row in enumerate(pattern):
                for col_index, pixel in enumerate(row):
                    if pixel == "1":
                        self.rect(
                            cursor + col_index * scale,
                            y + row_index * scale,
                            cursor + (col_index + 1) * scale,
                            y + (row_index + 1) * scale,
                            color,
                        )
            cursor += 6 * scale

    def wrapped_text(self, text: str, left: int, top: int, width: int, scale: int, color: tuple[int, int, int], max_lines: int = 3):
        words = text.upper().replace("'", "").split()
        lines = []
        current = ""
        max_chars = max(1, width // (6 * scale))
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word[:max_chars]
        if current:
            lines.append(current)
        for index, line in enumerate(lines[:max_lines]):
            self.text(line, left, top + index * 9 * scale, scale, color, centered_width=width)

    def png_bytes(self) -> bytes:
        raw = bytearray()
        stride = self.width * 3
        for y in range(self.height):
            raw.append(0)
            raw.extend(self.pixels[y * stride:(y + 1) * stride])
        compressed = zlib.compress(bytes(raw), 9)

        def chunk(kind: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data))
                + kind
                + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
            )

        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", compressed)
            + chunk(b"IEND", b"")
        )


def _card_display(card: VoyageCard, mode: GameMode, reveal: bool = True) -> tuple[str, str, tuple[int, int, int]]:
    if mode is GameMode.EPIC and card.name == "Nobody" and not reveal:
        return "Wind Bag", "Hidden", SUIT_COLORS["wind"]
    if card.kind is CardKind.NUMBERED and card.suit:
        return str(card.rank), card.suit.label, SUIT_COLORS[card.suit.value[0]]
    if card.kind is CardKind.WIND:
        return "Wind", "Bag", SUIT_COLORS["wind"]
    if card.kind is CardKind.DIVINE:
        return card.name, "Divine", SUIT_COLORS["olympus"]
    category = CHARACTER_CATEGORY_LABELS.get(card.category, "Special")
    return card.name, category, SUIT_COLORS["special"]


def render_card_art(cards: list[VoyageCard], mode: GameMode, reveal: bool = True) -> io.BytesIO:
    cards = cards or [VoyageCard(id="empty", name="Empty", kind=CardKind.WIND)]
    columns = min(CARD_COLUMNS, len(cards))
    rows = (len(cards) + columns - 1) // columns
    width = columns * CARD_WIDTH + (columns + 1) * CARD_GAP
    height = rows * CARD_HEIGHT + (rows + 1) * CARD_GAP
    image = SimplePng(width, height, (31, 37, 44))

    for index, card in enumerate(cards):
        row, column = divmod(index, columns)
        x = CARD_GAP + column * (CARD_WIDTH + CARD_GAP)
        y = CARD_GAP + row * (CARD_HEIGHT + CARD_GAP)
        title, subtitle, accent = _card_display(card, mode, reveal=reveal)
        subtitle_scale = 1 if len(subtitle) > 11 else 2
        image.rect(x, y, x + CARD_WIDTH, y + CARD_HEIGHT, CARD_BG)
        image.border(x, y, x + CARD_WIDTH, y + CARD_HEIGHT, CARD_BORDER, width=4)
        image.rect(x + 10, y + 10, x + CARD_WIDTH - 10, y + 46, accent)
        image.text(subtitle, x + 12, y + 20, subtitle_scale, (255, 255, 255), centered_width=CARD_WIDTH - 24)
        if card.kind is CardKind.NUMBERED:
            image.text(title, x + 20, y + 80, 8, CARD_INK, centered_width=CARD_WIDTH - 40)
            image.text(subtitle, x + 18, y + 156, 2, CARD_INK, centered_width=CARD_WIDTH - 36)
        else:
            image.wrapped_text(title, x + 14, y + 78, CARD_WIDTH - 28, 2, CARD_INK)
            image.text(subtitle, x + 18, y + 160, 2, CARD_INK, centered_width=CARD_WIDTH - 36)
        label = card.short_label(mode, reveal=reveal).replace("💨 ", "").replace("⚡", "").replace("🌊", "").replace("⚔", "").replace("🏺", "")
        image.wrapped_text(label, x + 14, y + 194, CARD_WIDTH - 28, 1, CARD_BORDER, max_lines=2)

    buffer = io.BytesIO()
    buffer.write(image.png_bytes())
    buffer.seek(0)
    return buffer


def card_art_file(cards: list[VoyageCard], mode: GameMode, filename: str, reveal: bool = True) -> discord.File:
    return discord.File(render_card_art(cards, mode, reveal=reveal), filename=filename)


def card_dictionary_page(index: int) -> tuple[discord.Embed, Optional[discord.File]]:
    section = CARD_DICTIONARY[index]
    filename = f"odyssey_card_dictionary_{index + 1}.png"
    embed = discord.Embed(
        title=f"Odyssey's Voyage Card Dictionary",
        description=f"**{section['title']}**",
        color=ODYSSEY_COLOR,
    )
    for name, value in section["entries"]:
        embed.add_field(name=name, value=value, inline=False)
    embed.set_footer(text=f"Category {index + 1}/{len(CARD_DICTIONARY)}")
    if "art_url" in section:
        embed.set_image(url=section["art_url"])
        return embed, None
    cards = [factory() for factory in section["cards"]]
    embed.set_image(url=f"attachment://{filename}")
    return embed, card_art_file(cards, GameMode.VOYAGE, filename)


class CardDictionaryCategoryButton(discord.ui.Button):
    def __init__(self, index: int):
        super().__init__(
            label=CARD_DICTIONARY[index]["button"],
            style=discord.ButtonStyle.secondary,
            row=1 + (index // 5),
        )
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if isinstance(view, CardDictionaryView):
            await view.show_page(interaction, self.index)


class CardDictionaryView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.index = 0
        self.message: Optional[discord.Message] = None
        self.prev_button = discord.ui.Button(label="← Previous", style=discord.ButtonStyle.primary, row=0)
        self.next_button = discord.ui.Button(label="Next →", style=discord.ButtonStyle.primary, row=0)
        self.prev_button.callback = self.previous_page
        self.next_button.callback = self.next_page
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        for index in range(len(CARD_DICTIONARY)):
            self.add_item(CardDictionaryCategoryButton(index))
        self.sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This card dictionary menu isn't yours.", ephemeral=True)
            return False
        return True

    def sync_buttons(self):
        for child in self.children:
            if isinstance(child, CardDictionaryCategoryButton):
                child.style = (
                    discord.ButtonStyle.success
                    if child.index == self.index
                    else discord.ButtonStyle.secondary
                )
        self.prev_button.disabled = False
        self.next_button.disabled = False

    async def previous_page(self, interaction: discord.Interaction):
        await self.show_page(interaction, (self.index - 1) % len(CARD_DICTIONARY))

    async def next_page(self, interaction: discord.Interaction):
        await self.show_page(interaction, (self.index + 1) % len(CARD_DICTIONARY))

    async def show_page(self, interaction: discord.Interaction, index: int):
        self.index = index
        self.sync_buttons()
        embed, file = card_dictionary_page(index)
        await interaction.response.edit_message(
            embed=embed,
            attachments=[file] if file else [],
            view=self,
        )

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


async def send_card_dictionary(ctx: commands.Context):
    view = CardDictionaryView(ctx.author.id)
    embed, file = card_dictionary_page(view.index)
    if file:
        view.message = await ctx.send(embed=embed, file=file, view=view)
    else:
        view.message = await ctx.send(embed=embed, view=view)


@dataclass
class PlayedCard:
    player_id: int
    player_name: str
    card: VoyageCard


@dataclass
class TrickResult:
    winner: PlayedCard
    reason: str
    revealed: set[int] = field(default_factory=set)


def numbered_limit(player_count: int) -> int:
    if player_count <= 5:
        return 14
    if player_count <= 7:
        return 16
    if player_count <= 9:
        return 18
    return 20


def numbered_cards(player_count: int, suffix: str = "") -> list[VoyageCard]:
    limit = numbered_limit(player_count)
    cards: list[VoyageCard] = []
    for suit in Suit:
        for rank in range(1, limit + 1):
            cards.append(
                VoyageCard(
                    id=f"{suit.value[0]}-{rank}{suffix}",
                    name=f"{suit.label} {rank}",
                    kind=CardKind.NUMBERED,
                    suit=suit,
                    rank=rank,
                )
            )
    return cards


def build_deck(player_count: int, mode: GameMode, cards_needed: int = 0) -> list[VoyageCard]:
    deck: list[VoyageCard] = numbered_cards(player_count)

    odysseus_pool = [
        VoyageCard(id=f"odysseus-{name.lower()}", name=name, kind=CardKind.ODYSSEUS)
        for name in ("Nobody", "Strategist", "Trickster", "King")
    ]
    deck.append(random.choice(odysseus_pool))
    for name in ("Eurylochus", "Polites", "Elpenor", "Argos"):
        deck.append(VoyageCard(id=f"crew-{name.lower()}", name=name, kind=CardKind.CREW))
    for name in ("Sirens", "Calypso", "Circe"):
        deck.append(VoyageCard(id=f"woman-{name.lower()}", name=name, kind=CardKind.WOMAN))

    if mode is GameMode.EPIC:
        divine_pool = [
            VoyageCard(id="divine-poseidon", name="Poseidon's Wrath", kind=CardKind.DIVINE),
            VoyageCard(id="divine-tiresias", name="Tiresias' Vision", kind=CardKind.DIVINE),
            VoyageCard(id="divine-hermes", name="Gift of Hermes", kind=CardKind.DIVINE),
            VoyageCard(id="divine-athena", name="Athena's Protection", kind=CardKind.DIVINE),
        ]
        deck.extend(random.sample(divine_pool, 2))

    wind_count = max(4, min(player_count * 2, 12))
    for index in range(wind_count):
        deck.append(VoyageCard(id=f"wind-{index + 1}", name="Wind Bag", kind=CardKind.WIND))

    copy_index = 1
    while len(deck) < cards_needed:
        extras = numbered_cards(player_count, suffix=f"-extra-{copy_index}")
        random.shuffle(extras)
        deck.extend(extras[: cards_needed - len(deck)])
        copy_index += 1

    random.shuffle(deck)
    return deck


def led_numbered_suit(played: list[PlayedCard]) -> Optional[Suit]:
    for play in played:
        if play.card.kind is CardKind.NUMBERED:
            return play.card.suit
    return None


def is_legal_play(
    hand: list[VoyageCard],
    card: VoyageCard,
    led_suit: Optional[Suit],
    trump_suit: Suit,
) -> bool:
    if card not in hand:
        return False
    if card.kind is not CardKind.NUMBERED:
        return True
    if led_suit is None:
        return True
    has_led_suit = any(c.kind is CardKind.NUMBERED and c.suit is led_suit for c in hand)
    has_trump = any(c.kind is CardKind.NUMBERED and c.suit is trump_suit for c in hand)
    if has_led_suit:
        return card.kind is CardKind.NUMBERED and card.suit is led_suit
    if has_trump:
        return card.kind is CardKind.NUMBERED and card.suit is trump_suit
    return True


def captured_character_cards(winner: PlayedCard, played: list[PlayedCard]) -> list[PlayedCard]:
    winner_category = winner.card.category
    if winner_category is None:
        return []
    captured = []
    for play in played:
        if play.player_id == winner.player_id:
            continue
        category = play.card.category
        if category is None:
            continue
        if winner.card.is_circe and category in {"crew", "odysseus"}:
            captured.append(play)
        elif COUNTERS[winner_category] == category:
            captured.append(play)
    return captured


def captured_rank_14_bonus_cards(played: list[PlayedCard]) -> list[VoyageCard]:
    return [
        play.card
        for play in played
        if play.card.kind is CardKind.NUMBERED and play.card.rank == 14
    ]


def rank_14_bonus(card: VoyageCard) -> int:
    if card.kind is not CardKind.NUMBERED or card.rank != 14:
        return 0
    return 20 if card.suit is Suit.OLYMPUS else 10


def resolve_trick(
    played: list[PlayedCard],
    trump_suit: Suit = Suit.OLYMPUS,
    mode: GameMode = GameMode.VOYAGE,
) -> TrickResult:
    """Resolve a trick with Odyssey's order-based special counter loop."""
    if not played:
        raise ValueError("Cannot resolve an empty trick.")

    odysseus_present = any(play.card.kind is CardKind.ODYSSEUS for play in played)
    for play in played:
        if play.card.is_poseidon and odysseus_present:
            revealed = {p.player_id for p in played if p.card.name == "Nobody"}
            return TrickResult(play, "Poseidon's Wrath punishes Odysseus.", revealed=revealed)

    for play in played:
        if play.card.is_circe:
            return TrickResult(play, "Circe overrules ordinary specials and numbered cards.")

    active_special: Optional[PlayedCard] = None
    for play in played:
        category = play.card.category
        if category is None:
            continue
        if active_special is None:
            active_special = play
            continue
        active_category = active_special.card.category
        if active_category and COUNTERS[category] == active_category:
            active_special = play
    if active_special is not None:
        revealed = set()
        if mode is GameMode.EPIC and active_special.card.name == "Nobody":
            revealed.add(active_special.player_id)
        return TrickResult(
            active_special,
            f"{active_special.card.short_label(mode)} wins through the special counter order.",
            revealed=revealed,
        )

    numbered = [play for play in played if play.card.kind is CardKind.NUMBERED]
    if numbered:
        trump_cards = [play for play in numbered if play.card.suit is trump_suit]
        if trump_cards:
            winner = max(trump_cards, key=lambda play: play.card.rank)
            return TrickResult(winner, f"Highest {trump_suit.display()} trump wins.")
        led_suit = led_numbered_suit(played)
        led_cards = [play for play in numbered if play.card.suit is led_suit]
        winner = max(led_cards, key=lambda play: play.card.rank)
        return TrickResult(winner, f"Highest {led_suit.display()} card wins.")

    return TrickResult(played[0], "Every card was a Wind Bag or inactive intervention; first played wins.")


def rules_embed(mode: GameMode = GameMode.VOYAGE) -> discord.Embed:
    embed = discord.Embed(
        title="Odyssey's Voyage Rules",
        description=(
            "A configurable trick-taking voyage. Each round you predict your fate, "
            "then try to win exactly that many tricks."
        ),
        color=ODYSSEY_COLOR,
    )
    embed.add_field(
        name="How to Play",
        value=(
            f"- {MIN_PLAYERS}-{MAX_PLAYERS} players join the lobby.\n"
            "- The host starts the game.\n"
            f"- Games default to {DEFAULT_ROUNDS} rounds and can be set from {MIN_ROUNDS}-{MAX_ROUNDS} rounds.\n"
            f"- Round 1 deals {STARTING_HAND_SIZE} cards each, then each later round deals 1 more card.\n"
            "- If every player has only one card for the final trick, it plays automatically.\n"
            "- At the start of each round, privately choose your Prophecy.\n"
            "- Prophecy means how many tricks you think you will win that round."
        ),
        inline=False,
    )
    embed.add_field(
        name="Numbered Cards",
        value=(
            "- Suits are Olympus, Sea, War, and Journey.\n"
            "- Each suit runs from rank 1 to rank 14.\n"
            "- Olympus is trump unless Epic Mode Trickster changes it.\n"
            "- For numbered suit cards: follow the led suit if you can; otherwise play trump if you have it; otherwise play any suit.\n"
            "- If no special card decides the trick, highest trump wins; otherwise highest led suit wins."
        ),
        inline=False,
    )
    embed.add_field(
        name="Special Cards",
        value=(
            "- Wind Bags usually lose. If everyone plays Wind Bags, the first one wins.\n"
            "- Wind Bags, character cards, and Epic-only Divine cards can be played at any time.\n"
            "- Only one Odysseus card appears each round.\n"
            "- Crew beats Women, Women beat Odysseus, Odysseus beats Crew by play order.\n"
            "- Circe beats Odysseus, Crew, and numbered cards.\n"
            "- Poseidon's Wrath beats any Odysseus card, including Nobody."
        ),
        inline=False,
    )
    embed.add_field(
        name="Scoring",
        value=(
            "- Exact nonzero Prophecy: +20 points per predicted trick.\n"
            "- Exact zero Prophecy: +10 times the round number.\n"
            "- Missed Prophecy: -10 points per trick difference.\n"
            f"- Character capture: +{CAPTURE_BONUS} for each countered character card captured by the trick winner.\n"
            "- Any rank 14 winning a trick: +10 bonus; Olympus 14: +20 bonus.\n"
            "- Argos defeated by Odysseus: +10 bonus to Argos' player.\n"
            "- Epic Mode King winning a trick: +20 bonus."
        ),
        inline=False,
    )
    if mode is GameMode.EPIC:
        embed.add_field(
            name="Epic Mode",
            value=(
                "- Nobody appears privately as a Wind Bag until revealed by winning or Poseidon.\n"
                "- Strategist may exchange one hand card with one won-pile card after winning.\n"
                "- Trickster chooses a new trump suit for the round when played.\n"
                "- Gift of Hermes may replace a played card before trick resolution.\n"
                "- Athena's Protection returns itself and draws a replacement after losing."
            ),
            inline=False,
        )
    embed.set_footer(text="Use the lobby buttons to join, leave, start, cancel, or reopen these rules.")
    return embed


class RulesHelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Voyage Rules", style=discord.ButtonStyle.primary)
    async def voyage_rules(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_message(embed=rules_embed(GameMode.VOYAGE), ephemeral=True)

    @discord.ui.button(label="Epic Rules", style=discord.ButtonStyle.secondary)
    async def epic_rules(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_message(embed=rules_embed(GameMode.EPIC), ephemeral=True)


class ProphecyButton(discord.ui.Button):
    def __init__(self, value: int):
        super().__init__(label=str(value), style=discord.ButtonStyle.primary, row=value // 5)
        self.value = value

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if isinstance(view, ProphecyView):
            await view.choose(interaction, self.value)


class ProphecyView(discord.ui.View):
    def __init__(self, game: "OdysseyVoyageGame", player_id: int):
        super().__init__(timeout=PROPHECY_TIMEOUT)
        self.game = game
        self.player_id = player_id
        for value in range(game.current_hand_size + 1):
            self.add_item(ProphecyButton(value))
        if game.mode is GameMode.EPIC and game.has_card(player_id, "Tiresias' Vision"):
            self.add_item(TiresiasButton(1))
            self.add_item(TiresiasButton(-1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("This prophecy is not yours.", ephemeral=True)
            return False
        return True

    async def choose(self, interaction: discord.Interaction, value: int):
        await self.game.set_prophecy(interaction, value)


class TiresiasButton(discord.ui.Button):
    def __init__(self, delta: int):
        label = "Tiresias +1" if delta > 0 else "Tiresias -1"
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=3)
        self.delta = delta

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not isinstance(view, ProphecyView):
            return
        current = view.game.prophecies.get(interaction.user.id, 0)
        await view.game.set_prophecy(interaction, max(0, min(view.game.current_hand_size, current + self.delta)))


class CardSelect(discord.ui.Select):
    def __init__(self, game: "OdysseyVoyageGame", player_id: int):
        self.game = game
        self.player_id = player_id
        options = []
        led_suit = led_numbered_suit(game.current_trick)
        must_follow = led_suit is not None and any(
            card.kind is CardKind.NUMBERED and card.suit is led_suit
            for card in game.hands[player_id]
        )
        must_trump = led_suit is not None and not must_follow and any(
            card.kind is CardKind.NUMBERED and card.suit is game.trump_suit
            for card in game.hands[player_id]
        )
        for index, card in enumerate(game.hands[player_id]):
            legal = is_legal_play(game.hands[player_id], card, led_suit, game.trump_suit)
            label = card.short_label(game.mode, reveal=False)
            if legal:
                description = "Legal play"
            elif must_follow:
                description = f"Must follow {led_suit.display()}"
            elif must_trump:
                description = f"Must play trump {game.trump_suit.display()}"
            else:
                description = "Illegal play"
            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=str(index),
                    description=description[:100],
                    default=False,
                )
            )
        super().__init__(placeholder="Choose a card to play", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await self.game.play_selected_card(interaction, int(self.values[0]))


class CardPlayView(discord.ui.View):
    def __init__(self, game: "OdysseyVoyageGame", player_id: int):
        super().__init__(timeout=TURN_TIMEOUT)
        self.add_item(CardSelect(game, player_id))


class OpenHandView(discord.ui.View):
    def __init__(self, game: "OdysseyVoyageGame", trick_number: int, player_id: int):
        super().__init__(timeout=TURN_TIMEOUT)
        self.game = game
        self.trick_number = trick_number
        self.player_id = player_id

    @discord.ui.button(label="Open Hand", style=discord.ButtonStyle.primary)
    async def open_hand(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id not in self.game.player_ids:
            await interaction.response.send_message("You are not in this voyage.", ephemeral=True)
            return
        if self.game.phase != "playing":
            await interaction.response.send_message("Cards can only be played during the trick phase.", ephemeral=True)
            return
        if interaction.user.id != self.game.current_player_id:
            await interaction.response.send_message("It is not your turn.", ephemeral=True)
            return
        image_filename = "odyssey_hand.png"
        await interaction.response.send_message(
            embed=self.game.private_hand_embed(interaction.user.id, image_filename=image_filename),
            file=self.game.private_hand_file(interaction.user.id, filename=image_filename),
            view=CardPlayView(self.game, interaction.user.id),
            ephemeral=True,
        )

    async def on_timeout(self):
        if (
            self.game.phase == "playing"
            and self.game.trick_number == self.trick_number
            and self.game.current_player_id == self.player_id
        ):
            await self.game.auto_play_current_player()


class OpenProphecyView(discord.ui.View):
    def __init__(self, game: "OdysseyVoyageGame"):
        super().__init__(timeout=PROPHECY_TIMEOUT)
        self.game = game

    @discord.ui.button(label="Choose Prophecy", style=discord.ButtonStyle.primary)
    async def choose_prophecy(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id not in self.game.player_ids:
            await interaction.response.send_message("Only joined players may choose a prophecy.", ephemeral=True)
            return
        if self.game.phase != "prophecy":
            await interaction.response.send_message("Prophecy choices are closed.", ephemeral=True)
            return
        if interaction.user.id in self.game.prophecies:
            await interaction.response.send_message("You already locked your prophecy.", ephemeral=True)
            return
        image_filename = "odyssey_prophecy_hand.png"
        await interaction.response.send_message(
            embed=self.game.private_hand_embed(
                interaction.user.id,
                title="Choose your Prophecy",
                image_filename=image_filename,
            ),
            file=self.game.private_hand_file(interaction.user.id, filename=image_filename),
            view=ProphecyView(self.game, interaction.user.id),
            ephemeral=True,
        )

    async def on_timeout(self):
        if self.game.phase != "prophecy":
            return
        for player in self.game.players:
            self.game.prophecies.setdefault(player.id, 0)
        await self.game.ctx.send("AFK prophecy choices defaulted to **0** after 60 seconds.")
        await self.game.begin_trick()


class TrumpSelect(discord.ui.Select):
    def __init__(self, game: "OdysseyVoyageGame"):
        self.game = game
        options = [
            discord.SelectOption(label=suit.label, value=suit.value[0], emoji=suit.emoji)
            for suit in Suit
        ]
        super().__init__(placeholder="Choose the new trump suit", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected = next(suit for suit in Suit if suit.value[0] == self.values[0])
        self.game.trump_suit = selected
        view = self.view
        if isinstance(view, TrumpChoiceView):
            view.completed = True
            view.stop()
        await interaction.response.send_message(
            f"Trickster bends fate: **{selected.display()}** is trump for the rest of the round.",
            ephemeral=True,
        )
        await self.game.after_card_play()


class TrumpChoiceView(discord.ui.View):
    def __init__(self, game: "OdysseyVoyageGame", player_id: int):
        super().__init__(timeout=TURN_TIMEOUT)
        self.player_id = player_id
        self.completed = False
        self.add_item(TrumpSelect(game))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("Only the Trickster's player can choose trump.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.completed:
            return
        select = next((item for item in self.children if isinstance(item, TrumpSelect)), None)
        if isinstance(select, TrumpSelect):
            await select.game.ctx.send("Trickster timed out; trump remains unchanged.")
            await select.game.after_card_play()


class GiftSelect(discord.ui.Select):
    def __init__(self, game: "OdysseyVoyageGame", player_id: int):
        self.game = game
        self.player_id = player_id
        options = [
            discord.SelectOption(label=card.short_label(game.mode)[:100], value=str(index))
            for index, card in enumerate(game.hands[player_id])
        ]
        super().__init__(placeholder="Replace your played card", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        await self.game.use_gift(interaction, self.player_id, int(self.values[0]))


class GiftView(discord.ui.View):
    def __init__(self, game: "OdysseyVoyageGame", player_id: int):
        super().__init__(timeout=TURN_TIMEOUT)
        self.player_id = player_id
        self.add_item(GiftSelect(game, player_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("This Gift of Hermes choice is not yours.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Keep Played Card", style=discord.ButtonStyle.secondary)
    async def keep(self, interaction: discord.Interaction, _button: discord.ui.Button):
        view_select = next((item for item in self.children if isinstance(item, GiftSelect)), None)
        if isinstance(view_select, GiftSelect):
            view_select.game.gift_pending = None
            await interaction.response.send_message("You kept your played card.", ephemeral=True)
            await view_select.game.resolve_current_trick()


class StrategistSelect(discord.ui.Select):
    def __init__(self, game: "OdysseyVoyageGame", player_id: int, source: str):
        self.game = game
        self.player_id = player_id
        self.source = source
        cards = game.hands[player_id] if source == "hand" else game.won_piles[player_id]
        options = [
            discord.SelectOption(label=card.short_label(game.mode)[:100], value=str(index))
            for index, card in enumerate(cards[:25])
        ]
        super().__init__(
            placeholder="Choose from your hand" if source == "hand" else "Choose from your won pile",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        await self.game.choose_strategist_exchange(interaction, self.player_id, self.source, int(self.values[0]))


class StrategistView(discord.ui.View):
    def __init__(self, game: "OdysseyVoyageGame", player_id: int):
        super().__init__(timeout=TURN_TIMEOUT)
        self.player_id = player_id
        self.add_item(StrategistSelect(game, player_id, "hand"))
        self.add_item(StrategistSelect(game, player_id, "pile"))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("This Strategist exchange is not yours.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Skip Exchange", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, _button: discord.ui.Button):
        view_select = next((item for item in self.children if isinstance(item, StrategistSelect)), None)
        if isinstance(view_select, StrategistSelect):
            view_select.game.strategist_pending = None
            if view_select.game.strategist_event:
                view_select.game.strategist_event.set()
                view_select.game.strategist_event = None
        await interaction.response.send_message("Strategist exchange skipped.", ephemeral=True)


class LobbyView(discord.ui.View):
    def __init__(self, cog: "OdysseysVoyage", game: "OdysseyVoyageGame"):
        super().__init__(timeout=300)
        self.cog = cog
        self.game = game

    @discord.ui.button(label="Help / Rules", style=discord.ButtonStyle.secondary)
    async def rules(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.send_message(embed=rules_embed(self.game.mode), ephemeral=True)

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Join from a server channel.", ephemeral=True)
            return
        message = self.game.add_player(interaction.user)
        await interaction.response.send_message(message, ephemeral=True)
        await self.game.refresh_lobby()

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary)
    async def leave(self, interaction: discord.Interaction, _button: discord.ui.Button):
        message = self.game.remove_player(interaction.user.id)
        await interaction.response.send_message(message, ephemeral=True)
        await self.game.refresh_lobby()

    @discord.ui.button(label="Start", style=discord.ButtonStyle.primary)
    async def start(self, interaction: discord.Interaction, _button: discord.ui.Button):
        is_gm_user = await is_game_master(self.cog.bot, interaction.user)
        if interaction.user.id != self.game.host_id and not is_gm_user:
            await interaction.response.send_message("Only the host/GM may start the voyage.", ephemeral=True)
            return
        if len(self.game.players) < MIN_PLAYERS:
            await interaction.response.send_message(
                f"Odyssey's Voyage needs at least {MIN_PLAYERS} players.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message("The voyage begins.", ephemeral=True)
        await self.game.start()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.game.host_id:
            await interaction.response.send_message("Only the host may cancel this voyage.", ephemeral=True)
            return
        await interaction.response.send_message("Odyssey's Voyage has been cancelled.", ephemeral=True)
        await self.game.cancel("Odyssey's Voyage was cancelled by the host.")

    async def on_timeout(self):
        if self.game.phase == "lobby":
            self.cog.games.pop(self.game.channel_id, None)
            await self.game.disable_lobby("Odyssey's Voyage lobby expired.")


class OdysseyVoyageGame:
    def __init__(
        self,
        cog: "OdysseysVoyage",
        ctx: commands.Context,
        host: discord.Member,
        mode: GameMode,
        total_rounds: int = DEFAULT_ROUNDS,
    ):
        self.cog = cog
        self.ctx = ctx
        self.channel_id = ctx.channel.id
        self.host_id = host.id
        self.mode = mode
        self.total_rounds = total_rounds
        self.players: list[discord.Member] = [host]
        self.phase = "lobby"
        self.round_number = 0
        self.trick_number = 0
        self.trump_suit = Suit.OLYMPUS
        self.deck: list[VoyageCard] = []
        self.hands: dict[int, list[VoyageCard]] = {}
        self.prophecies: dict[int, int] = {}
        self.tricks_won: dict[int, int] = {}
        self.scores: dict[int, int] = {host.id: 0}
        self.round_bonuses: dict[int, int] = {}
        self.round_bonus_details: dict[int, list[str]] = {}
        self.won_piles: dict[int, list[VoyageCard]] = {}
        self.current_trick: list[PlayedCard] = []
        self.leader_index = 0
        self.turn_index = 0
        self.message: Optional[discord.Message] = None
        self.lock = asyncio.Lock()
        self.gift_pending: Optional[int] = None
        self.strategist_pending: Optional[dict[str, int]] = None
        self.strategist_event: Optional[asyncio.Event] = None

    @property
    def player_ids(self) -> set[int]:
        return {player.id for player in self.players}

    @property
    def current_player_id(self) -> int:
        return self.players[self.turn_index].id

    @property
    def current_hand_size(self) -> int:
        return STARTING_HAND_SIZE + self.round_number - 1

    def player_for(self, player_id: int) -> discord.Member:
        return next(player for player in self.players if player.id == player_id)

    def add_player(self, member: discord.Member) -> str:
        if self.phase != "lobby":
            return "This voyage has already left Ithaca."
        if member.id in self.player_ids:
            return "You are already aboard."
        if len(self.players) >= MAX_PLAYERS:
            return f"This voyage is full at {MAX_PLAYERS} players."
        self.players.append(member)
        self.scores.setdefault(member.id, 0)
        return "You joined Odyssey's Voyage."

    def remove_player(self, player_id: int) -> str:
        if self.phase != "lobby":
            return "You cannot leave after the voyage starts."
        if player_id == self.host_id:
            return "The host cannot leave; let the lobby expire or start a new channel."
        before = len(self.players)
        self.players = [player for player in self.players if player.id != player_id]
        return "You left Odyssey's Voyage." if len(self.players) != before else "You were not aboard."

    def lobby_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Odyssey's Voyage",
            description=(
                f"Mode: **{self.mode.value.title()}**\n"
                f"Rounds: **{self.total_rounds}** | Starting hand: **{STARTING_HAND_SIZE} cards**\n"
                f"Players: **{len(self.players)}/{MAX_PLAYERS}**\n"
                f"Host: <@{self.host_id}>"
            ),
            color=ODYSSEY_COLOR,
        )
        embed.add_field(
            name="Crew",
            value="\n".join(f"{index}. {player.mention}" for index, player in enumerate(self.players, 1)),
            inline=False,
        )
        return embed

    async def refresh_lobby(self):
        if self.message:
            await self.message.edit(embed=self.lobby_embed())

    async def disable_lobby(self, content: str):
        if not self.message:
            return
        view = LobbyView(self.cog, self)
        for child in view.children:
            child.disabled = True
        await self.message.edit(content=content, view=view)

    async def cancel(self, content: str):
        self.phase = "cancelled"
        self.cog.games.pop(self.channel_id, None)
        await self.disable_lobby(content)

    def has_card(self, player_id: int, name: str) -> bool:
        return any(card.name == name for card in self.hands.get(player_id, []))

    async def start(self):
        self.phase = "starting"
        if self.message:
            view = LobbyView(self.cog, self)
            for child in view.children:
                child.disabled = True
            await self.message.edit(embed=self.lobby_embed(), view=view)
        for player in self.players:
            self.scores.setdefault(player.id, 0)
        await self.start_round()

    async def start_round(self):
        self.round_number += 1
        self.trick_number = 0
        self.phase = "prophecy"
        self.trump_suit = Suit.OLYMPUS
        self.prophecies = {}
        self.tricks_won = {player.id: 0 for player in self.players}
        self.round_bonuses = {player.id: 0 for player in self.players}
        self.round_bonus_details = {player.id: [] for player in self.players}
        self.won_piles = {player.id: [] for player in self.players}
        self.current_trick = []

        hand_size = self.current_hand_size
        needed = len(self.players) * hand_size
        self.deck = build_deck(len(self.players), self.mode, cards_needed=needed)
        self.hands = {player.id: [] for player in self.players}
        for _ in range(hand_size):
            for player in self.players:
                self.hands[player.id].append(self.deck.pop())
        for hand in self.hands.values():
            hand.sort(key=lambda card: card.sort_key())

        embed = self.public_state_embed(
            "Round Start",
            f"Round **{self.round_number}/{self.total_rounds}**. "
            f"Cards this round: **{hand_size}**. Choose your private prophecy.",
        )
        await self.ctx.send(
            content=f"{self.player_mentions()} choose your prophecy.",
            embed=embed,
            view=OpenProphecyView(self),
        )

    async def set_prophecy(self, interaction: discord.Interaction, value: int):
        if self.phase != "prophecy":
            await interaction.response.send_message("Prophecy choices are closed.", ephemeral=True)
            return
        self.prophecies[interaction.user.id] = value
        await interaction.response.send_message(f"Prophecy locked at **{value}**.", ephemeral=True)
        if len(self.prophecies) == len(self.players):
            await self.begin_trick()

    async def begin_trick(self):
        self.phase = "playing"
        self.trick_number += 1
        self.current_trick = []
        self.turn_index = self.leader_index
        if self.final_trick_has_no_choices():
            await self.play_final_trick_automatically()
            return
        embed = self.public_state_embed(
            f"Round {self.round_number}, Trick {self.trick_number}",
            f"{self.players[self.turn_index].mention} leads. Trump: **{self.trump_suit.display()}**",
        )
        await self.ctx.send(
            content=f"{self.players[self.turn_index].mention}, it is your turn.",
            embed=embed,
            view=OpenHandView(self, self.trick_number, self.current_player_id),
        )

    def final_trick_has_no_choices(self) -> bool:
        return (
            self.trick_number >= self.current_hand_size
            and all(len(self.hands.get(player.id, [])) == 1 for player in self.players)
        )

    async def play_final_trick_automatically(self):
        played_lines = []
        for _ in range(len(self.players)):
            player = self.players[self.turn_index]
            hand = self.hands[player.id]
            card = hand.pop(0)
            self.current_trick.append(PlayedCard(player.id, player.display_name, card))
            played_lines.append(f"{player.mention}: **{card.short_label(self.mode)}**")
            self.turn_index = (self.turn_index + 1) % len(self.players)

        embed = self.public_state_embed(
            "Final Trick Auto-Played",
            "Everyone had one card left, so the final trick was played automatically.",
        )
        embed.add_field(name="Auto-Played Cards", value="\n".join(played_lines), inline=False)
        await self.ctx.send(embed=embed)
        await self.resolve_current_trick()

    def player_mentions(self) -> str:
        return " ".join(player.mention for player in self.players)

    def public_state_embed(self, title: str, description: str) -> discord.Embed:
        embed = discord.Embed(title=f"Odyssey's Voyage: {title}", description=description, color=ODYSSEY_COLOR)
        status_lines = []
        for player in self.players:
            prophecy = self.prophecies.get(player.id, "?")
            status_lines.append(
                f"{player.mention}: {self.scores.get(player.id, 0)} pts | "
                f"Prophecy {prophecy} | Tricks {self.tricks_won.get(player.id, 0)}"
            )
        embed.add_field(name="Voyagers", value="\n".join(status_lines), inline=False)
        if self.current_trick:
            played_lines = []
            for play in self.current_trick:
                reveal = play.player_id in resolve_trick(self.current_trick, self.trump_suit, self.mode).revealed
                played_lines.append(f"{play.player_name}: **{play.card.short_label(self.mode, reveal=reveal)}**")
            embed.add_field(name="Played Cards", value="\n".join(played_lines), inline=False)
        embed.set_footer(text=f"Mode: {self.mode.value.title()} | Trump: {self.trump_suit.display()}")
        return embed

    def private_hand_embed(
        self,
        player_id: int,
        title: str = "Your Hand",
        image_filename: str = "odyssey_hand.png",
    ) -> discord.Embed:
        embed = discord.Embed(title=title, color=ODYSSEY_COLOR)
        hand = self.hands.get(player_id, [])
        cards = [f"{index}. {card.short_label(self.mode, reveal=False)}" for index, card in enumerate(hand, 1)]
        embed.description = "\n".join(cards) if cards else "Your hand is empty."
        embed.add_field(name="Round", value=str(self.round_number), inline=True)
        embed.add_field(name="Trump", value=self.trump_suit.display(), inline=True)
        if hand:
            embed.set_image(url=f"attachment://{image_filename}")
        return embed

    def private_hand_file(self, player_id: int, filename: str = "odyssey_hand.png") -> Optional[discord.File]:
        hand = self.hands.get(player_id, [])
        if not hand:
            return None
        return card_art_file(hand, self.mode, filename, reveal=False)

    async def play_selected_card(self, interaction: discord.Interaction, index: int):
        async with self.lock:
            if self.phase != "playing":
                await interaction.response.send_message("Cards cannot be played right now.", ephemeral=True)
                return
            if interaction.user.id != self.current_player_id:
                await interaction.response.send_message("It is not your turn.", ephemeral=True)
                return
            hand = self.hands[interaction.user.id]
            if index < 0 or index >= len(hand):
                await interaction.response.send_message("That card is no longer in your hand.", ephemeral=True)
                return
            card = hand[index]
            if not is_legal_play(hand, card, led_numbered_suit(self.current_trick), self.trump_suit):
                await interaction.response.send_message(
                    "For numbered cards, follow the led suit if you can; otherwise play trump if you have it.",
                    ephemeral=True,
                )
                return
            hand.pop(index)
            self.current_trick.append(PlayedCard(interaction.user.id, interaction.user.display_name, card))
            if self.mode is GameMode.EPIC and card.name == "Trickster":
                await interaction.response.send_message(
                    "Trickster played. Choose a new trump suit.",
                    view=TrumpChoiceView(self, interaction.user.id),
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(f"You played **{card.short_label(self.mode)}**.", ephemeral=True)
        await self.after_card_play()

    async def after_card_play(self):
        if len(self.current_trick) < len(self.players):
            self.turn_index = (self.turn_index + 1) % len(self.players)
            await self.ctx.send(
                content=f"{self.players[self.turn_index].mention}, it is your turn.",
                embed=self.public_state_embed(
                    f"Round {self.round_number}, Trick {self.trick_number}",
                    f"{self.players[self.turn_index].mention} is next to play.",
                ),
                view=OpenHandView(self, self.trick_number, self.current_player_id),
            )
            return
        gift_players = [play.player_id for play in self.current_trick if play.card.is_gift and self.hands[play.player_id]]
        if self.mode is GameMode.EPIC and gift_players:
            self.gift_pending = gift_players[0]
            await self.ctx.send(
                f"<@{self.gift_pending}> may use Gift of Hermes before the trick resolves.",
                view=GiftPublicPrompt(self, self.gift_pending),
            )
            return
        await self.resolve_current_trick()

    async def auto_play_current_player(self):
        async with self.lock:
            if self.phase != "playing":
                return
            player = self.players[self.turn_index]
            hand = self.hands[player.id]
            if not hand:
                return
            led_suit = led_numbered_suit(self.current_trick)
            legal_cards = [
                card for card in hand
                if is_legal_play(hand, card, led_suit, self.trump_suit)
            ]
            card = legal_cards[0] if legal_cards else hand[0]
            hand.remove(card)
            self.current_trick.append(PlayedCard(player.id, player.display_name, card))
            await self.ctx.send(f"{player.mention} timed out and played **{card.short_label(self.mode)}** automatically.")
        await self.after_card_play()

    async def use_gift(self, interaction: discord.Interaction, player_id: int, hand_index: int):
        if self.gift_pending != player_id:
            await interaction.response.send_message("Gift of Hermes is not pending for you.", ephemeral=True)
            return
        if hand_index < 0 or hand_index >= len(self.hands[player_id]):
            await interaction.response.send_message("That replacement card is no longer available.", ephemeral=True)
            return
        old_play = next(play for play in self.current_trick if play.player_id == player_id)
        replacement = self.hands[player_id].pop(hand_index)
        self.hands[player_id].append(old_play.card)
        self.hands[player_id].sort(key=lambda card: card.sort_key())
        old_play.card = replacement
        self.gift_pending = None
        await interaction.response.send_message(f"Gift of Hermes replaced your play with **{replacement.short_label(self.mode)}**.", ephemeral=True)
        await self.resolve_current_trick()

    def add_round_bonus(self, player_id: int, amount: int, reason: str):
        self.round_bonuses[player_id] += amount
        self.round_bonus_details.setdefault(player_id, []).append(f"+{amount} {reason}")

    async def resolve_current_trick(self):
        result = resolve_trick(self.current_trick, self.trump_suit, self.mode)
        winner_id = result.winner.player_id
        self.tricks_won[winner_id] += 1
        self.won_piles[winner_id].extend(play.card for play in self.current_trick)
        captured_cards = captured_character_cards(result.winner, self.current_trick)
        if captured_cards:
            captured_names = ", ".join(play.card.short_label(self.mode) for play in captured_cards)
            self.add_round_bonus(winner_id, CAPTURE_BONUS * len(captured_cards), f"captured {captured_names}")

        for play in self.current_trick:
            if play.card.name == "Argos" and result.winner.card.kind is CardKind.ODYSSEUS and result.winner.player_id != play.player_id:
                self.add_round_bonus(play.player_id, 10, "Argos defeated by Odysseus")
            if self.mode is GameMode.EPIC and play.card.is_athena and play.player_id != winner_id:
                self.hands[play.player_id].append(play.card)
                if self.deck:
                    self.hands[play.player_id].append(self.deck.pop())
                self.hands[play.player_id].sort(key=lambda card: card.sort_key())
        rank_14_cards = captured_rank_14_bonus_cards(self.current_trick)
        for card in rank_14_cards:
            self.add_round_bonus(winner_id, rank_14_bonus(card), f"{card.suit.label} 14 captured")
        if self.mode is GameMode.EPIC and result.winner.card.name == "King":
            self.add_round_bonus(winner_id, 20, "King won in Epic Mode")

        embed = self.public_state_embed(
            "Trick Winner",
            f"{result.winner.player_name} wins with **{result.winner.card.short_label(self.mode)}**.\n{result.reason}",
        )
        if captured_cards:
            embed.add_field(
                name="Capture Bonus",
                value=(
                    f"+{CAPTURE_BONUS * len(captured_cards)} for capturing "
                    f"{', '.join(play.card.short_label(self.mode) for play in captured_cards)}"
                ),
                inline=False,
            )
        if rank_14_cards:
            embed.add_field(
                name="Rank 14 Bonus",
                value="\n".join(
                    f"+{rank_14_bonus(card)} for capturing {card.short_label(self.mode)}"
                    for card in rank_14_cards
                ),
                inline=False,
            )
        image_filename = "odyssey_winning_card.png"
        embed.set_image(url=f"attachment://{image_filename}")
        await self.ctx.send(
            embed=embed,
            file=card_art_file([result.winner.card], self.mode, image_filename),
        )

        self.leader_index = next(index for index, player in enumerate(self.players) if player.id == winner_id)
        if self.mode is GameMode.EPIC and result.winner.card.name == "Strategist" and self.hands[winner_id] and self.won_piles[winner_id]:
            self.strategist_pending = {"hand": -1, "pile": -1}
            self.strategist_event = asyncio.Event()
            await self.ctx.send(
                f"<@{winner_id}> may exchange one hand card with one won-pile card.",
                view=StrategistPublicPrompt(self, winner_id),
            )
            try:
                await asyncio.wait_for(self.strategist_event.wait(), timeout=TURN_TIMEOUT)
            except asyncio.TimeoutError:
                self.strategist_pending = None
                self.strategist_event = None

        if self.trick_number >= self.current_hand_size:
            await self.score_round()
        else:
            await self.begin_trick()

    async def choose_strategist_exchange(self, interaction: discord.Interaction, player_id: int, source: str, index: int):
        if self.strategist_pending is None:
            await interaction.response.send_message("No Strategist exchange is pending.", ephemeral=True)
            return
        self.strategist_pending[source] = index
        if self.strategist_pending["hand"] >= 0 and self.strategist_pending["pile"] >= 0:
            hand_index = self.strategist_pending["hand"]
            pile_index = self.strategist_pending["pile"]
            hand = self.hands[player_id]
            pile = self.won_piles[player_id]
            if hand_index < len(hand) and pile_index < len(pile):
                hand[hand_index], pile[pile_index] = pile[pile_index], hand[hand_index]
                hand.sort(key=lambda card: card.sort_key())
                await interaction.response.send_message("Strategist exchange completed.", ephemeral=True)
            else:
                await interaction.response.send_message("One of those cards is no longer available.", ephemeral=True)
            self.strategist_pending = None
            if self.strategist_event:
                self.strategist_event.set()
                self.strategist_event = None
            return
        await interaction.response.send_message("Selection noted. Choose the other card to exchange.", ephemeral=True)

    async def score_round(self):
        lines = []
        for player in self.players:
            prophecy = self.prophecies[player.id]
            won = self.tricks_won[player.id]
            if prophecy == won:
                base_points = 10 * self.current_hand_size if prophecy == 0 else 20 * prophecy
                base_reason = "exact zero" if prophecy == 0 else "exact prophecy"
            else:
                base_points = -10 * abs(prophecy - won)
                base_reason = "missed prophecy"
            bonus_points = self.round_bonuses[player.id]
            points = base_points + bonus_points
            self.scores[player.id] += points
            bonus_details = self.round_bonus_details.get(player.id) or ["none"]
            lines.append(
                f"{player.mention}: prophecy **{prophecy}**, won **{won}**, "
                f"base **{base_points}** ({base_reason}), bonus **{bonus_points}** "
                f"({'; '.join(bonus_details)}), round **{points}**, total **{self.scores[player.id]}**"
            )
        embed = discord.Embed(
            title=f"Odyssey's Voyage: Round {self.round_number} Scoreboard",
            description="\n".join(lines),
            color=ODYSSEY_COLOR,
        )
        await self.ctx.send(embed=embed)
        if self.round_number >= self.total_rounds:
            await self.finish_game()
            return
        await self.start_round()

    async def finish_game(self):
        self.phase = "completed"
        ranking = sorted(self.players, key=lambda player: self.scores[player.id], reverse=True)
        lines = [
            f"{place}. {player.mention}: **{self.scores[player.id]}** points"
            for place, player in enumerate(ranking, 1)
        ]
        await self.ctx.send(
            embed=discord.Embed(
                title="Odyssey's Voyage: Final Ranking",
                description="\n".join(lines),
                color=discord.Color.gold(),
            )
        )
        self.cog.games.pop(self.channel_id, None)


class GiftPublicPrompt(discord.ui.View):
    def __init__(self, game: OdysseyVoyageGame, player_id: int):
        super().__init__(timeout=TURN_TIMEOUT)
        self.game = game
        self.player_id = player_id

    @discord.ui.button(label="Use Gift of Hermes", style=discord.ButtonStyle.primary)
    async def use_gift(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("This Gift of Hermes choice is not yours.", ephemeral=True)
            return
        image_filename = "odyssey_gift_hand.png"
        await interaction.response.send_message(
            embed=self.game.private_hand_embed(
                self.player_id,
                title="Gift of Hermes",
                image_filename=image_filename,
            ),
            file=self.game.private_hand_file(self.player_id, filename=image_filename),
            view=GiftView(self.game, self.player_id),
            ephemeral=True,
        )

    async def on_timeout(self):
        if self.game.gift_pending == self.player_id:
            self.game.gift_pending = None
            await self.game.resolve_current_trick()


class StrategistPublicPrompt(discord.ui.View):
    def __init__(self, game: OdysseyVoyageGame, player_id: int):
        super().__init__(timeout=TURN_TIMEOUT)
        self.game = game
        self.player_id = player_id

    @discord.ui.button(label="Strategist Exchange", style=discord.ButtonStyle.primary)
    async def exchange(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("This Strategist choice is not yours.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Choose one card from your hand and one from your won trick pile.",
            view=StrategistView(self.game, self.player_id),
            ephemeral=True,
        )

    async def on_timeout(self):
        if self.game.strategist_pending is not None:
            self.game.strategist_pending = None
        if self.game.strategist_event:
            self.game.strategist_event.set()
            self.game.strategist_event = None


class OdysseysVoyage(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.games: dict[int, OdysseyVoyageGame] = {}

    @commands.command(name="odysseyvoyage", aliases=["voyage", "ov"])
    async def odyssey_voyage(
        self,
        ctx: commands.Context,
        mode: str = "voyage",
        rounds: Optional[int] = None,
    ):
        """Start an Odyssey's Voyage lobby. Use '$ov epic 5' for Epic Mode."""
        mode_text = mode.lower()
        if mode_text in {"help", "rules", "rule"}:
            await ctx.reply(
                embed=rules_embed(GameMode.VOYAGE),
                view=RulesHelpView(),
            )
            return
        if mode_text in {"cards", "card", "dictionary", "dict"}:
            await send_card_dictionary(ctx)
            return
        if mode_text.isdigit():
            rounds = int(mode_text)
            mode_text = "voyage"
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.reply("Odyssey's Voyage can only be played in a server channel.")
            return
        if ctx.channel.id in self.games:
            await ctx.reply("There is already an Odyssey's Voyage game in this channel.")
            return
        try:
            game_mode = GameMode(mode_text)
        except ValueError:
            await ctx.reply("Unknown mode. Use `voyage`, `epic`, or a round count like `5`.")
            return
        total_rounds = DEFAULT_ROUNDS if rounds is None else rounds
        if total_rounds < MIN_ROUNDS or total_rounds > MAX_ROUNDS:
            await ctx.reply(f"Round count must be between {MIN_ROUNDS} and {MAX_ROUNDS}.")
            return
        game = OdysseyVoyageGame(self, ctx, ctx.author, game_mode, total_rounds=total_rounds)
        self.games[ctx.channel.id] = game
        view = LobbyView(self, game)
        game.message = await ctx.send(embed=game.lobby_embed(), view=view)

    @commands.command(name="odysseyvoyage_cards", aliases=["ovcards", "voyagecards", "odysseycards"])
    async def odyssey_voyage_cards(self, ctx: commands.Context):
        """Show Odyssey's Voyage card powers, matchups, and art."""
        await send_card_dictionary(ctx)

    @commands.command(name="odysseyvoyage_cancel", aliases=["ovcancel"])
    async def cancel_odyssey_voyage(self, ctx: commands.Context):
        """Cancel the Odyssey's Voyage game in this channel."""
        game = self.games.get(ctx.channel.id)
        if game is None:
            await ctx.reply("There is no Odyssey's Voyage game in this channel.")
            return
        if ctx.author.id != game.host_id:
            await ctx.reply("Only the host may cancel this voyage.")
            return
        await game.cancel("Odyssey's Voyage was cancelled by the host.")
        await ctx.reply("Odyssey's Voyage has been cancelled.")


async def setup(bot):
    await bot.add_cog(OdysseysVoyage(bot))
