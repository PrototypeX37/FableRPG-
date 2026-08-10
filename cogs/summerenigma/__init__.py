from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

import discord
from discord.ext import commands, tasks

from classes.badges import Badge
from utils.checks import has_char, is_gm
from utils.i18n import _, locale_doc


EVENT_NAME = "Apollo's Olympic Enigma Hunt"
FINAL_META_ANSWER = "esyeisainikitis"
ENIGMA_EVENT_CHANNEL_ID = 1404885918959140985
ENIGMA_EVENT_ROLE_ID = 1405909212424306729
PHASE_ORDER = ("bronze", "silver", "gold", "olympian")
PHASE_DAYS = {
    "bronze": (1, 2, 3, 4),
    "silver": (5, 6, 7, 8),
    "gold": (9, 10, 11, 12),
    "olympian": (13, 14, 15),
}
PHASE_COOLDOWNS = {
    "bronze": dt.timedelta(hours=24),
    "silver": dt.timedelta(hours=48),
    "gold": dt.timedelta(hours=72),
}
NEXT_PHASE_BY_COMPLETED_PHASE = {
    "bronze": "silver",
    "silver": "gold",
    "gold": "olympian",
}
PREVIOUS_PHASE_BY_NEXT_PHASE = {
    next_phase: completed_phase
    for completed_phase, next_phase in NEXT_PHASE_BY_COMPLETED_PHASE.items()
}
PHASE_UNLOCK_MESSAGES = {
    "silver": (
        "Apollo descends from his chariot. The Silver Gates open for you alone, "
        "for now. New trials await, and they will not yield to brute force."
    ),
    "gold": (
        "The Gold Gates open. Apollo takes up his lyre. What follows is not a "

        "test of knowledge alone; it is a test of listening."
    ),
    "olympian": (
        "The Olympian Gates open for you alone. Apollo descends fully from "
        "Olympus. Three trials remain, and they are not separate. They are one."
    ),
}
PHASE_FIRST_CLEAR_MESSAGES = {
    "bronze": (
        "A mortal has seized the torch and begun the run. The Silver Gates "
        "tremble. Who among you will follow?"
    ),
    "silver": (
        "A champion has passed the Pentathlon. The Gold Gates shudder. The race "
        "is on, mortals."
    ),
    "gold": (
        "One mortal has survived Apollo's Trial. The Olympian Gates crack open. "
        "Who dares follow?"
    ),
    "olympian": (
        "A mortal has answered Apollo's Final Chord. The hunt is complete. The "
        "gods applaud. Who else will claim their place on the scroll?"
    ),
}
SUCCESS_LETTERS = {
    0: "E",
    1: "S",
    2: "Y",
    3: "E",
    4: "I",
    5: "S",
    6: "A",
    7: "I",
    8: "N",
    9: "I",
    10: "K",
    11: "I",
    12: "T",
    13: "I",
    14: "S",
}

DAY9_SHEET_URL = "https://i.imgur.com/jecTq3z.jpeg"
SHARED_ENIGMA_KEY_URLS = (
    "https://i.imgur.com/XvExRlA.jpeg",
    "https://i.imgur.com/f6g61e8.jpeg",
    "https://i.imgur.com/VRFpq7T.jpeg",
    "https://i.imgur.com/eQjBSPW.jpeg",
    "https://i.imgur.com/6evUVlI.jpeg",
)
SUCCESS_MEDIA_URLS = {
    1: (SHARED_ENIGMA_KEY_URLS[0],),
    2: (SHARED_ENIGMA_KEY_URLS[1],),
    3: (SHARED_ENIGMA_KEY_URLS[2],),
    4: (SHARED_ENIGMA_KEY_URLS[3],),
    5: (SHARED_ENIGMA_KEY_URLS[4],),
}
DAY10_AUDIO_URL = "https://drive.google.com/file/d/1dcu7WQOtNL_lTeXkKJ9TdQF1Qhd-RIzA/view?usp=drivesdk"
DAY10_HINT1_URL = "https://drive.google.com/file/d/18_CeVp_sRC3atn1FudnG7Xw3QZgBBuJU/view?usp=drivesdk"
DAY10_HINT2_URL = "https://drive.google.com/file/d/1Tpl6Jot1DWScREuO-8u7Deyx9dSjAReI/view?usp=drivesdk"
DAY11_PICTOGRAM_URL = "https://i.imgur.com/CouqJD0.png"
DAY12_OLD_MESSAGE_URLS = (
    "https://discord.com/channels/1323388333589528638/1413231200721703056/1413832279435902976",
    "https://discord.com/channels/1323388333589528638/1404886208143954122/1447059790239891580",
    "https://discord.com/channels/1323388333589528638/1404885918959140985/1475687569995071538",
)
DAY14_RINGS_URL = "https://i.imgur.com/zSyGGCr.png"


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def normalize_answer(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9\u0370-\u03ff]+", " ", value)
    words = [word for word in value.split() if word not in {"the"}]
    return " ".join(words).strip()


def normalize_compact(value: str) -> str:
    return normalize_answer(value).replace(" ", "")


def success_text(day: int, message: str) -> str:
    return message


@dataclass(frozen=True)
class EnigmaDay:
    day: int
    phase: str
    title: str
    prompt: str
    accepted: tuple[str, ...]
    success: str
    hints: tuple[tuple[int, str], ...]
    ordered: bool = False
    any_three: bool = False
    media_urls: tuple[str, ...] = ()
    hint_media_urls: tuple[str, ...] = ()


