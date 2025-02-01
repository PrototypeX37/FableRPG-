import asyncio
import os
import random
import discord
from discord.ext import commands
from discord.ui import View, Button
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageChops, ImageFont
from io import BytesIO
import traceback
from utils.i18n import _, locale_doc, use_current_gettext
from datetime import datetime

from cogs.shard_communication import user_on_cooldown as user_cooldown
from utils.checks import is_gm, has_char  # Import is_gm from utils.checks
from utils.i18n import locale_doc

# Directions
N, S, W, E = ("n", "s", "w", "e")

def get_hp_display(current_hp, max_hp=100):
    """
    Returns the HP display as current_hp / max_hp.
    """
    return f"{current_hp}/{max_hp}"

class Cell:
    def __init__(self, x, y, walls, cog):
        self.x = x
        self.y = y
        self.walls = set(walls)
        self.cog = cog  # Reference to the cog to access methods
        self.trap = random.randint(1, 10) == 1  # 10% Chance of being a trap
        if not self.trap:
            self.enemy = self.generate_enemy()  # Generate an enemy or None
        else:
            self.enemy = None
        self.treasure = False

    def is_full(self):
        return len(self.walls) == 4

    def _wall_to(self, other):
        assert abs(self.x - other.x) + abs(self.y - other.y) == 1, f"{self}, {other}"
        if other.y < self.y:
            return N
        elif other.y > self.y:
            return S
        elif other.x < self.x:
            return W
        elif other.x > self.x:
            return E
        else:
            raise ValueError("Invalid cell positions for wall removal.")

    def connect(self, other):
        other.walls.remove(other._wall_to(self))
        self.walls.remove(self._wall_to(other))

    def generate_enemy(self):
        """
        Randomly generates an enemy with varying stats.
        """
        if random.random() < self.cog.get_enemy_chance():  # Enemy chance based on time of day
            enemy_type = random.choice(['Goblin', 'Skeleton', 'Orc'])
            return Enemy(enemy_type)
        return None

class Enemy:
    def __init__(self, enemy_type):
        self.type = enemy_type
        if enemy_type == 'Goblin':
            self.hp = 30
            self.max_hp = 30
            self.attack = 7
            self.defense = 5
        elif enemy_type == 'Skeleton':
            self.hp = 50
            self.max_hp = 50
            self.attack = 10
            self.defense = 6
        elif enemy_type == 'Orc':
            self.hp = 70
            self.max_hp = 70
            self.attack = 11
            self.defense = 3

    def is_defeated(self):
        return self.hp <= 0

class Maze:
    def __init__(self, width=20, height=10, cog=None):
        self.width = width
        self.height = height
        self.cog = cog  # Reference to the cog to access methods
        self.cells = [Cell(x, y, [N, S, E, W], cog) for y in range(height) for x in range(width)]

    def __getitem__(self, index):
        x, y = index
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.cells[x + y * self.width]
        return None

    def neighbors(self, cell):
        x, y = cell.x, cell.y
        potential = [(x, y - 1), (x, y + 1), (x - 1, y), (x + 1, y)]  # Corrected line
        return [self[new_x, new_y] for new_x, new_y in potential if self[new_x, new_y]]

    def randomize(self):
        cell_stack = []
        cell = random.choice(self.cells)
        n_visited = 1
        total = len(self.cells)

        while n_visited < total:
            neighbors = [c for c in self.neighbors(cell) if c.is_full()]
            if neighbors:
                neighbor = random.choice(neighbors)
                cell.connect(neighbor)
                cell_stack.append(cell)
                cell = neighbor
                n_visited += 1
            elif cell_stack:
                cell = cell_stack.pop()

    @staticmethod
    def generate(width=20, height=10, treasures=5, cog=None):
        maze = Maze(width, height, cog)
        maze.randomize()
        treasure_cells = random.sample(maze.cells[1:], treasures)
        for cell in treasure_cells:
            cell.treasure = True
        return maze

class AdventureState:
    def __init__(self, user_id, maze, attack, defense, total_treasures):
        self.user_id = user_id
        self.maze = maze
        self.attack = attack
        self.defense = defense
        self.position = (0, 0)  # Start at top-left
        self.hp = 100  # Initial HP
        self.max_hp = 100  # Max HP
        self.treasures_found = 0
        self.total_treasures = total_treasures  # Store total number of treasures
        self.special_treasure_found = False
        self.is_active = True
        self.in_combat = False  # Indicates if the player is currently in combat
        self.message = None  # Reference to the Discord message
        self.turn = 1  # Initialize turn counter
        self.uploaded_files = []  # List to track uploaded filenames
        self.gold = 0  # Total gold collected
        self.inventory = []  # Player's inventory
        self.current_enemy = None  # Current enemy in battle

    def get_turn_filename(self):
        """
        Returns the current turn filename.
        """
        filename = f"maze_{self.user_id}_{self.turn}_{random.randint(1000,99999)}.png"
        self.uploaded_files.append(filename)
        self.turn += 1
        if self.turn > 999:
            self.turn = 1
        return filename