DAY_CONTENT: dict[int, EnigmaDay] = {
    1: EnigmaDay(
        1,
        "bronze",
        "The Oracle Speaks",
        """Apollo lays seven riddles beneath the morning sun. Name any three.

1. I am the voice behind every song, the truth behind every prophecy, and the aim behind every perfect shot. Who am I?
2. I have golden wings and hover over battlefields, bestowing wreaths upon the brave. I serve as Zeus's trusted charioteer, yet I own no distinct myths of my own. Who am I?
3. My sister bears wisdom into the fight,
But I arrive with fury's might.
Heroes pray, though few adore.
Who am I at the heart of war?
4. I roam the deep woods where the wild creatures run,
I sleep in the shadows and flee from the sun.
With silver-tipped arrows and bow made of gold,
I fiercely protect the young and the bold.
I dance with my nymphs in the pale lunar light,
But a mortal who spies me is cursed to the night. Who am I?
5. Kings bow before me, storms answer to me, and my weapon can split the sky in two. Though many sit upon thrones, only one sits above the gods. Who am I?
6. I rule the largest kingdom in the world, yet few wish to enter it. My subjects never leave, and my riches lie buried beneath the earth. Who am I?
7. I have no kingdom on earth, sea, or sky. Yet every night I build palaces, wage wars, and tell stories inside your mind. Who am I?""",
        ("apollo", "nike", "ares", "artemis", "zeus", "hades", "morpheus"),
        success_text(1, "Apollo smile**S**. The first torch catches."),
        (
            (12, "Apollo whispers: one answer shines above all others. One flies on wings of gold. One wages war in silence."),
            (24, "The sun god himself is among them. Look to the arts, the light, and the victor's wreath."),
        ),
        any_three=True,
    ),
    2: EnigmaDay(
        2,
        "bronze",
        "The Medalist's Riddles",
        """Four answers. Four podium truths. Submit them in order.

1. I am not born whole. Two strangers meet in flame and history remembers only who remains.
2. This is a type of color, not yellow nor blue. You will require this type of arrow when Lychaon's after you.
3. I've many uses, such as bangles and bribes. On the top of the podium, I'm worn with pride.
4. Git gud kid, what are you?""",
        ("bronze silver gold loser",),
        success_text(2, "Apollo laughs softl**Y**. The medals know your name."),
        (
            (12, "Think of the podium. Three stand upon it. One does not."),
            (24, "The fourth answer requires no riddle. It requires only honesty."),
        ),
        ordered=True,
    ),
    3: EnigmaDay(
        3,
        "bronze",
        "The Lyre's First Chord",
        """Three riddles open the ear. The fourth is your answer.

1. I'm written down but I'm not a letter. I'm read but I'm not a book. I'm played but I'm not a game.
2. I can be sharp, flat, or natural, but I'm not a landscape.
3. I'm heard in every song, but if you remove me completely, nobody notices I'm gone until it's too much.

Apollo's last question: I can speed up a musician's heartbeat, slow down a dancer's feet, and change a listener's mood without saying a single word.

Remember these three elements. A note has a name. That name is a letter. Seven letters hold all of creation's sound.""",
        ("tempo",),
        success_text(3, "The lyr**E** answers your hand."),
        (
            (12, "It is not the note. It is not the pause. It is the heartbeat between them."),
            (24, "A conductor raises their baton. What do the musicians watch for?"),
        ),
    ),
    4: EnigmaDay(
        4,
        "bronze",
        "The Forge of Hephaestus",
        """Three gods stand in smoke, discord, and wine. Name all three in order.

1. I breathe not with lungs, yet I fan the bright spark,
I build the king's weapons and forge in the dark.
With anvil and hammer, I shape the red coal,
A craftsman of power, though broken and whole.

2. A fruit became a promise.
A promise became a journey.
A journey became a fire.

3. Born of the lightning, I'm twice given breath,
I bring you sweet madness, but I can bring death.
I wear not a crown, but a wreath of the vine,
I loosen your tongue and I pour out the wine.""",
        ("hephaestus eris dionysus",),
        success_text(4, "The forge glows br**I**ght for you."),
        (
            (12, "One shapes weapons in fire. One threw an apple that started a war. One pours the wine that loosens all tongues."),
            (24, "The first is lame but mighty. The second is chaos dressed as celebration. The third is born twice."),
        ),
        ordered=True,
    ),
    5: EnigmaDay(
        5,
        "silver",
        "Lost in Translation",
        """The words are broken, but victory has a long memory. Name the original song.

I bowed down
And if you sit down
You have given me glory and honor and riches and everything I have.
Thank you all
But this isn't a boat ride
It wasn't a fun trip
I see this as a challenge for all people.
And I will not fail.
(Smell black, smell black, smell black)""",
        ("we are champions", "champions", "queen champions", "we are the champions"),
        success_text(5, "Victorious echoe**S** survive broken words."),
        (
            (12, "Apollo says: the words are broken but the spirit survives. This song knows victory."),
            (24, "A band. A stadium. A promise that they will not stop fighting. The year was 1977."),
        ),
    ),
    6: EnigmaDay(
        6,
        "silver",
        "The Athlete's Hidden Diary",
        """Dawn broke over the stadium as I laced my sandals.
Apollo himself seemed to watch from the sun.
Perfection, I have learned, is not a destination.
Hours of practice make the impossible feel light.
Nothing silences doubt like the roar of the crowd.
Each race is a new prayer to the gods.

The diary contains a name. Find her.""",
        ("daphne",),
        success_text(6, "D**A**phne's laurel bends toward you."),
        (
            (12, "The diary contains more than training notes. Read the beginning of each thought."),
            (24, "She was a naiad. She fled. She became eternal. Her name runs through the text."),
        ),
    ),
    7: EnigmaDay(
        7,
        "silver",
        "Apollo's Chariot Race",
        """Four answers. One truth hidden within them. Find what lies beneath, and you will have today's offering.

1. I race across the dusty field with no feet of my own, I carry the warrior, the king, and the champion's throne. Pulled by the beasts of the earth, I leave two spinning tracks, and I am guided by the reins that lay upon my hacks.
2. I am stretched and pulled, but I am not a rubber band. I have a string, but I am not a guitar. With a sharp eye and a steady hand, I send a pointed friend to a painted end.
3. I am the secret to running an obstacle course without stumbling. I combine speed, balance, and quick reflexes.
4. I stand between you and your darkest desires. I can make you climb mountains or walk across fire. Though I can be built with discipline and repetition, I am invisible to the naked eye.

Apollo's chariot leaves one spare mark in the dust. Use the letters you have found, but let one unnecessary letter fall away.""",
        ("a torch", "torch"),
        success_text(7, "The hidden flame r**I**ses from the wheel."),
        (
            (12, "The answers themselves are not the answer. Something hides within them, but one letter is only dust from the wheels."),
            (24, "From CHARIOT, remove the letter that does not belong to the Olympic flame. Rearrange what remains."),
        ),
    ),
    8: EnigmaDay(
        8,
        "silver",
        "The Hellenic Medal Cipher",
        """Apollo sets four Greek signs upon the podium:

**Γ Ο Λ Δ**

"These are not medals yet," he says, bright as noon. "Name each letter as the Greeks would, then take the first Roman sound each name carries. The laurel will answer in gold."
""",
        ("gold",),
        success_text(8, "The stadium thu**N**der speaks in gold."),
        (
            (12, "Apollo says: the letters are Greek, but each name begins with a Roman sound. Let Gamma speak first."),
            (24, "Apollo sends one last pattern: .- -. ... .-- . .-. ....... .. ... ....... --. --- .-.. -.."),
        ),
    ),
    9: EnigmaDay(
        9,
        "gold",
        "The Lyre's Score",
        """Apollo lays a finger upon the score.

Not every note sings for the crowd. Some sing only for those who know their names.

Read the marked notes in order, mortal. The lyre has already written the word. The colored letters beside the notes are a key for a later ring.""",
        ("badge",),
        success_text(9, "The marked notes have crowned you **I**n gold."),
        (
            (12, "Apollo taps the score: each note is not only a sound, mortal. Here, each note bears a symbol."),
            (24, "The lyre does not ask you to count every echo. Each highlighted note is needed only once."),
            (48, "A, B, C, D, E, F, G. The lyre uses only seven letters, but seven is enough."),
        ),
        media_urls=(DAY9_SHEET_URL,),
    ),
    10: EnigmaDay(
        10,
        "gold",
        "The Backwards Oracle",
        "The oracle speaks backwards. Reverse the song, hear the truth, and name it.",
        ("eye of tiger", "eye of the tiger", "survivor"),
        success_text(10, "The oracle turns bac**K** and sings true."),
        (
            (12, "Apollo says: the oracle speaks backwards. To hear the truth, you must turn time around."),
            (24, "It is a song of rising. Of fighting. Of not giving up. The year was 1982."),
        ),
        media_urls=(DAY10_AUDIO_URL,),
        hint_media_urls=(DAY10_HINT1_URL, DAY10_HINT2_URL),
    ),
    11: EnigmaDay(
        11,
        "gold",
        "The Shadow Race",
        """Three shadows race across three different tracks. Only together do they cross the finish line.

Clue A: The image shows a sport without its name.
Clue B: I stand between you and your darkest desires. I can make you climb mountains or walk across fire.
Clue C: A broken song fragment points to a moment that is already happening.

I got this tonight.
I almost want to be alive
And I can live this life.
I am very happy.
Please let me stop now.
don't disturb me
I am happy because I am happy
I am a meteor. I run like a tiger.
ignore the laws of gravity
I feel like Lady Godiva in my race car.
I walked and there was no one in front of me.
The weather is very hot.
200 degrees is Fahrenheit to me.
I travel at the speed of light.
I highly recommend you
I feel good
I like it
(Don't go now) If you want to have fun
talk to me
I like it very much.
(Don't go now) Yes, that's good.
Of course I can't resist.
I am a stone on the way to Mars.
I'm crazy, I can't control myself.
I am a nuclear program planner.
It is sad.
The weather is very hot.
200 degrees is Fahrenheit to me.
I travel at the speed of light.
I'll give you a woman ultrasound.
6, 6, 6.
(Don't die, don't die) I love you.
(Let it dry, let it dry) Just sleep. have fun
(Don't worry, don't worry)
No, it's fine.
Fire from heaven yes
200 degrees is Fahrenheit to me.
I travel at the speed of light.
I make you a big man (hehe)
I feel good
I like it
(Don't go out) If you wanna have fun (oh yeah)
talk to me
Yes, that's good.
I don't want this to end.
yes yes yes
From here
yes, yes, yes, yes
For example

Combine the three answers.""",
        ("running strength now",),
        success_text(11, "Three tracks meet **I**n one finish beneath your feet."),
        (
            (12, "Clue A moves on two legs. Clue B holds you upright. Clue C is already happening."),
            (24, "Three words. Three disciplines. One phrase that could describe any Olympian's last 100 metres."),
        ),
        media_urls=(DAY11_PICTOGRAM_URL,),
    ),
    12: EnigmaDay(
        12,
        "gold",
        "The Fracture",
        """Apollo hands you a broken tablet.

I'm invisible to the naked eye until you're put to the test,
A sudden snap, a heavy cast, and I demand you rest.
I hold you up until I heal, causing pain that's all too real.

The three pieces were once one. Read them as one.""",
        ("a fracture", "fracture"),
        success_text(12, "The fracture spli**T**s, and truth remains."),
        (
            (12, f"Apollo has shown this broken truth before. Return to his old messages and study the images he left there: {DAY12_OLD_MESSAGE_URLS[0]}"),
            (24, f"The old images and today's riddle point to the same wound. Follow the remaining trails: {DAY12_OLD_MESSAGE_URLS[1]} and {DAY12_OLD_MESSAGE_URLS[2]}"),
        ),
    ),
    13: EnigmaDay(
        13,
        "olympian",
        "The Winner's Paradox",
        """One is born only after the contest ends. One exists before the first whistle blows.

Submit them in the correct order, or Apollo will return you to the starting line.

1. I exist only after the contest, yet I'm strived for from the beginning, and yet for my presence to be known, others must fail.
2. I have no voice but can make you laugh, cry, or think. I demand your attention, yet I don't exist in the physical world. I need players to come to life, and once you finish, I am over.""",
        ("a game a winner", "game winner"),
        success_text(13, "The contest gives way to **I**ts champion."),
        (
            (12, "Which came first: the contest, or its champion?"),
            (24, "Without one, the other cannot exist. But one must exist first for the other to be possible."),
        ),
        ordered=True,
    ),
    14: EnigmaDay(
        14,
        "olympian",
        "The Five-Ring Cipher",
        """Five rings. Five colors. One instrument waits inside them.

Apollo placed the color key inside the Lyre's Score. Read the colored letters from the rings and name the word.""",
        ("piano",),
        success_text(14, "The piano open**S** the ring of colors."),
        (
            (12, "Five rings. Five colors. The answer is locked in a key Apollo already placed beside the Lyre's Score."),
            (24, "Go back to Day 9. The colored notes and letters explain how the rings speak."),
        ),
        media_urls=(DAY14_RINGS_URL,),
    ),
    15: EnigmaDay(
        15,
        "olympian",
        "Apollo's Final Chord",
        """Apollo's final chord is not hidden in the trials themselves, but in what he said when you conquered them.

Gather the crowned letters from each victory, beginning with the first flame he gave you. Speak the phrase beneath the sun.""",
        (
            FINAL_META_ANSWER,
            "esy eisai nikitis",
            "you are winner",
            "you are the winner",
            "you are victor",
            "you are the victor",
        ),
        "Apollo lowers his golden laurel to your brow. You did not merely finish, mortal. You became what every contest seeks.",
        (
            (12, "Every victory message holds one crowned letter. Begin with the letter Apollo gave when you started."),
            (24, "The letters form a Greek phrase written in Latin letters: ESY EISAI NIKITIS."),
            (48, "It means: you are the winner."),
        ),
    ),
}