class ConfirmQuitView(discord.ui.View):
    def __init__(self, cog, state):
        super().__init__(timeout=30)
        self.cog = cog
        self.state = state
        self.message = None
        self.original_message = None
        self.original_view = None

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.danger, custom_id="confirm_quit_yes")
    async def confirm_quit_yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handles the confirmation to quit the adventure."""
        try:
            await self.cog.handle_quit(interaction, self.state)
            self.stop()
        except Exception as e:
            await self.cog.report_error(e, interaction)

    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary, custom_id="confirm_quit_no")
    async def confirm_quit_no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handles the cancellation of quitting the adventure."""
        try:
            # Re-enable all buttons in the original view
            for child in self.original_view.children:
                child.disabled = False

            # Update the original message with re-enabled buttons
            embed, file = await self.cog.create_maze_embed(self.state)
            await self.original_message.edit(
                content=None,
                embed=embed,
                view=self.original_view,
                attachments=[file]
            )

            # Instead of trying to delete the ephemeral message,
            # just update it to show it's been cancelled
            await interaction.response.edit_message(
                content="Quit cancelled - you can continue your adventure!",
                view=None
            )

            self.stop()
        except Exception as e:
            await self.cog.report_error(e, interaction)

    async def on_timeout(self):
        """Handles the view timeout by re-enabling the original view."""
        try:
            # Re-enable all buttons in the original view
            for child in self.original_view.children:
                child.disabled = False

            # Update the original message with re-enabled buttons
            embed, file = await self.cog.create_maze_embed(self.state)
            await self.original_message.edit(
                content=None,
                embed=embed,
                view=self.original_view,
                attachments=[file]
            )

            # Try to delete the confirmation message
            if self.message:
                await self.message.delete()
        except Exception as e:
            print(f"Error in timeout handler: {e}")