class SummerEnigma(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.unlock_notifier.start()

    def cog_unload(self):
        self.unlock_notifier.cancel()

    async def cog_load(self):
        await self.ensure_tables()

    async def ensure_tables(self) -> None:
        await self.bot.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS summer_enigma_players (
                "user" BIGINT PRIMARY KEY REFERENCES profile("user") ON DELETE CASCADE,
                enrolled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                completed_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS summer_enigma_solves (
                "user" BIGINT NOT NULL REFERENCES profile("user") ON DELETE CASCADE,
                day SMALLINT NOT NULL,
                solved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY ("user", day)
            );
            CREATE TABLE IF NOT EXISTS summer_enigma_unlocks (
                "user" BIGINT NOT NULL REFERENCES profile("user") ON DELETE CASCADE,
                day SMALLINT NOT NULL,
                unlocked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY ("user", day)
            );
            CREATE TABLE IF NOT EXISTS summer_enigma_hints (
                "user" BIGINT NOT NULL REFERENCES profile("user") ON DELETE CASCADE,
                day SMALLINT NOT NULL,
                hint_index SMALLINT NOT NULL,
                sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY ("user", day, hint_index)
            );
            CREATE TABLE IF NOT EXISTS summer_enigma_notifications (
                "user" BIGINT NOT NULL REFERENCES profile("user") ON DELETE CASCADE,
                phase TEXT NOT NULL,
                sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY ("user", phase)
            );
            CREATE TABLE IF NOT EXISTS summer_enigma_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO summer_enigma_state (key, value)
            VALUES ('enabled', 'false')
            ON CONFLICT (key) DO NOTHING;
            """
        )

    async def event_enabled(self) -> bool:
        value = await self.bot.pool.fetchval(
            "SELECT value FROM summer_enigma_state WHERE key = 'enabled';"
        )
        return str(value).lower() == "true"

    async def set_event_enabled(self, enabled: bool) -> None:
        await self.bot.pool.execute(
            """
            INSERT INTO summer_enigma_state (key, value)
            VALUES ('enabled', $1)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
            """,
            "true" if enabled else "false",
        )

    async def enroll_player(self, user_id: int) -> None:
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO summer_enigma_players ("user")
                    VALUES ($1)
                    ON CONFLICT ("user") DO UPDATE SET updated_at = NOW();
                    """,
                    user_id,
                )
                await conn.execute(
                    """
                    INSERT INTO summer_enigma_unlocks ("user", day)
                    VALUES ($1, 1)
                    ON CONFLICT ("user", day) DO NOTHING;
                    """,
                    user_id,
                )

    async def solved_days(self, user_id: int) -> set[int]:
        rows = await self.bot.pool.fetch(
            'SELECT day FROM summer_enigma_solves WHERE "user" = $1;', user_id
        )
        return {int(row["day"]) for row in rows}

    async def is_day_unlocked(self, user_id: int, day: int) -> bool:
        if day == 15:
            solved = await self.solved_days(user_id)
            if 13 not in solved or 14 not in solved:
                return False
        exists = await self.bot.pool.fetchval(
            'SELECT 1 FROM summer_enigma_unlocks WHERE "user" = $1 AND day = $2;',
            user_id,
            day,
        )
        return bool(exists)

    async def unlock_day_for_user(self, user_id: int, day: int) -> bool:
        status = await self.bot.pool.execute(
            """
            INSERT INTO summer_enigma_unlocks ("user", day)
            VALUES ($1, $2)
            ON CONFLICT ("user", day) DO NOTHING;
            """,
            user_id,
            day,
        )
        return status.endswith("1")

    async def unlock_phase_for_user(self, user_id: int, phase: str) -> bool:
        return await self.unlock_day_for_user(user_id, PHASE_DAYS[phase][0])

    async def state_value(self, key: str) -> Optional[str]:
        return await self.bot.pool.fetchval(
            "SELECT value FROM summer_enigma_state WHERE key = $1;", key
        )

    async def set_state_value_once(self, key: str, value: str) -> bool:
        status = await self.bot.pool.execute(
            """
            INSERT INTO summer_enigma_state (key, value)
            VALUES ($1, $2)
            ON CONFLICT (key) DO NOTHING;
            """,
            key,
            value,
        )
        return status.endswith("1")

    async def set_state_value(self, key: str, value: str) -> None:
        await self.bot.pool.execute(
            """
            INSERT INTO summer_enigma_state (key, value)
            VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;
            """,
            key,
            value,
        )

    async def parse_state_datetime(self, key: str) -> Optional[dt.datetime]:
        value = await self.state_value(key)
        if not value:
            return None
        try:
            return dt.datetime.fromisoformat(value)
        except ValueError:
            return None

    async def first_phase_complete_time(self, phase: str) -> Optional[dt.datetime]:
        days = list(PHASE_DAYS[phase])
        row = await self.bot.pool.fetchrow(
            """
            SELECT MAX(solved_at) AS completed_at
            FROM summer_enigma_solves
            WHERE day = ANY($1::smallint[])
            GROUP BY "user"
            HAVING COUNT(DISTINCT day) = $2
            ORDER BY completed_at ASC
            LIMIT 1;
            """,
            days,
            len(days),
        )
        if not row:
            return None
        return row["completed_at"]

    async def global_phase_open_time(self, phase: str) -> Optional[dt.datetime]:
        manual_open_at = await self.parse_state_datetime(f"phase_manual_open_at_{phase}")
        if manual_open_at is not None:
            return manual_open_at
        previous_phase = PREVIOUS_PHASE_BY_NEXT_PHASE.get(phase)
        if previous_phase is None:
            return None
        first_completed_at = await self.first_phase_complete_time(previous_phase)
        if first_completed_at is None:
            return None
        return first_completed_at + PHASE_COOLDOWNS[previous_phase]

    async def phase_complete_time(self, user_id: int, phase: str) -> Optional[dt.datetime]:
        rows = await self.bot.pool.fetch(
            """
            SELECT day, solved_at
            FROM summer_enigma_solves
            WHERE "user" = $1 AND day = ANY($2::smallint[]);
            """,
            user_id,
            list(PHASE_DAYS[phase]),
        )
        if len(rows) != len(PHASE_DAYS[phase]):
            return None
        return max(row["solved_at"] for row in rows)

    async def next_locked_phase(self, user_id: int) -> tuple[Optional[str], Optional[dt.datetime]]:
        for index, phase in enumerate(PHASE_ORDER[:-1]):
            completed_at = await self.phase_complete_time(user_id, phase)
            if not completed_at:
                return None, None
            next_phase = PHASE_ORDER[index + 1]
            already = await self.bot.pool.fetchval(
                """
                SELECT 1 FROM summer_enigma_unlocks
                WHERE "user" = $1 AND day = $2;
                """,
                user_id,
                PHASE_DAYS[next_phase][0],
            )
            if already:
                continue
            opens_at = await self.global_phase_open_time(next_phase)
            if opens_at is None:
                return None, None
            return next_phase, opens_at
        return None, None

    async def maybe_unlock_ready_phases(self, user_id: int) -> list[str]:
        unlocked = []
        now = utcnow()
        while True:
            phase, opens_at = await self.next_locked_phase(user_id)
            if phase is None or opens_at is None or opens_at > now:
                return unlocked
            if await self.unlock_phase_for_user(user_id, phase):
                unlocked.append(phase)

    def next_day_after_solve(self, day: int) -> Optional[int]:
        if day in {4, 8, 12, 15}:
            return None
        next_day = day + 1
        if next_day in DAY_CONTENT:
            return next_day
        return None

    async def unlock_next_day_after_solve(
        self, user: discord.User | discord.Member, day: int
    ) -> Optional[int]:
        next_day = self.next_day_after_solve(day)
        if next_day is None:
            return None
        if next_day == 15:
            solved = await self.solved_days(user.id)
            if 13 not in solved or 14 not in solved:
                return None
        if await self.unlock_day_for_user(user.id, next_day):
            try:
                await user.send(
                    "Apollo lifts one finger. The next gate opens, but only one step. Do not run past the sun."
                )
                await self.send_day_prompt(user, next_day)
            except Exception:
                pass
            return next_day
        return None

    async def send_day_prompt(self, destination, day: int) -> None:
        content = DAY_CONTENT[day]
        embed = discord.Embed(
            title=f"Day {day} - {content.title}",
            description=content.prompt,
            colour=discord.Color.gold(),
        )
        embed.set_footer(text=f"{EVENT_NAME} | Answer with $enigma answer day{day} <answer>")
        await destination.send(embed=embed)
        for url in content.media_urls:
            if url:
                await destination.send(url)

    async def send_unlocked_days(self, user: discord.User | discord.Member, days: tuple[int, ...]) -> None:
        for day in days:
            await self.send_day_prompt(user, day)

    async def send_hint_to_destination(
        self,
        destination,
        user_id: int,
        day: int,
        hint_index: int,
        *,
        manual: bool = False,
    ) -> bool:
        content = DAY_CONTENT[day]
        if hint_index >= len(content.hints):
            return False
        status = await self.bot.pool.execute(
            """
            INSERT INTO summer_enigma_hints ("user", day, hint_index)
            VALUES ($1, $2, $3)
            ON CONFLICT ("user", day, hint_index) DO NOTHING;
            """,
            user_id,
            day,
            hint_index,
        )
        if not status.endswith("1"):
            return False
        _hours, hint = content.hints[hint_index]
        prefix = "requested" if manual else "sun-timed"
        await destination.send(f"☀️ **Day {day} Hint ({prefix}):** {hint}")
        if hint_index < len(content.hint_media_urls) and content.hint_media_urls[hint_index]:
            await destination.send(content.hint_media_urls[hint_index])
        return True

    async def send_due_hints(self, user_id: int) -> None:
        rows = await self.bot.pool.fetch(
            """
            SELECT seu.day, seu.unlocked_at
            FROM summer_enigma_unlocks seu
            WHERE seu."user" = $1
              AND NOT EXISTS (
                  SELECT 1 FROM summer_enigma_solves ses
                  WHERE ses."user" = seu."user" AND ses.day = seu.day
              );
            """,
            user_id,
        )
        if not rows:
            return
        try:
            user = await self.bot.fetch_user(user_id)
        except Exception:
            return
        now = utcnow()
        for row in rows:
            day = int(row["day"])
            content = DAY_CONTENT.get(day)
            if not content:
                continue
            unlocked_at = row["unlocked_at"]
            for hint_index, (hours, _hint) in enumerate(content.hints):
                if unlocked_at + dt.timedelta(hours=hours) > now:
                    continue
                try:
                    await self.send_hint_to_destination(user, user_id, day, hint_index)
                except Exception:
                    break

    async def check_answer(self, content: EnigmaDay, answer: str) -> tuple[bool, str]:
        normalized = normalize_answer(answer)
        compact = normalize_compact(answer)
        if content.any_three:
            parts = [normalize_answer(part) for part in answer.split()]
            valid = {normalize_answer(item) for item in content.accepted}
            found = {part for part in parts if part in valid}
            if len(found) >= 3:
                return True, ""
            return False, "Apollo counts the names and finds the offering incomplete. Name any three of the seven."
        accepted = {normalize_answer(item) for item in content.accepted}
        compact_accepted = {normalize_compact(item) for item in content.accepted}
        if normalized in accepted or compact in compact_accepted:
            return True, ""
        if not content.ordered:
            for accepted_answer in accepted:
                if normalized and (normalized in accepted_answer or accepted_answer in normalized):
                    return True, ""
        return False, "Apollo shakes his head. The lyre has not spoken that name."

    async def mark_solved(self, user_id: int, day: int) -> bool:
        status = await self.bot.pool.execute(
            """
            INSERT INTO summer_enigma_solves ("user", day)
            VALUES ($1, $2)
            ON CONFLICT ("user", day) DO NOTHING;
            """,
            user_id,
            day,
        )
        await self.bot.pool.execute(
            'UPDATE summer_enigma_players SET updated_at = NOW() WHERE "user" = $1;',
            user_id,
        )
        return status.endswith("1")

    async def award_enigma_badge(self, user_id: int) -> bool:
        current = await self.bot.pool.fetchval(
            'SELECT badges FROM profile WHERE "user" = $1;', user_id
        )
        if current is None:
            return False
        badges = Badge.from_db(current)
        if badges & Badge.ENIGMA_CHAMPION:
            return False
        badges |= Badge.ENIGMA_CHAMPION
        await self.bot.pool.execute(
            'UPDATE profile SET badges = $1 WHERE "user" = $2;',
            badges.to_db(),
            user_id,
        )
        return True

    async def announce_first_clear(self, phase: str, user: discord.User | discord.Member) -> None:
        key = f"announced_{phase}"
        announced = await self.set_state_value_once(key, "true")
        next_phase = NEXT_PHASE_BY_COMPLETED_PHASE.get(phase)
        if next_phase:
            first_completed_at = await self.first_phase_complete_time(phase)
            if first_completed_at:
                await self.set_state_value_once(
                    f"phase_first_cleared_at_{phase}",
                    first_completed_at.isoformat(),
                )
                await self.set_state_value_once(
                    f"phase_open_at_{next_phase}",
                    (first_completed_at + PHASE_COOLDOWNS[phase]).isoformat(),
                )
        if not announced:
            return
        message = PHASE_FIRST_CLEAR_MESSAGES.get(phase)
        if not message:
            return
        try:
            await self.bot.public_log(f"☀️ **{EVENT_NAME}**\n{message}\nFirst clear: {user.mention}")
        except Exception:
            pass

    async def maybe_complete_phase(self, user, day: int) -> Optional[str]:
        phase = DAY_CONTENT[day].phase
        completed_at = await self.phase_complete_time(user.id, phase)
        if not completed_at:
            return None
        await self.announce_first_clear(phase, user)
        if phase == "olympian":
            await self.bot.pool.execute(
                """
                UPDATE summer_enigma_players
                SET completed_at = COALESCE(completed_at, NOW()), updated_at = NOW()
                WHERE "user" = $1;
                """,
                user.id,
            )
        return phase

    async def log_solve(self, user, day: int) -> None:
        try:
            await self.bot.public_log(
                f"☀️ **Enigma solve:** {user.mention} solved Day {day} - {DAY_CONTENT[day].title}."
            )
        except Exception:
            pass

    def build_help_embed(self, prefix: str) -> discord.Embed:
        embed = discord.Embed(
            title=f"☀️ {EVENT_NAME}",
            description=(
                "Apollo's hunt is self-paced. Start in DM, solve the open trials, "
                "and keep the crowned letters from every victory."
            ),
            colour=discord.Color.gold(),
        )
        embed.add_field(
            name="Commands",
            value=(
                f"`{prefix}enigma start` - Enroll and receive Day 1\n"
                f"`{prefix}enigma day <number>` - Resend an unlocked puzzle\n"
                f"`{prefix}answer day<number> <answer>` - Submit an answer\n"
                f"`{prefix}enigma answer day<number> <answer>` - Same answer command under enigma\n"
                f"`{prefix}enigma progress` - See your gates and solved days\n"
                f"`{prefix}enigma hint day<number>` - Claim the next hint\n"
                f"`{prefix}enigma leaderboard` - See final finishers"
            ),
            inline=False,
        )
        return embed

    def build_gm_embed(self, prefix: str) -> discord.Embed:
        embed = discord.Embed(
            title="☀️ Enigma GM Commands",
            colour=discord.Color.gold(),
        )
        embed.description = (
            f"`{prefix}enigma admin on/off` - Enable or disable player starts and answers\n"
            f"`{prefix}enigma admin restart` - Wipe all enigma progress\n"
            f"`{prefix}enigma admin reset @user` - Reset one player\n"
            f"`{prefix}enigma admin unlock @user <phase>` - Force-unlock the first day of a phase\n"
            f"`{prefix}enigma admin globalunlock <phase>` - Globally open a phase and ping the event channel\n"
            f"`{prefix}enigma admin solve @user <day>` - Mark one day solved\n"
            f"`{prefix}enigmagm` - Show this panel"
        )
        return embed

    async def build_progress_embed(self, user_id: int) -> discord.Embed:
        solved = await self.solved_days(user_id)
        embed = discord.Embed(
            title="☀️ Apollo's Enigma Progress",
            colour=discord.Color.gold(),
        )
        for phase in PHASE_ORDER:
            parts = []
            for day in PHASE_DAYS[phase]:
                unlocked = await self.is_day_unlocked(user_id, day)
                if day in solved:
                    marker = "Solved"
                elif unlocked:
                    marker = "Open"
                else:
                    marker = "Locked"
                parts.append(f"Day {day}: **{marker}**")
            embed.add_field(name=phase.title(), value="\n".join(parts), inline=False)
        next_phase, opens_at = await self.next_locked_phase(user_id)
        if next_phase and opens_at:
            remaining = opens_at - utcnow()
            if remaining.total_seconds() > 0:
                hours = int(remaining.total_seconds() // 3600)
                minutes = int((remaining.total_seconds() % 3600) // 60)
                embed.set_footer(text=f"Next gate: {next_phase.title()} in {hours}h {minutes}m")
        return embed

    @commands.group(name="enigma", aliases=["summerenigma", "apolloenigma"], invoke_without_command=True)
    @locale_doc
    async def enigma(self, ctx):
        _("""View Apollo's Olympic Enigma Hunt.""")
        await ctx.send(embed=self.build_help_embed(ctx.clean_prefix))

    @enigma.command(name="start", aliases=["join"])
    @has_char()
    async def enigma_start(self, ctx):
        if not await self.event_enabled():
            return await ctx.send("Apollo's Enigma Hunt has not opened yet.")
        await self.enroll_player(ctx.author.id)
        try:
            await ctx.author.send(
                success_text(0, "Apollo places the first flam**E** in your hands.")
            )
            await ctx.author.send(
                "One gate opens first. Win it, and the next will answer."
            )
            await self.send_day_prompt(ctx.author, 1)
            if ctx.guild:
                await ctx.send("Apollo has sent the first trial to your DM.")
        except discord.Forbidden:
            await ctx.send("I could not DM you. Please open your DMs and try again.")

    @enigma.command(name="day", aliases=["trial"])
    @has_char()
    async def enigma_day(self, ctx, day: int):
        if day not in DAY_CONTENT:
            return await ctx.send("That enigma day does not exist.")
        await self.enroll_player(ctx.author.id)
        await self.maybe_unlock_ready_phases(ctx.author.id)
        if not await self.is_day_unlocked(ctx.author.id, day):
            return await ctx.send("This trial has not yet opened for you. Complete what came before, and wait for the gates to turn.")
        try:
            await self.send_day_prompt(ctx.author, day)
            if ctx.guild:
                await ctx.send("Apollo has resent that trial to your DM.")
        except discord.Forbidden:
            await ctx.send("I could not DM you. Please open your DMs and try again.")

    async def handle_answer_submission(self, ctx, day_token: str, answer: str):
        if not await self.event_enabled():
            return await ctx.send("Apollo's Enigma Hunt has not opened yet.")
        match = re.search(r"\d+", str(day_token))
        if not match:
            return await ctx.send("Use a day like `day9` or `9`.")
        day = int(match.group(0))
        if day not in DAY_CONTENT:
            return await ctx.send("That enigma day does not exist.")
        await self.enroll_player(ctx.author.id)
        await self.maybe_unlock_ready_phases(ctx.author.id)
        if not await self.is_day_unlocked(ctx.author.id, day):
            return await ctx.send("This trial has not yet opened for you. Complete what came before, and wait for the gates to turn.")
        if day in await self.solved_days(ctx.author.id):
            return await ctx.send("Apollo smiles. You have already claimed this victory. Move forward.")
        ok, error = await self.check_answer(DAY_CONTENT[day], answer)
        if not ok:
            return await ctx.send(error)
        is_new = await self.mark_solved(ctx.author.id, day)
        if not is_new:
            return await ctx.send("Apollo smiles. You have already claimed this victory. Move forward.")
        await self.log_solve(ctx.author, day)
        phase = await self.maybe_complete_phase(ctx.author, day)
        response = DAY_CONTENT[day].success
        if phase in PHASE_COOLDOWNS:
            next_phase = NEXT_PHASE_BY_COMPLETED_PHASE.get(phase)
            opens_at = await self.global_phase_open_time(next_phase) if next_phase else None
            if opens_at and opens_at <= utcnow() and next_phase:
                response += f"\n\nThe {phase.title()} gate is complete. The {next_phase.title()} gate already stands open."
                await self.unlock_phase_for_user(ctx.author.id, next_phase)
                try:
                    await self.send_day_prompt(ctx.author, PHASE_DAYS[next_phase][0])
                except Exception:
                    pass
            elif opens_at:
                remaining = opens_at - utcnow()
                hours = max(0, int(remaining.total_seconds() // 3600))
                minutes = max(0, int((remaining.total_seconds() % 3600) // 60))
                response += (
                    f"\n\nThe {phase.title()} gate is complete. Apollo's next gate opens for all challengers in "
                    f"**{hours}h {minutes}m**."
                )
            else:
                response += (
                    f"\n\nThe {phase.title()} gate is complete. Apollo has not yet opened the next gate."
                )
        elif phase == "olympian":
            awarded_badge = await self.award_enigma_badge(ctx.author.id)
            response += f"\n\n**{EVENT_NAME} is complete.** The gods remember your name."
            if awarded_badge:
                response += "\nApollo pins the **Enigma Champion** badge to your legend."
        await ctx.send(response)
        for url in SUCCESS_MEDIA_URLS.get(day, ()):
            await ctx.send(
                "A torn note of Apollo's score falls from the laurel. Keep it; later colors may seek this music."
            )
            await ctx.send(url)
        if phase not in PHASE_COOLDOWNS and phase != "olympian":
            await self.unlock_next_day_after_solve(ctx.author, day)

    @enigma.command(name="answer", aliases=["a"])
    @has_char()
    async def enigma_answer(self, ctx, day_token: str, *, answer: str):
        await self.handle_answer_submission(ctx, day_token, answer)

    @commands.command(name="answer", aliases=["ans"])
    @has_char()
    async def enigma_answer_shortcut(self, ctx, day_token: str, *, answer: str):
        await self.handle_answer_submission(ctx, day_token, answer)

    @enigma.command(name="progress", aliases=["status"])
    @has_char()
    async def enigma_progress(self, ctx):
        await self.enroll_player(ctx.author.id)
        await ctx.send(embed=await self.build_progress_embed(ctx.author.id))

    @enigma.command(name="hint")
    @has_char()
    async def enigma_hint(self, ctx, day_token: str):
        match = re.search(r"\d+", str(day_token))
        if not match:
            return await ctx.send("Use a day like `day9` or `9`.")
        day = int(match.group(0))
        if day not in DAY_CONTENT:
            return await ctx.send("That enigma day does not exist.")
        await self.enroll_player(ctx.author.id)
        if not await self.is_day_unlocked(ctx.author.id, day):
            return await ctx.send("This trial has not yet opened for you.")
        if day in await self.solved_days(ctx.author.id):
            return await ctx.send("Apollo smiles. You already solved this one.")
        sent_rows = await self.bot.pool.fetch(
            """
            SELECT hint_index FROM summer_enigma_hints
            WHERE "user" = $1 AND day = $2;
            """,
            ctx.author.id,
            day,
        )
        sent = {int(row["hint_index"]) for row in sent_rows}
        content = DAY_CONTENT[day]
        next_index = next((idx for idx in range(len(content.hints)) if idx not in sent), None)
        if next_index is None:
            return await ctx.send("Apollo has no more hints for this trial.")
        await self.send_hint_to_destination(
            ctx, ctx.author.id, day, next_index, manual=True
        )

    @enigma.command(name="leaderboard", aliases=["lb", "top"])
    async def enigma_leaderboard(self, ctx):
        rows = await self.bot.pool.fetch(
            """
            SELECT sep.*, profile.name
            FROM summer_enigma_players sep
            JOIN profile ON profile."user" = sep."user"
            WHERE sep.completed_at IS NOT NULL
            ORDER BY sep.completed_at ASC
            LIMIT 10;
            """
        )
        embed = discord.Embed(
            title="☀️ Apollo's Enigma Finishers",
            colour=discord.Color.gold(),
        )
        if not rows:
            embed.description = "No one has completed Apollo's final chord yet."
        for index, row in enumerate(rows, start=1):
            embed.add_field(
                name=f"#{index} {row['name'] or row['user']}",
                value=f"Completed: **{row['completed_at']:%Y-%m-%d %H:%M UTC}**",
                inline=False,
            )
        await ctx.send(embed=embed)

    @is_gm()
    @commands.command(name="enigmagm", aliases=["summerenigmagm"])
    async def enigma_gm_help(self, ctx):
        await ctx.send(embed=self.build_gm_embed(ctx.clean_prefix))

    @is_gm()
    @enigma.group(name="admin", aliases=["gm"], invoke_without_command=True)
    async def enigma_admin(self, ctx):
        enabled = await self.event_enabled()
        await ctx.send(f"Apollo's Enigma Hunt is **{'enabled' if enabled else 'disabled'}**.")

    @enigma_admin.command(name="on")
    async def enigma_admin_on(self, ctx):
        await self.set_event_enabled(True)
        await ctx.send("Apollo's Enigma Hunt is now open.")

    @enigma_admin.command(name="off")
    async def enigma_admin_off(self, ctx):
        await self.set_event_enabled(False)
        await ctx.send("Apollo's Enigma Hunt is now closed.")

    @enigma_admin.command(name="restart", aliases=["resetall"])
    async def enigma_admin_restart(self, ctx):
        if not await ctx.confirm("Reset every player's enigma progress, solves, hints, and announcements?"):
            return await ctx.send("Enigma restart cancelled.")
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM summer_enigma_hints;")
                await conn.execute("DELETE FROM summer_enigma_notifications;")
                await conn.execute("DELETE FROM summer_enigma_unlocks;")
                await conn.execute("DELETE FROM summer_enigma_solves;")
                await conn.execute("DELETE FROM summer_enigma_players;")
                await conn.execute(
                    """
                    DELETE FROM summer_enigma_state
                    WHERE key LIKE 'announced_%'
                       OR key LIKE 'phase_first_cleared_at_%'
                       OR key LIKE 'phase_open_at_%'
                       OR key LIKE 'phase_manual_open_at_%';
                    """
                )
        await ctx.send("Apollo's Enigma Hunt has been reset.")

    @enigma_admin.command(name="reset")
    async def enigma_admin_reset_player(self, ctx, target: discord.Member):
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute('DELETE FROM summer_enigma_hints WHERE "user" = $1;', target.id)
                await conn.execute('DELETE FROM summer_enigma_notifications WHERE "user" = $1;', target.id)
                await conn.execute('DELETE FROM summer_enigma_unlocks WHERE "user" = $1;', target.id)
                await conn.execute('DELETE FROM summer_enigma_solves WHERE "user" = $1;', target.id)
                await conn.execute('DELETE FROM summer_enigma_players WHERE "user" = $1;', target.id)
        await ctx.send(f"Reset enigma progress for **{target}**.")

    @enigma_admin.command(name="unlock")
    async def enigma_admin_unlock(self, ctx, target: discord.Member, phase: str):
        phase = phase.lower()
        if phase not in PHASE_DAYS:
            return await ctx.send(f"Unknown phase. Use: {', '.join(PHASE_DAYS)}")
        await self.enroll_player(target.id)
        await self.unlock_phase_for_user(target.id, phase)
        await ctx.send(f"Unlocked **{phase}** for **{target}**.")

    @enigma_admin.command(name="globalunlock", aliases=["gunlock", "openphase"])
    async def enigma_admin_global_unlock(self, ctx, phase: str):
        phase = phase.lower()
        if phase not in PHASE_DAYS:
            return await ctx.send(f"Unknown phase. Use: {', '.join(PHASE_DAYS)}")

        await self.set_state_value(
            f"phase_manual_open_at_{phase}", utcnow().isoformat()
        )

        rows = await self.bot.pool.fetch(
            'SELECT "user" FROM summer_enigma_players WHERE completed_at IS NULL;'
        )
        unlocked_count = 0
        for row in rows:
            user_id = int(row["user"])
            if phase != "bronze":
                previous_phase = PREVIOUS_PHASE_BY_NEXT_PHASE.get(phase)
                if previous_phase and not await self.phase_complete_time(user_id, previous_phase):
                    continue
            if await self.unlock_phase_for_user(user_id, phase):
                unlocked_count += 1

        first_day = PHASE_DAYS[phase][0]
        channel = self.bot.get_channel(ENIGMA_EVENT_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(ENIGMA_EVENT_CHANNEL_ID)
            except Exception:
                channel = None
        public_message = (
            f"<@&{ENIGMA_EVENT_ROLE_ID}> ☀️ **Apollo's Enigma Hunt**\n"
            f"The **{phase.title()}** gate has been opened globally by staff.\n"
            f"Eligible challengers can continue with `{ctx.clean_prefix}enigma day {first_day}`."
        )
        if channel is not None:
            allowed = discord.AllowedMentions(roles=True)
            await channel.send(public_message, allowed_mentions=allowed)
        else:
            await ctx.send("Could not find the configured enigma event channel.")

        await ctx.send(
            f"Globally opened **{phase}**. First day: **Day {first_day}**. "
            f"Unlocked for **{unlocked_count}** enrolled eligible player(s)."
        )

    @enigma_admin.command(name="solve")
    async def enigma_admin_solve(self, ctx, target: discord.Member, day: int):
        if day not in DAY_CONTENT:
            return await ctx.send("That enigma day does not exist.")
        await self.enroll_player(target.id)
        await self.mark_solved(target.id, day)
        await self.maybe_complete_phase(target, day)
        await ctx.send(f"Marked Day {day} solved for **{target}**.")

    @tasks.loop(minutes=10)
    async def unlock_notifier(self):
        if not hasattr(self.bot, "pool"):
            return
        try:
            rows = await self.bot.pool.fetch('SELECT "user" FROM summer_enigma_players WHERE completed_at IS NULL;')
        except Exception:
            return
        for row in rows:
            user_id = int(row["user"])
            await self.send_due_hints(user_id)
            unlocked = await self.maybe_unlock_ready_phases(user_id)
            if not unlocked:
                continue
            try:
                user = await self.bot.fetch_user(user_id)
            except Exception:
                continue
            for phase in unlocked:
                status = await self.bot.pool.execute(
                    """
                    INSERT INTO summer_enigma_notifications ("user", phase)
                    VALUES ($1, $2)
                    ON CONFLICT ("user", phase) DO NOTHING;
                    """,
                    user_id,
                    phase,
                )
                if not status.endswith("1"):
                    continue
                try:
                    await user.send(PHASE_UNLOCK_MESSAGES[phase])
                    await self.send_day_prompt(user, PHASE_DAYS[phase][0])
                except Exception:
                    pass

    @unlock_notifier.before_loop
    async def before_unlock_notifier(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(SummerEnigma(bot))