class AdventureView(discord.ui.View):
    def __init__(self, cog, state):
        super().__init__(timeout=None)
        self.cog = cog
        self.state = state

        # Disable movement buttons if in combat
        if state.in_combat:
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    if child.custom_id.startswith("move_"):
                        child.disabled = True
                    elif child.custom_id.startswith("action_"):
                        child.disabled = False
        else:
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    if child.custom_id.startswith("move_"):
                        child.disabled = False
                    elif child.custom_id.startswith("action_"):
                        child.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.state.user_id:
            await interaction.response.send_message("This adventure isn't for you!", ephemeral=True)
            return False
        return True

    # Movement Buttons in the order: North, East, South, West
    @discord.ui.button(label="North", style=discord.ButtonStyle.primary, emoji="⬆️", custom_id="move_north")
    async def move_north_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.safe_handle_move(interaction, self.state, "north")

    @discord.ui.button(label="East", style=discord.ButtonStyle.primary, emoji="➡️", custom_id="move_east")
    async def move_east_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.safe_handle_move(interaction, self.state, "east")

    @discord.ui.button(label="South", style=discord.ButtonStyle.primary, emoji="⬇️", custom_id="move_south")
    async def move_south_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.safe_handle_move(interaction, self.state, "south")

    @discord.ui.button(label="West", style=discord.ButtonStyle.primary, emoji="⬅️", custom_id="move_west")
    async def move_west_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.safe_handle_move(interaction, self.state, "west")

    # Action Buttons
    @discord.ui.button(label="⚔️ Attack", style=discord.ButtonStyle.secondary, custom_id="action_attack")
    async def action_attack_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.safe_handle_action(interaction, self.state, "attack")

    @discord.ui.button(label="🛡️ Defend", style=discord.ButtonStyle.secondary, custom_id="action_defend")
    async def action_defend_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.safe_handle_action(interaction, self.state, "defend")

    @discord.ui.button(label="❤️ Heal", style=discord.ButtonStyle.secondary, custom_id="action_heal")
    async def action_heal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.safe_handle_action(interaction, self.state, "heal")

    @discord.ui.button(label="🏳️ Quit", style=discord.ButtonStyle.danger, custom_id="quit_adventure")
    async def quit_adventure_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handles the Quit button by prompting for confirmation."""
        try:
            # Acknowledge the button interaction
            await interaction.response.defer()

            # Disable all buttons in the original view
            for child in self.children:
                child.disabled = True

            # Create the confirmation view
            confirm_view = ConfirmQuitView(self.cog, self.state)

            # Update the original message with disabled buttons
            embed, file = await self.cog.create_maze_embed(self.state)
            await interaction.message.edit(
                content="⚠️ Are you sure you want to quit the adventure?",
                embed=embed,
                view=self,  # Use the current view with disabled buttons
                attachments=[file]
            )

            # Send the confirmation buttons in a separate message
            confirm_message = await interaction.followup.send(
                "Click Yes to confirm quitting, or No to continue playing.",
                view=confirm_view,
                ephemeral=True
            )

            # Store the message references
            confirm_view.message = confirm_message
            confirm_view.original_message = interaction.message
            confirm_view.original_view = self

        except Exception as e:
            await self.cog.report_error(e, interaction)

class TestCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_adventures = {}  # user_id: AdventureState
        self.error_recipient_id = 295173706496475136  # Replace with the actual user ID

        # Create a directory for maze images if it doesn't exist
        self.image_dir = "maze_images"
        os.makedirs(self.image_dir, exist_ok=True)

        # Load assets
        self.assets = self.load_assets()

        # Load font
        self.font = self.load_font()

        # Manual night time toggle (None = use system time, True = night, False = day)
        self.manual_night_time = None

    def load_assets(self):
        """
        Loads graphical assets from the 'assets' directory.
        """
        assets = {}
        asset_names = ['floor', 'player', 'enemy_goblin', 'enemy_skeleton', 'enemy_orc', 'treasure', 'trap', 'exit', 'battle_image']
        for name in asset_names:
            path = os.path.join('assets', f'{name}.png')
            if os.path.exists(path):
                assets[name] = Image.open(path).convert('RGBA')
            else:
                print(f"Asset '{name}' not found at '{path}'.")
        return assets

    def load_font(self):
        """
        Loads a font for rendering text in images.
        """
        font_path = os.path.join('assets', 'pixel_font.ttf')
        if os.path.exists(font_path):
            return ImageFont.truetype(font_path, size=24)  # Increased font size to 24
        else:
            return ImageFont.load_default()

    @has_char()
    @user_cooldown(7200)
    @commands.command(aliases=["aa"], brief="Embark on an active adventure.")
    @locale_doc
    async def activeadventure(self, ctx):
        _(
            # xgettext: no-python-format
            """Active adventures will put you into a randomly generated maze. You will begin in the top left corner and your goal is to find the exit in the bottom right corner.
            You control your character with the arrow buttons below the message.

            You have a fixed amount of HP based on your items. The adventure ends when you find the exit or your HP drop to zero.
            You can lose HP by getting damaged by traps or enemies.

            The maze contains safe spaces and treasures but also traps and enemies.
            Each space has a 10% chance of being a trap. If a space does not have a trap, it has a 10% chance of having an enemy.
            Each maze has 5 treasure chests.

            Traps can damage you from 1/10 of your total HP to up to 1/8 of your total HP.
            Enemy damage is based on your own damage. During enemy fights, you can attack (⚔️), defend (🛡️) or recover HP (❤️)
            Treasure chests can have gold up to 25 times your attack + defense.

            If you reach the end, you will receive a special treasure with gold up to 100 times your attack + defense.

            (This command has a cooldown of 30 minutes)"""
        )



        if not self.is_night_time():
            tres = 5  # Static number of treasures during the day
            maze = Maze.generate(width=12, height=12, treasures=tres, cog=self)
        else:
            tres = random.randint(5, 9)  # Random treasures between 5 and 9 during the night
            maze = Maze.generate(width=15, height=15, treasures=tres, cog=self)
        attack, defense = await self.get_damage_armor_for(ctx.author)
        state = AdventureState(ctx.author.id, maze, attack, defense, total_treasures=tres)
        self.active_adventures[ctx.author.id] = state

        try:
            # Generate initial maze image
            embed, file = await self.create_maze_embed(state)
            view = AdventureView(self, state)
            message = await ctx.send(
                "🌀 You have entered the labyrinth! Use the buttons below to navigate.",
                embed=embed,
                view=view,
                file=file
            )
            state.message = message  # Store reference to the message
            view.message = message  # Optionally store reference in the view
        except Exception as e:
            await self.report_error(e, ctx)


    @is_gm()
    @commands.command(aliases=["aaanight"], brief="Toggle night time manually.")
    async def toggle_night_time(self, ctx, mode: str = None):
        """
        Toggles night time manually.
        Usage: aaanight [on|off|auto]
        """

        channel_id = 1311869497497354281  # Replace with your channel ID

        if channel_id != ctx.channel.id:
            return

        if mode is None:
            # Toggle between None and not None
            if self.manual_night_time is None:
                self.manual_night_time = True
                await ctx.send("🌙 Night time is now manually set to ON.")
            else:
                self.manual_night_time = None
                await ctx.send("🌞 Night time is now set to AUTO (system time).")
        else:
            mode = mode.lower()
            if mode == "on":
                self.manual_night_time = True
                await ctx.send("🌙 Night time is now manually set to ON.")
            elif mode == "off":
                self.manual_night_time = False
                await ctx.send("🌞 Night time is now manually set to OFF.")
            elif mode == "auto":
                self.manual_night_time = None
                await ctx.send("🌞 Night time is now set to AUTO (system time).")
            else:
                await ctx.send("Invalid mode. Usage: aaanight [on|off|auto]")

    def is_night_time(self):
        """
        Determines if it's nighttime based on manual setting or system time.
        Nighttime is from 6 PM to 6 AM.
        """
        if self.manual_night_time is not None:
            return self.manual_night_time
        current_hour = datetime.now().hour
        return current_hour >= 18 or current_hour < 6

    def get_treasure_gold_range(self):
        """
        Returns the gold range for treasures based on time of day.
        """
        if self.is_night_time():
            return (15000, 25000)
        else:
            return (10000, 15000)

    def get_exit_gold_range(self):
        """
        Returns the gold range for exiting the maze based on time of day.
        """
        if self.is_night_time():
            return (50000, 70000)
        else:
            return (30000, 50000)

    def get_enemy_chance(self):
        """
        Returns the chance of an enemy appearing in a cell based on time of day.
        """
        if self.is_night_time():
            return 0.10  # 15% chance during nighttime (50% more than daytime)
        else:
            return 0.05  # 10% chance during daytime

    async def create_maze_embed(self, state: AdventureState):
        """
        Creates an embed displaying the current maze or battle scene.
        Saves the image locally and embeds it directly.
        """
        if state.in_combat:
            # Generate battle image with HP bars
            image_buffer = self.generate_battle_image(state)
            # Create embed for battle
            embed = discord.Embed(
                title="⚔️ Battle! ⚔️",
                description=f"You are battling a {state.current_enemy.type}!",
                color=discord.Color.red()
            )

            # Add HP fields
            embed.add_field(
                name="❤️ Your HP",
                value=f"{get_hp_display(state.hp, state.max_hp)}",
                inline=True
            )
            embed.add_field(
                name=f"👾 {state.current_enemy.type}'s HP",
                value=f"{get_hp_display(state.current_enemy.hp, state.current_enemy.max_hp)}",
                inline=True
            )
        else:
            # Generate maze image
            maze = state.maze
            image_buffer = self.generate_maze_image(maze, state.position, state)
            # Create embed for maze
            embed = discord.Embed(
                title="🌀 Maze Adventure 🌀",
                description="Navigate through the maze to find the exit!",
                color=discord.Color.dark_gray()
            )

            # Add HP display
            embed.add_field(
                name="❤️ HP",
                value=f"{get_hp_display(state.hp, state.max_hp)}",
                inline=True
            )
            embed.add_field(
                name="💰 Gold",
                value=f"{state.gold}",
                inline=False
            )
            embed.add_field(
                name="💎 Treasures",
                value=f"{state.treasures_found}/{state.total_treasures}",
                inline=False
            )
        embed.set_footer(text="Use the buttons below to interact.")

        # Save image locally
        filename = state.get_turn_filename()
        filepath = os.path.join(self.image_dir, filename)

        # Save image to file
        with open(filepath, 'wb') as f:
            image_buffer.seek(0)
            f.write(image_buffer.getvalue())

        # Add image file to embed
        file = discord.File(filepath, filename=filename)
        embed.set_image(url=f"attachment://{filename}")

        return embed, file

    def generate_battle_image(self, state: AdventureState):
        """
        Generates the battle image with HP bars for the player and enemy.
        """
        # Load the battle background image
        battle_bg = self.assets.get('battle_image')
        if not battle_bg:
            # If the image is not found, create a placeholder
            battle_bg = Image.new('RGBA', (800, 600), (0, 0, 0, 255))
        else:
            # Resize if necessary
            battle_bg = battle_bg.copy()

        img = battle_bg.copy()
        draw = ImageDraw.Draw(img)

        # Define HP bar dimensions and positions
        bar_width = 300  # Increased bar width
        bar_height = 20
        player_bar_x = 50  # Left side for player
        player_bar_y = 50  # Back to top position

        enemy_bar_x = img.width - bar_width - 50  # Right side for enemy
        enemy_bar_y = 50  # Back to top position

        # Draw player HP bar with pixel art style
        self.draw_pixel_hp_bar(
            draw, player_bar_x, player_bar_y, bar_width, bar_height,
            state.hp, state.max_hp, fill_color=(0, 255, 0)
        )
        # Draw enemy HP bar with pixel art style
        self.draw_pixel_hp_bar(
            draw, enemy_bar_x, enemy_bar_y, bar_width, bar_height,
            state.current_enemy.hp, state.current_enemy.max_hp, fill_color=(255, 0, 0)
        )

        # Save to BytesIO
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    def draw_pixel_hp_bar(self, draw, x, y, width, height, current_hp, max_hp, fill_color):
        """
        Draws a pixel art style HP bar.
        """
        # Border rectangle
        border_color = (255, 255, 255)
        draw.rectangle((x, y, x + width, y + height), outline=border_color, width=2)

        # Inner background
        draw.rectangle((x + 1, y + 1, x + width - 1, y + height - 1), fill=(50, 50, 50))

        # HP proportion
        hp_ratio = current_hp / max_hp
        hp_fill_width = int((width - 2) * hp_ratio)

        # Gradient fill for HP bar
        for i in range(hp_fill_width):
            line_color = (
                int(fill_color[0] * (1 - i / hp_fill_width)),
                int(fill_color[1] * (i / hp_fill_width)),
                fill_color[2]
            )
            draw.line(
                [(x + 1 + i, y + 1), (x + 1 + i, y + height - 2)],
                fill=line_color
            )

        # HP text
        hp_text = f"{current_hp}/{max_hp}"
        font = self.font  # Use the font loaded in __init__

        # Get text size
        bbox = draw.textbbox((0, 0), hp_text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        text_x = x + (width - text_width) / 2
        text_y = y + (height - text_height) / 2 - 1  # Adjusted for better centering
        draw.text(
            (text_x, text_y),
            hp_text,
            fill=(255, 255, 255),
            font=font
        )

    def generate_maze_image(self, maze: Maze, player_pos, state: AdventureState):
        """
        Generates a maze image using Pillow with enhanced visuals.
        Walls are drawn as lines, and other elements use assets.
        Implements fog of war during nighttime with an orange glow.
        """
        cell_size = 40
        wall_thickness = 4
        width_px = maze.width * cell_size
        height_px = maze.height * cell_size

        img = Image.new("RGBA", (width_px, height_px), (20, 20, 20, 255))  # Dark background
        draw = ImageDraw.Draw(img)

        px, py = player_pos

        # Draw floor
        floor_tile = self.assets.get('floor')
        for cell in maze.cells:
            x = cell.x
            y = cell.y
            cell_x = x * cell_size
            cell_y = y * cell_size
            if floor_tile:
                img.paste(floor_tile, (cell_x, cell_y))
            else:
                draw.rectangle([(cell_x, cell_y), (cell_x + cell_size, cell_y + cell_size)], fill=(50, 50, 50))

        # Draw walls as lines
        wall_color = (200, 200, 200)  # Light gray walls
        for cell in maze.cells:
            x = cell.x
            y = cell.y
            cell_x = x * cell_size
            cell_y = y * cell_size

            if N in cell.walls:
                draw.line([(cell_x, cell_y), (cell_x + cell_size, cell_y)], fill=wall_color, width=wall_thickness)
            if S in cell.walls:
                draw.line([(cell_x, cell_y + cell_size), (cell_x + cell_size, cell_y + cell_size)], fill=wall_color, width=wall_thickness)
            if W in cell.walls:
                draw.line([(cell_x, cell_y), (cell_x, cell_y + cell_size)], fill=wall_color, width=wall_thickness)
            if E in cell.walls:
                draw.line([(cell_x + cell_size, cell_y), (cell_x + cell_size, cell_y + cell_size)], fill=wall_color, width=wall_thickness)

        # Draw exit
        exit_tile = self.assets.get('exit')
        if exit_tile:
            ex, ey = (maze.width - 1) * cell_size, (maze.height - 1) * cell_size
            img.paste(exit_tile, (ex, ey), exit_tile)

        # Draw treasures, enemies, and traps
        for cell in maze.cells:
            x = cell.x
            y = cell.y
            cell_x = x * cell_size
            cell_y = y * cell_size

            if cell.trap and not state.in_combat:
                trap_tile = self.assets.get('trap')
                if trap_tile:
                    # Resize trap sprite to 20x20
                    trap_tile_resized = trap_tile.resize((20, 20))
                    img.paste(trap_tile_resized, (cell_x + 10, cell_y + 10), trap_tile_resized)
            if cell.treasure:
                treasure_tile = self.assets.get('treasure')
                if treasure_tile:
                    img.paste(treasure_tile, (cell_x, cell_y), treasure_tile)
            if cell.enemy:
                enemy_tile = self.assets.get(f'enemy_{cell.enemy.type.lower()}')
                if enemy_tile:
                    img.paste(enemy_tile, (cell_x, cell_y), enemy_tile)

        # Apply fog of war and orange glow during nighttime
        if self.is_night_time():
            # Create radial gradient mask
            gradient_radius = cell_size * 3  # Adjust for desired glow size
            mask = Image.new('L', (width_px, height_px), color=0)
            draw_mask = ImageDraw.Draw(mask)

            center_x = px * cell_size + cell_size // 2
            center_y = py * cell_size + cell_size // 2
            max_radius = int(gradient_radius)

            for r in range(max_radius, 0, -1):
                alpha = int(255 * ((max_radius - r) / max_radius))
                bbox = (
                    center_x - r,
                    center_y - r,
                    center_x + r,
                    center_y + r
                )
                draw_mask.ellipse(bbox, fill=alpha)

            # Apply darkness outside the gradient
            darkness = Image.new('RGBA', (width_px, height_px), (0, 0, 0, 255))
            img = Image.composite(img, darkness, mask)

            # Create orange glow
            glow = Image.new('RGBA', (width_px, height_px), (255, 140, 0, 0))
            # Soften the glow
            glow_mask = mask.filter(ImageFilter.GaussianBlur(radius=10))
            glow.putalpha(glow_mask)
            img = Image.alpha_composite(img, glow)

        # Draw player on top
        player_tile = self.assets.get('player')
        if player_tile:
            img.paste(player_tile, (px * cell_size, py * cell_size), player_tile)
        else:
            # If no player asset, draw a simple circle
            draw.ellipse(
                [(px * cell_size + 5, py * cell_size + 5), (px * cell_size + cell_size - 5, py * cell_size + cell_size - 5)],
                fill=(0, 0, 255, 255)
            )

        # Save to BytesIO
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    async def get_damage_armor_for(self, user):
        """
        Retrieves the user's attack and defense stats.
        Placeholder: Replace with actual logic.
        """
        # Example values; replace with actual retrieval logic
        attack = 15
        defense = 5
        return attack, defense

    async def safe_handle_move(self, interaction: discord.Interaction, state: AdventureState, direction: str):
        try:
            await self.handle_move(interaction, state, direction)
        except Exception as e:
            await self.report_error(e, interaction)

    async def safe_handle_action(self, interaction: discord.Interaction, state: AdventureState, action: str):
        try:
            await self.handle_action(interaction, state, action)
        except Exception as e:
            await self.report_error(e, interaction)

    async def safe_handle_quit(self, interaction: discord.Interaction, state: AdventureState):
        try:
            await self.handle_quit(interaction, state)
        except Exception as e:
            await self.report_error(e, interaction)

    async def handle_move(self, interaction: discord.Interaction, state: AdventureState, direction: str):
        x, y = state.position
        maze = state.maze
        current_cell = maze[x, y]

        dir_letter = direction[0].lower()
        if dir_letter not in 'nsew':
            # Invalid direction
            return

        # Check if in combat
        if state.in_combat:
            embed, file = await self.create_maze_embed(state)
            view = AdventureView(self, state)
            await interaction.response.edit_message(
                content="⚔️ You are in combat and cannot move!",
                embed=embed,
                view=view,
                attachments=[file]
            )
            return

        # Check for walls
        if dir_letter in current_cell.walls:
            # Generate the current embed and view to ensure consistency
            embed, file = await self.create_maze_embed(state)
            view = AdventureView(self, state)

            # Update the original message with the wall notification
            await interaction.response.edit_message(
                content="⛔ You can't move in that direction; there's a wall!",
                embed=embed,
                view=view,
                attachments=[file]
            )
            return

        # Update position
        if dir_letter == 'n':
            y -= 1
        elif dir_letter == 's':
            y += 1
        elif dir_letter == 'e':
            x += 1
        elif dir_letter == 'w':
            x -= 1

        state.position = (x, y)
        current_cell = maze[x, y]  # Update current cell

        # Check for events
        event_messages = []

        if current_cell.trap:
            # Trigger trap
            damage = random.randint(5, 15)
            state.hp -= damage
            event_messages.append(f"💥 You stepped on a trap and lost {damage} HP!")
            current_cell.trap = False  # Remove trap after triggering

        if current_cell.enemy:
            state.in_combat = True
            state.current_enemy = current_cell.enemy
            event_messages.append(f"🐲 You encountered a {current_cell.enemy.type}! Prepare for battle!")
        elif current_cell.treasure:
            state.treasures_found += 1
            gold_min, gold_max = self.get_treasure_gold_range()
            gold_found = random.randint(gold_min, gold_max)
            state.gold += gold_found  # Award random gold
            current_cell.treasure = False  # Remove treasure from cell
            event_messages.append(f"💰 You found a treasure and gained {gold_found} gold!")

        # Check if player has reached the exit
        if (x, y) == (maze.width - 1, maze.height - 1):
            # Player reached the exit
            gold_min, gold_max = self.get_exit_gold_range()
            exit_reward = random.randint(gold_min, gold_max)
            state.gold += exit_reward
            await self.end_adventure(
                interaction,
                state,
                success=True,
                message="",
            )
            return

        # Check if player is dead
        if state.hp <= 0:
            await self.end_adventure(
                interaction,
                state,
                success=False,
                message="😵 You have died in the maze."
            )
            return

        # Generate the updated embed and view
        embed, file = await self.create_maze_embed(state)
        view = AdventureView(self, state)

        # Combine event messages
        event_content = "\n".join(event_messages) if event_messages else "✅ You moved successfully."

        # Edit the original message with updated embed and content
        await interaction.response.edit_message(
            content=event_content,
            embed=embed,
            view=view,
            attachments=[file]
        )

    async def handle_action(self, interaction: discord.Interaction, state: AdventureState, action: str):
        """
        Handles player's actions during battles.
        """
        enemy = state.current_enemy
        if not enemy:
            # No enemy to interact with; inform the user by editing the message
            state.in_combat = False  # Ensure combat state is updated
            embed, file = await self.create_maze_embed(state)
            view = AdventureView(self, state)
            await interaction.response.edit_message(
                content="❗ There's no enemy here!",
                embed=embed,
                view=view,
                attachments=[file]
            )
            return

        message = ""
        if action == "attack":
            damage = max(0, state.attack - enemy.defense)
            enemy_damage = max(0, enemy.attack - state.defense)
            # Player attacks enemy
            enemy.hp -= damage
            message += f"⚔️ You attacked the {enemy.type} and dealt {damage} damage!\n"
            # Enemy retaliates if not defeated
            if not enemy.is_defeated():
                state.hp -= enemy_damage
                message += f"🐲 The {enemy.type} retaliated and dealt {enemy_damage} damage!\n"
            else:
                message += f"🎉 You defeated the {enemy.type}!\n"
                cell = state.maze[state.position]
                cell.enemy = None
                state.current_enemy = None
                state.in_combat = False
        elif action == "defend":
            enemy_damage = max(0, (enemy.attack // 2) - state.defense * 2)
            state.hp -= enemy_damage
            message += f"🛡️ You defended against the {enemy.type}'s attack!\n"
            message += f"🐲 The {enemy.type} dealt {enemy_damage} damage!\n"
        elif action == "heal":
            heal_amount = random.randint(3, 10)
            state.hp = min(state.max_hp, state.hp + heal_amount)
            enemy_damage = max(0, enemy.attack - state.defense)
            state.hp -= enemy_damage
            message += f"❤️ You healed yourself for {heal_amount} HP!\n"
            message += f"🐲 The {enemy.type} attacked you and dealt {enemy_damage} damage!\n"
        else:
            # Unknown action; inform the user
            embed, file = await self.create_maze_embed(state)
            view = AdventureView(self, state)
            await interaction.response.edit_message(
                content="❓ Unknown action.",
                embed=embed,
                view=view,
                attachments=[file]
            )
            return

        # Check player's HP
        if state.hp <= 0:
            message += "😵 You have been defeated by the enemy."
            await self.end_adventure(
                interaction,
                state,
                success=False,
                message=message
            )
            return

        # Check if enemy is defeated
        if enemy.is_defeated():
            cell = state.maze[state.position]
            cell.enemy = None
            state.current_enemy = None
            state.in_combat = False

        # Update embed and view
        embed, file = await self.create_maze_embed(state)
        view = AdventureView(self, state)
        await interaction.response.edit_message(
            content=message,
            embed=embed,
            view=view,
            attachments=[file]
        )

    async def handle_quit(self, interaction: discord.Interaction, state: AdventureState):
        """
        Handles the player quitting the adventure.
        """
        try:
            # First try to acknowledge the interaction if not already done
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)

            await self.end_adventure(
                interaction=interaction,
                state=state,
                success=False,
                message="🏳️ You have quit the adventure."
            )
        except Exception as e:
            await self.report_error(e, interaction)

    async def end_adventure(self, interaction: discord.Interaction, state: AdventureState, success: bool, message: str):
        """
        Ends the adventure, cleans up the state, and sends a final message.
        """
        try:
            # Delete all uploaded images
            await self.delete_uploaded_images(state)

            # Remove the adventure state
            if state.user_id in self.active_adventures:
                del self.active_adventures[state.user_id]

            # Create the final embed message
            embed = discord.Embed(
                title="🌀 Adventure Ended 🌀",
                description=message,
                color=discord.Color.green() if success else discord.Color.red()
            )

            if success:
                # Grant gold to the user
                embed.add_field(name="🎁 Reward", value=f"You have been rewarded with {state.gold} gold!", inline=False)
                async with self.bot.pool.acquire() as conn:
                    await conn.execute(
                        'UPDATE profile SET "money"="money"+$1 WHERE "user"=$2;',
                        state.gold,
                        interaction.user.id,
                    )


            # Try to edit the original message if it exists and is accessible
            try:
                if state.message and not interaction.response.is_done():
                    await interaction.response.edit_message(
                        embed=embed,
                        view=None,
                        content=None,
                        attachments=[]
                    )
                elif state.message:
                    await interaction.message.edit(
                        embed=embed,
                        view=None,
                        content=None,
                        attachments=[]
                    )
            except (discord.NotFound, discord.HTTPException, AttributeError):
                # If editing fails, try to send a new message
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            embed=embed,
                            ephemeral=True
                        )
                    else:
                        await interaction.followup.send(
                            embed=embed,
                            ephemeral=True
                        )
                except Exception as e:
                    print(f"Failed to send end message: {e}")

        except Exception as e:
            print(f"Error in end_adventure: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "⚠️ An error occurred while ending the adventure. The administrators have been notified.",
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        "⚠️ An error occurred while ending the adventure. The administrators have been notified.",
                        ephemeral=True
                    )
            except Exception as e:
                print(f"Failed to send error message: {e}")



    async def delete_uploaded_images(self, state: AdventureState):
        """
        Deletes all uploaded maze images associated with the adventure from local storage.
        """
        for filename in state.uploaded_files:
            try:
                filepath = os.path.join(self.image_dir, filename)
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception as e:
                print(f"Failed to delete {filename}: {e}")

    async def report_error(self, exception: Exception, context):
        """
        Reports errors by sending the traceback to a specified user via DM.
        """
        error_recipient = self.bot.get_user(self.error_recipient_id)
        if not error_recipient:
            print(f"Error recipient with ID {self.error_recipient_id} not found.")
            return

    @is_gm()
    @commands.command(name="spinwheel")
    async def spinwheel(self, ctx):
        await ctx.send("Spinning the wheel from a list generated by AI.. This way take awhile to generate several lists.")
        await asyncio.sleep(7)
        await ctx.send(f'{ctx.author.mention} Create a modern "Snake 2". You have 30 minutes to create this.')


    @commands.command(name="gameserver")
    async def game_server(self, ctx, subcommand: str):
        if ctx.author.id != 295173706496475136:
            await ctx.send("You do not have permission to use this command.")
            return

        if subcommand.lower() == "pair":
            # Start the pairing simulation
            embed = discord.Embed(
                title="Pairing with Unity RPG Game Server...",
                color=discord.Color.blue()
            )
            embed.add_field(name="Status", value="Connecting to server...", inline=False)
            message = await ctx.send(embed=embed)

            # Simulate server loading process
            await asyncio.sleep(3)
            embed.set_field_at(0, name="Status", value="Authenticating credentials...", inline=False)
            await message.edit(embed=embed)

            await asyncio.sleep(4)
            embed.set_field_at(0, name="Status", value="Establishing secure connection...", inline=False)
            await message.edit(embed=embed)

            await asyncio.sleep(5)
            embed.set_field_at(0, name="Status", value="Loading server assets...", inline=False)
            await message.edit(embed=embed)

            # Simulating loading maps (longer time for realism)
            await asyncio.sleep(7)
            maps = [
                "Darcian City",
                "Greenwood Forest",
                "Arctic Highlands",
                "Volcanic Wastes",
                "Mystic Isles",
                "Cursed Dungeons",
                "Sky Fortress"
            ]
            embed.add_field(name="Maps Loaded", value=", ".join(maps), inline=False)
            embed.set_field_at(0, name="Status", value="Maps successfully loaded!", inline=False)
            await message.edit(embed=embed)

            # Simulating loading classes
            await asyncio.sleep(6)
            classes = ["Santa's Helper", "Reaper"]
            embed.add_field(name="Classes Loaded", value=f"{', '.join(classes)}", inline=False)
            embed.set_field_at(0, name="Status", value="Classes successfully loaded!", inline=False)
            await message.edit(embed=embed)

            # Simulating loading quests
            await asyncio.sleep(5)
            quests = [f"Quest {i}" for i in range(1, 45)]
            embed.add_field(name="Quests Loaded", value=f"{len(quests)} Quests", inline=False)
            embed.set_field_at(0, name="Status", value="Quests successfully loaded!", inline=False)
            await message.edit(embed=embed)

            # Simulating loading monsters (longer time for realism)
            await asyncio.sleep(9)
            monsters = [f"Monster {i}" for i in range(1, 145)]
            embed.add_field(name="Monsters Loaded", value=f"{len(monsters)} Monsters", inline=False)
            embed.set_field_at(0, name="Status", value="Monsters successfully loaded!", inline=False)
            await message.edit(embed=embed)

            # Simulating loading NPCs separately
            await asyncio.sleep(7)
            npcs = [f"NPC {i}" for i in range(1, 34)]
            embed.add_field(name="NPCs Loaded", value=f"{len(npcs)} NPCs", inline=False)
            embed.set_field_at(0, name="Status", value="NPCs successfully loaded!", inline=False)
            await message.edit(embed=embed)

            # Simulating other data loading
            await asyncio.sleep(7)
            other_data = "Guilds, Skills, Items, Regions"
            embed.add_field(name="Other Data", value=other_data, inline=False)
            embed.set_field_at(0, name="Status", value="Game assets successfully loaded!", inline=False)
            await message.edit(embed=embed)

            # Simulate players online
            await asyncio.sleep(5)
            players_online = 0
            embed.add_field(name="Players Online", value=f"{players_online} / 1000", inline=False)
            embed.set_field_at(0, name="Status", value="Connection finalized. Pairing complete.", inline=False)
            embed.title = "Game Server Paired Successfully!"
            embed.color = discord.Color.green()
            await message.edit(embed=embed)

            # Server log embed
            await asyncio.sleep(10)
            log_embed = discord.Embed(
                title="Server Log",
                description="Monitoring server activity...",
                color=discord.Color.gold()
            )
            log_embed.add_field(
                name="Log Entry 1",
                value=f"Lunar ({ctx.author.id}) is connecting...",
                inline=False
            )
            log_message = await ctx.send(embed=log_embed)

            await asyncio.sleep(13)
            log_embed.add_field(
                name="Log Entry 2",
                value=f"Lunar ({ctx.author.id}) has connected and spawned in 'Darcian City'.",
                inline=False
            )
            await log_message.edit(embed=log_embed)

            # Fetch and display player data
            player_data = {
                "Marriage": "mothermanic",
                "Weapons": "Soul Feast",
                "Class": "Reaper",
                "Level": 47,
                "Home": "Greenwood Cottage"
            }
            log_embed.add_field(
                name="Player Data",
                value=f"Marriage: {player_data['Marriage']}\n"
                      f"Weapons: {player_data['Weapons']}\n"
                      f"Class: {player_data['Class']}\n"
                      f"Level: {player_data['Level']}\n"
                      f"Home: {player_data['Home']}",
                inline=False
            )
            await log_message.edit(embed=log_embed)

            # Update players online to 1
            players_online = 1
            embed.set_field_at(len(embed.fields) - 1, name="Players Online", value=f"{players_online} / 1000",
                               inline=False)
            await message.edit(embed=embed)

            # Simulate disconnection
            await asyncio.sleep(180)  # 3 minutes later
            log_embed.add_field(
                name="Log Entry 3",
                value=f"Lunar ({ctx.author.id}) has disconnected.",
                inline=False
            )
            await log_message.edit(embed=log_embed)

            # Update players online to 0
            players_online = 0
            embed.set_field_at(len(embed.fields) - 1, name="Players Online", value=f"{players_online} / 1000",
                               inline=False)
            embed.set_field_at(0, name="Status", value="Connection closed. Server shutting down...", inline=False)
            await message.edit(embed=embed)

            # Server shutdown
            await asyncio.sleep(10)
            log_embed.add_field(
                name="Log Entry 4",
                value="Server shutting down (manual)...",
                inline=False
            )
            log_embed.color = discord.Color.red()
            log_embed.title = "Server Shutdown"
            await log_message.edit(embed=log_embed)

    @commands.Cog.listener()
    async def on_ready(self):
        print("TestCog is ready.")

async def setup(bot):
    await bot.add_cog(TestCog(bot))
