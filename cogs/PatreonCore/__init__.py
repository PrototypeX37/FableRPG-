from datetime import datetime, timezone
import discord
from discord.ext import commands, tasks
import patreon
from datetime import datetime, timedelta
import aiohttp
import asyncio
import json
import os

from utils.checks import is_gm
from cogs.shard_communication import user_on_cooldown as user_cooldown


class PatreonCore(commands.Cog):
    """Cog for syncing Patreon pledges with Discord roles"""
    # Monthly weapon-token distribution knobs for `redeemweapontokens`.
    # Edit these values to change payout math.
    WEAPON_TOKEN_BASE = 1
    WEAPON_TOKEN_BOOSTER_MIN = 2
    WEAPON_TOKEN_TIER_MINIMUMS = {
        1: 2,
        2: 3,
        3: 5,  # Tier 3+ uses this minimum
    }

    def __init__(self, bot):
        self.bot = bot
        self.role_driven_sync = False

        # Patreon API credentials
        self.client_id = ""
        self.client_secret = ""
        self.access_token = ""
        self.refresh_token = ""
        self.token_expires_at = datetime.now(timezone.utc) + timedelta(days=30)  # Assuming token expires in 30 days

        # Campaign and guild settings
        self.patreon_campaign_id = 11352402  # Will be populated on first API call
        self.guild_id = 1323388333589528638  # Support server ID
        self.booster_role_id = 1405906986507178066  # Support server booster role
        self.check_interval = 60 * 30  # Check every 30 minutes

        # Discord Patreon role ID -> internal tier level
        self.role_tier_mapping = {
            1411756981274017912: 1,  # Mortal
            1411757068364546139: 2,  # Demi-God
            1411757100136140913: 4,  # Olympian
            1411757151306645706: 4,  # Titan
            1411757168356491508: 4,  # Primordial Fate
        }

        # Role mapping - map Patreon tier IDs to Discord role IDs
        # You'll need to populate this with your actual tier IDs and role IDs
        self.tier_role_mapping = {}
        # Optional Patreon tier ID -> internal tier level (1..4+)
        self.tier_level_mapping = {}

        # Store patron data
        self.patrons_data = {}
        self.patreon_tier_map = {}
        self.last_updated = None
        self.manual_tier_overrides = {}
        self.pending_tier_validations = {}

        # Configurable log channel
        self.log_channel_id = None

        # Config file path
        self.config_file = "patreon_config.json"
        self._load_config()
        # Force API-first mode for patron sync (role sync remains fallback on API failure).
        self.role_driven_sync = False
        if not self.access_token:
            # Fallback to the single-token config if patreon_config.json is missing.
            self.access_token = self.bot.config.external.patreon_token or ""

        # Start the update task
        self.update_roles.start()

    def _member_tier_from_roles(self, member: discord.Member) -> tuple[int, list[str]]:
        """Return (highest_tier, matched_role_ids) for a member based on Discord roles."""
        highest_tier = 0
        matched_role_ids: list[str] = []
        for role in member.roles:
            mapped_tier = self.role_tier_mapping.get(int(role.id))
            if mapped_tier is None:
                continue
            matched_role_ids.append(str(role.id))
            highest_tier = max(highest_tier, int(mapped_tier))
        return highest_tier, matched_role_ids

    async def _snapshot_role_driven_patrons(self, guild: discord.Guild) -> dict[str, list[str]]:
        """Build role-driven patron snapshot and keep DB tier in sync."""
        snapshot: dict[str, list[str]] = {}
        async for member in guild.fetch_members(limit=None):
            tier_level, matched_roles = self._member_tier_from_roles(member)
            await self.update_patron_tier_in_db(member.id, tier_level, source="discord_roles")
            if tier_level >= 1:
                snapshot[str(member.id)] = matched_roles
        return snapshot

    def _tier_level_from_entitled_tiers(self, tier_ids: list[str]) -> int:
        """Infer internal tier from Patreon entitled tiers."""
        highest = 0
        for tier_id in tier_ids:
            tier_id_str = str(tier_id)

            # Explicit mapping by Patreon tier ID wins.
            explicit_level = self.tier_level_mapping.get(tier_id_str)
            if explicit_level is not None:
                try:
                    highest = max(highest, int(explicit_level))
                    continue
                except (TypeError, ValueError):
                    pass

            # If Patreon tier is mapped to a Discord role, reuse role->tier mapping.
            mapped_role_id = self.tier_role_mapping.get(tier_id_str)
            if mapped_role_id is not None:
                try:
                    mapped_tier = self.role_tier_mapping.get(int(mapped_role_id), 0)
                    highest = max(highest, int(mapped_tier))
                    if mapped_tier:
                        continue
                except (TypeError, ValueError):
                    pass

            tier_meta = self.patreon_tier_map.get(tier_id_str, {})
            title = str(tier_meta.get("title", "")).lower()
            amount_cents = int(tier_meta.get("amount_cents", 0) or 0)

            if any(keyword in title for keyword in ("primordial", "titan", "olympian", "ragnarok", "legendary")):
                highest = max(highest, 4)
            elif any(keyword in title for keyword in ("demi", "warrior", "bronze")):
                highest = max(highest, 2)
            elif any(keyword in title for keyword in ("mortal", "adventurer", "basic")):
                highest = max(highest, 1)
            elif amount_cents >= 7500:
                highest = max(highest, 4)
            elif amount_cents >= 2500:
                highest = max(highest, 2)
            elif amount_cents > 0:
                highest = max(highest, 1)

        # Active patron with unknown tier metadata still gets baseline patron tier.
        if highest == 0 and tier_ids:
            return 1
        return highest

    def get_cached_tier_for_user(self, discord_user_id: int) -> int:
        tier_ids = self.patrons_data.get(str(discord_user_id))
        if tier_ids is None:
            tier_ids = self.patrons_data.get(discord_user_id)
        if not tier_ids:
            return 0
        if self.role_driven_sync:
            highest = 0
            for role_id in tier_ids:
                try:
                    highest = max(highest, int(self.role_tier_mapping.get(int(role_id), 0)))
                except (TypeError, ValueError):
                    continue
            return highest
        return self._tier_level_from_entitled_tiers(tier_ids)

    def _to_int(self, value, default=0):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _normalize_user_id(self, value):
        parsed = self._to_int(value, None)
        if parsed is None:
            raise ValueError(f"Invalid user id: {value}")
        return parsed

    def _user_key(self, user_id):
        return str(self._normalize_user_id(user_id))

    def _utcnow_iso(self):
        return datetime.now(timezone.utc).isoformat()

    def get_manual_tier_override(self, user_id):
        return self.manual_tier_overrides.get(self._user_key(user_id))

    def set_manual_tier_override(self, user_id, tier, setter_id=None):
        key = self._user_key(user_id)
        payload = {
            "tier": self._to_int(tier, 0),
            "set_at": self._utcnow_iso(),
        }
        if setter_id is not None:
            payload["set_by"] = self._to_int(setter_id, 0)
        self.manual_tier_overrides[key] = payload

        pending = self.pending_tier_validations.get(key)
        if pending and self._to_int(pending.get("proposed_tier"), -1) == payload["tier"]:
            self.pending_tier_validations.pop(key, None)
        self._save_config()

    def clear_manual_tier_override(self, user_id):
        key = self._user_key(user_id)
        had_override = self.manual_tier_overrides.pop(key, None) is not None
        had_pending = self.pending_tier_validations.pop(key, None) is not None
        if had_override or had_pending:
            self._save_config()
        return had_override

    def _load_config(self):
        """Load configuration from file"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)

                # Load basic settings
                self.guild_id = config.get('guild_id', self.guild_id)
                self.log_channel_id = config.get('log_channel_id', self.log_channel_id)
                self.check_interval = config.get('check_interval', self.check_interval)
                campaign_id = config.get('patreon_campaign_id', self.patreon_campaign_id)
                if campaign_id:
                    try:
                        self.patreon_campaign_id = int(campaign_id)
                    except (TypeError, ValueError):
                        pass

                # Load token info
                self.client_id = config.get('client_id', self.client_id)
                self.client_secret = config.get('client_secret', self.client_secret)
                self.access_token = config.get('access_token', self.access_token)
                self.refresh_token = config.get('refresh_token', self.refresh_token)
                expires_at = config.get('token_expires_at')
                if expires_at:
                    self.token_expires_at = datetime.fromisoformat(expires_at)

                # Load tier-role mapping
                self.tier_role_mapping = config.get('tier_role_mapping', {})
                self.tier_level_mapping = config.get('tier_level_mapping', self.tier_level_mapping)
                self.role_driven_sync = config.get('role_driven_sync', self.role_driven_sync)
                overrides = config.get("manual_tier_overrides", self.manual_tier_overrides)
                pending = config.get("pending_tier_validations", self.pending_tier_validations)
                self.manual_tier_overrides = overrides if isinstance(overrides, dict) else {}
                self.pending_tier_validations = pending if isinstance(pending, dict) else {}

                print("[PatreonCore] Configuration loaded successfully.")
        except Exception as e:
            print(f"[PatreonCore] Error loading config: {e}")

    def _save_config(self):
        """Save configuration to file"""
        try:
            config = {
                'guild_id': self.guild_id,
                'log_channel_id': self.log_channel_id,
                'check_interval': self.check_interval,
                'patreon_campaign_id': self.patreon_campaign_id,
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'access_token': self.access_token,
                'refresh_token': self.refresh_token,
                'token_expires_at': self.token_expires_at.isoformat(),
                'tier_role_mapping': self.tier_role_mapping,
                'tier_level_mapping': self.tier_level_mapping,
                'role_driven_sync': self.role_driven_sync,
                'manual_tier_overrides': self.manual_tier_overrides,
                'pending_tier_validations': self.pending_tier_validations,
            }

            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)

            print("[PatreonCore] Configuration saved successfully.")
        except Exception as e:
            print(f"[PatreonCore] Error saving config: {e}")

    def cog_unload(self):
        """Clean up when cog is unloaded"""
        self.update_roles.cancel()
        self._save_config()

    async def refresh_access_token(self):
        """Refresh the Patreon access token"""
        if not all([self.refresh_token, self.client_id, self.client_secret]):
            return False
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    'grant_type': 'refresh_token',
                    'refresh_token': self.refresh_token,
                    'client_id': self.client_id,
                    'client_secret': self.client_secret
                }

                async with session.post('https://www.patreon.com/api/oauth2/token', data=payload) as response:
                    if response.status == 200:
                        data = await response.json()
                        self.access_token = data['access_token']
                        self.refresh_token = data['refresh_token']
                        # Set expiration time (usually 30 days)
                        self.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=data.get('expires_in', 2592000))
                        self._save_config()
                        print("[PatreonCore] Token refreshed successfully")
                        return True
                    else:
                        error_text = await response.text()
                        print(f"[PatreonCore] Failed to refresh token: {response.status} - {error_text}")
                        return False
        except Exception as e:
            print(f"[PatreonCore] Error refreshing token: {e}")
            return False

    async def get_api_client(self):
        """Get a valid Patreon API client, refreshing the token if needed"""
        if not self.access_token:
            return None
        # Check if token needs refreshing (with 1-hour buffer)
        if self.refresh_token and datetime.now(timezone.utc) + timedelta(hours=1) >= self.token_expires_at:
            success = await self.refresh_access_token()
            if not success:
                print("[PatreonCore] Could not refresh token. Using existing token.")

        # Return API client
        return patreon.API(self.access_token)

    async def get_campaign_id(self):
        """Get the campaign ID from config or Patreon API."""
        if self.patreon_campaign_id:
            return int(self.patreon_campaign_id)

        if not self.access_token:
            print("[PatreonCore] Cannot fetch campaign ID without access token.")
            return None

        try:
            async with aiohttp.ClientSession() as session:
                url = "https://www.patreon.com/api/oauth2/v2/campaigns"
                headers = {"Authorization": f"Bearer {self.access_token}"}

                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        print(f"[PatreonCore] Error fetching campaign list: {response.status}")
                        return None

                    payload = await response.json()
                    campaigns = payload.get("data") or []
                    if not campaigns:
                        print("[PatreonCore] No campaigns found for token.")
                        return None

                    self.patreon_campaign_id = int(campaigns[0].get("id"))
                    print(f"[PatreonCore] Using campaign ID {self.patreon_campaign_id}")
                    self._save_config()
                    return self.patreon_campaign_id
        except Exception as e:
            print(f"[PatreonCore] Error in get_campaign_id: {e}")
            return None

    async def fetch_patrons(self):
        """Fetch patrons directly using Patreon's V2 API with correct include parameters"""
        user_id = 524674960153903126
        user = self.bot.get_user(user_id)

        try:
            if not self.access_token:
                print("[PatreonCore] No Patreon access token configured.")
                return [], []

            if self.refresh_token and datetime.now(timezone.utc) + timedelta(hours=1) >= self.token_expires_at:
                await self.refresh_access_token()

            if user:
                print("[PatreonCore] Starting patron fetch process using direct API")

            campaign_id = await self.get_campaign_id()
            if not campaign_id:
                if user:
                    print("[PatreonCore] No campaign ID available. Cannot fetch patrons.")
                return [], []

            # Get all patrons using direct API calls
            all_patrons = []
            included_data = []

            async with aiohttp.ClientSession() as session:
                # Build the URL with CORRECTED includes and fields
                base_url = f"https://www.patreon.com/api/oauth2/v2/campaigns/{campaign_id}/members"
                params = {
                    # Simplified include parameter - removed the nested relationship
                    "include": "user,currently_entitled_tiers",
                    "fields[member]": "patron_status,full_name,email",
                    "fields[user]": "social_connections,email",
                    "fields[tier]": "title,description,amount_cents",
                    "page[count]": 100
                }

                headers = {"Authorization": f"Bearer {self.access_token}"}

                if user:
                    print(f"[PatreonCore] Using params: {params}")

                next_url = base_url
                page_count = 0

                while next_url:
                    page_count += 1
                    if user:
                        print(f"[PatreonCore] Fetching page {page_count} of members...")

                    try:
                        async with session.get(next_url, params=params if page_count == 1 else None,
                                               headers=headers) as response:
                            # Print response status for debugging
                            if user:
                                print(f"[PatreonCore] Response status: {response.status}")

                            if response.status != 200:
                                if user:
                                    print(f"[PatreonCore] API error: {response.status}")
                                    error_text = await response.text()
                                    print(f"Error details: {error_text[:500]}...")
                                break

                            data = await response.json()

                            # Print some of the response structure for debugging
                            if user:
                                keys = list(data.keys())
                                print(f"[PatreonCore] Response keys: {keys}")

                            # Extract patrons from the data
                            members = data.get("data", [])
                            if user:
                                print(f"[PatreonCore] Fetched {len(members)} members in page {page_count}")
                                if members:
                                    print(f"[PatreonCore] First member keys: {list(members[0].keys())}")

                            # Debug first member
                            if members and len(members) > 0 and user:
                                sample = members[0]
                                attributes = sample.get("attributes", {})
                                status = attributes.get("patron_status")
                                print(f"[PatreonCore] Sample member status: {status}")

                                # Check for user relationship
                                relationships = sample.get("relationships", {})
                                print(f"[PatreonCore] Relationship keys: {list(relationships.keys())}")

                                user_rel = relationships.get("user", {}).get("data", {})
                                if user_rel:
                                    print(
                                        f"[PatreonCore] Sample member has user relationship: {user_rel.get('id')}")
                                else:
                                    print("[PatreonCore] Sample member has NO user relationship")

                            # Get included data
                            included = data.get("included", [])
                            if user:
                                print(
                                    f"[PatreonCore] Fetched {len(included)} included objects in page {page_count}")
                                if included:
                                    types = set([inc.get("type") for inc in included])
                                    print(f"[PatreonCore] Included object types: {types}")

                            all_patrons.extend(members)
                            included_data.extend(included)

                            # Check for pagination
                            links = data.get("links", {})
                            next_url = links.get("next")
                            if not next_url:
                                if user:
                                    print("[PatreonCore] No more pages to fetch")
                                break

                            # Remove params as they're included in the next_url
                            params = None

                    except Exception as e:
                        if user:
                            print(f"[PatreonCore] Error in API request: {e}")
                        break

                if user:
                    print(
                        f"[PatreonCore] Total patrons fetched: {len(all_patrons)} with {len(included_data)} included objects")

                # Debug what types of data we got
                status_counts = {}
                for patron in all_patrons:
                    status = patron.get("attributes", {}).get("patron_status")
                    status_counts[status] = status_counts.get(status, 0) + 1

                if user:
                    print(f"[PatreonCore] Patron status counts: {status_counts}")

                return all_patrons, included_data

        except Exception as e:
            import traceback
            error_traceback = traceback.format_exc()
            if user:
                print(f"[PatreonCore] Error fetching patrons: {e}")
                print(error_traceback)
            return [], []

    def process_patrons(self, patron_data, included_data):
        """Process patron data and extract Discord IDs with tier information"""
        result = {}
        debug_info = []

        user_id = 524674960153903126
        user = self.bot.get_user(user_id)

        try:
            debug_info.append(f"Processing {len(patron_data)} patrons and {len(included_data)} included items")

            # Create mappings from included data
            user_discord_map = {}  # Maps user_id to discord_id
            tier_map = {}  # Maps tier_id to tier_info

            # Process included data first
            debug_info.append("Processing included data...")
            for included in included_data:
                try:
                    inc_type = included.get("type")
                    inc_id = included.get("id")

                    if inc_type == "user":
                        # Extract social connections
                        attrs = included.get("attributes", {})
                        debug_info.append(f"User {inc_id} attributes keys: {list(attrs.keys())}")

                        social = attrs.get("social_connections", {})
                        debug_info.append(f"User {inc_id} social connections: {social}")
                        discord_data = social.get("discord")

                        if discord_data and "user_id" in discord_data:
                            discord_id = discord_data["user_id"]
                            if discord_id:
                                user_discord_map[inc_id] = discord_id
                                debug_info.append(f"Found Discord ID {discord_id} for user {inc_id}")

                    elif inc_type == "tier":
                        tier_title = included.get("attributes", {}).get("title", "Unknown Tier")
                        tier_map[inc_id] = {
                            "title": tier_title,
                            "amount_cents": included.get("attributes", {}).get("amount_cents", 0)
                        }
                        debug_info.append(f"Found tier: {tier_title} (ID: {inc_id})")

                except Exception as e:
                    debug_info.append(f"Error processing included item: {e}")

            if user:
                print(f"[PatreonCore] Found {len(user_discord_map)} users with Discord connections")
                print(f"[PatreonCore] Found {len(tier_map)} tiers")
            self.patreon_tier_map = tier_map

            # Process patrons
            debug_info.append("Processing patron data...")
            patron_count = 0
            active_patrons = 0

            for patron in patron_data:
                try:
                    patron_count += 1

                    # Get patron status
                    attributes = patron.get("attributes", {})
                    patron_status = attributes.get("patron_status")

                    # Only process active patrons
                    if patron_status != "active_patron":
                        continue

                    active_patrons += 1

                    # Get user ID and Discord ID
                    relationships = patron.get("relationships", {})
                    user_rel = relationships.get("user", {}).get("data", {})
                    user_id = user_rel.get("id") if user_rel else None

                    if not user_id:
                        debug_info.append(f"No user ID for patron {patron_count}")
                        continue

                    if user_id not in user_discord_map:
                        debug_info.append(f"No Discord ID for user {user_id}")
                        continue

                    discord_id = user_discord_map[user_id]

                    # Get entitled tiers
                    entitled_tiers = []
                    tier_rels = relationships.get("currently_entitled_tiers", {}).get("data", [])

                    for tier_rel in tier_rels:
                        tier_id = tier_rel.get("id")
                        if tier_id:
                            entitled_tiers.append(tier_id)

                    # Add this patron to the result with their entitled tiers
                    result[discord_id] = entitled_tiers

                    # Debug info
                    if entitled_tiers:
                        tier_names = [tier_map.get(tier_id, {}).get("title", f"Unknown ({tier_id})") for tier_id in
                                      entitled_tiers]
                        debug_info.append(f"Patron {discord_id} entitled to tiers: {', '.join(tier_names)}")
                    else:
                        debug_info.append(f"Patron {discord_id} has no entitled tiers")

                except Exception as e:
                    debug_info.append(f"Error processing patron {patron_count}: {e}")

            if user:
                print(
                    f"[PatreonCore] Successfully mapped {len(result)} Discord users out of {active_patrons} active patrons")

            debug_info.append(
                f"Successfully processed {len(result)} patrons with Discord accounts out of {active_patrons} active patrons")
            self.last_processing_debug = debug_info
            return result

        except Exception as e:
            import traceback
            error_text = traceback.format_exc()
            if user:
                print(f"[PatreonCore] Error processing patrons: {e}")
                print(error_text)
            debug_info.append(f"Error processing patrons: {e}")
            debug_info.append(error_text)
            self.last_processing_debug = debug_info
            return {}

    # Then add a new command to report on the processing
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def patrondebug(self, ctx):
        """Display detailed debug info about the last patron processing"""
        if not hasattr(self, 'last_processing_debug') or not self.last_processing_debug:
            await ctx.send("No patron processing debug information available.")
            return

        # Send in chunks to avoid message size limits
        chunks = []
        current_chunk = "### Patron Processing Debug Info:\n"

        for line in self.last_processing_debug:
            if len(current_chunk) + len(line) + 1 > 1900:
                chunks.append(current_chunk)
                current_chunk = line + "\n"
            else:
                current_chunk += line + "\n"

        if current_chunk:
            chunks.append(current_chunk)

        for i, chunk in enumerate(chunks):
            await ctx.send(f"{chunk}\n[Part {i + 1}/{len(chunks)}]")

    async def log_message(self, message):
        """Send a log message to the configured log channel"""
        if not self.log_channel_id:
            return

        try:
            channel = self.bot.get_channel(self.log_channel_id)
            if channel:
                await channel.send(message)
        except Exception as e:
            print(f"[PatreonCore] Error sending log message: {e}")

    async def _queue_pending_tier_validation(self, user_id, current_tier, proposed_tier, source):
        key = self._user_key(user_id)
        source_label = str(source or "unknown")
        current_tier = self._to_int(current_tier, 0)
        proposed_tier = self._to_int(proposed_tier, 0)
        existing = self.pending_tier_validations.get(key)
        if (
            existing is not None
            and self._to_int(existing.get("current_tier"), -1) == current_tier
            and self._to_int(existing.get("proposed_tier"), -1) == proposed_tier
            and str(existing.get("source")) == source_label
        ):
            return
        should_notify = True
        now_iso = self._utcnow_iso()
        self.pending_tier_validations[key] = {
            "current_tier": current_tier,
            "proposed_tier": proposed_tier,
            "source": source_label,
            "detected_at": existing.get("detected_at", now_iso) if existing else now_iso,
            "updated_at": now_iso,
        }
        self._save_config()
        if should_notify:
            await self._notify_pending_tier_validation(self._to_int(user_id, 0))

    async def _notify_pending_tier_validation(self, user_id):
        pending = self.pending_tier_validations.get(self._user_key(user_id))
        if not pending:
            return
        source = str(pending.get("source", "unknown"))
        source_display = {
            "patreon_api": "Patreon API",
            "patreon_api_removed": "Patreon API (missing patron)",
            "discord_roles": "Discord role sync",
        }.get(source, source)
        current_tier = self._to_int(pending.get("current_tier"), 0)
        proposed_tier = self._to_int(pending.get("proposed_tier"), 0)
        message = (
            f"⚠️ Pending tier validation for <@{user_id}> (`{user_id}`)\n"
            f"Current locked tier: `{current_tier}` | Incoming tier: `{proposed_tier}` from `{source_display}`.\n"
            f"Use `tierapprove {user_id}` to accept or `tierreject {user_id}` to keep the locked tier."
        )
        if self.log_channel_id:
            channel = self.bot.get_channel(self.log_channel_id)
            if channel:
                try:
                    await channel.send(message)
                    return
                except Exception as e:
                    print(f"[PatreonCore] Error notifying pending tier validation: {e}")
        guild = self.bot.get_guild(self.guild_id) if self.guild_id else None
        if guild and guild.system_channel:
            try:
                await guild.system_channel.send(message)
                return
            except Exception as e:
                print(f"[PatreonCore] Error notifying in system channel: {e}")
        print(f"[PatreonCore] {message}")

    async def update_patron_tier_in_db(self, discord_id, tier_level, source="unknown", force=False, conn=None):
        """Update the patron tier in DB while respecting manual overrides."""
        try:
            user_id = self._normalize_user_id(discord_id)
            desired_tier = self._to_int(tier_level, 0)
            if conn is None:
                async with self.bot.pool.acquire() as owned_conn:
                    return await self.update_patron_tier_in_db(
                        user_id,
                        desired_tier,
                        source=source,
                        force=force,
                        conn=owned_conn,
                    )

            row = await conn.fetchrow(
                'SELECT "tier" FROM profile WHERE "user" = $1',
                user_id,
            )
            if row is None:
                return "missing_profile"

            current_tier = self._to_int(row["tier"], 0)
            override = self.get_manual_tier_override(user_id)

            if override and not force and source != "manual":
                locked_tier = self._to_int(override.get("tier"), current_tier)

                # Enforce locked tier in DB if something else changed it.
                if current_tier != locked_tier:
                    await conn.execute(
                        'UPDATE profile SET "tier" = $1 WHERE "user" = $2',
                        locked_tier,
                        user_id,
                    )
                    current_tier = locked_tier
                    try:
                        await self.bot.clear_donator_cache(user_id)
                    except Exception:
                        pass

                if desired_tier != locked_tier:
                    await self._queue_pending_tier_validation(
                        user_id=user_id,
                        current_tier=locked_tier,
                        proposed_tier=desired_tier,
                        source=source,
                    )
                    print(
                        f"[PatreonCore] Blocked tier overwrite for {user_id}: "
                        f"locked {locked_tier}, incoming {desired_tier} from {source}."
                    )
                    return "pending_validation"
                return "locked_no_change"

            if current_tier == desired_tier:
                return "unchanged"

            await conn.execute(
                'UPDATE profile SET "tier" = $1 WHERE "user" = $2',
                desired_tier,
                user_id,
            )
            try:
                await self.bot.clear_donator_cache(user_id)
            except Exception:
                pass
            print(f"[PatreonCore] Updated tier for user {user_id} to {desired_tier} (source={source})")
            return "updated"

        except Exception as e:
            print(f"[PatreonCore] Error updating database for user {discord_id}: {e}")
            import traceback
            print(traceback.format_exc())
            return "error"

    @tasks.loop(minutes=30)
    async def update_roles(self):
        """Regular task to sync patron tiers. Role-driven mode does not require Patreon API."""
        user_id = 524674960153903126  # ID of the user to send the message to
        user = self.bot.get_user(user_id)
        await self.bot.wait_until_ready()

        # Skip if guild ID is not set
        if not self.guild_id:
            print("[PatreonCore] Guild ID not set. Skipping role update!")
            return

        print(f"[PatreonCore] [{datetime.now(timezone.utc)}] Updating Patreon roles...")

        try:
            # Role-driven mode: treat Discord roles as the source of truth.
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                print(f"[PatreonCore] Could not find guild with ID {self.guild_id}!")
                return

            if self.role_driven_sync:
                self.patrons_data = await self._snapshot_role_driven_patrons(guild)
                self.last_updated = datetime.now(timezone.utc)
                print(
                    f"[PatreonCore] Role-driven sync complete! "
                    f"Tracked {len(self.patrons_data)} patrons from Discord roles."
                )
                return

            # Fetch and process patron data
            patrons, included = await self.fetch_patrons()
            if not patrons:
                print("[PatreonCore] No patrons fetched from API. Keeping previous snapshot.")
                return

            new_data = self.process_patrons(patrons, included)
            previous_member_ids = set(self.patrons_data.keys())

            # Update roles for all members
            roles_added = 0
            roles_removed = 0

            for member_id, tier_ids in new_data.items():
                try:
                    tier_level_from_api = self._tier_level_from_entitled_tiers(tier_ids)
                    tier_level_from_roles = 0

                    try:
                        member = await guild.fetch_member(int(member_id))
                    except discord.errors.NotFound:
                        member = None

                    if member:
                        # Determine which roles the member should have
                        should_have_roles = set()
                        for tier_id in tier_ids:
                            mapped_role = self.tier_role_mapping.get(str(tier_id))
                            if mapped_role is None:
                                mapped_role = self.tier_role_mapping.get(tier_id)
                            if mapped_role is not None:
                                should_have_roles.add(int(mapped_role))

                        # Determine which patron roles the member currently has
                        has_roles = set()
                        for role_id in self.tier_role_mapping.values():
                            role = guild.get_role(int(role_id))
                            if role and role in member.roles:
                                has_roles.add(int(role_id))

                        # Add missing roles
                        for role_id in should_have_roles - has_roles:
                            role = guild.get_role(int(role_id))
                            if role:
                                await member.add_roles(role)
                                print(f"[PatreonCore] Added role {role.name} to {member.display_name}")
                                roles_added += 1

                        # Remove roles they should no longer have
                        for role_id in has_roles - should_have_roles:
                            role = guild.get_role(int(role_id))
                            if role:
                                await member.remove_roles(role)
                                print(f"[PatreonCore] Removed role {role.name} from {member.display_name}")
                                roles_removed += 1

                        for role_id in should_have_roles:
                            tier_level_from_roles = max(
                                tier_level_from_roles,
                                int(self.role_tier_mapping.get(int(role_id), 0)),
                            )
                    else:
                        print(f"[PatreonCore] Member {member_id} not found in server")

                    tier_level = max(tier_level_from_api, tier_level_from_roles)

                    # Update the database with tier level.
                    await self.update_patron_tier_in_db(
                        member_id,
                        tier_level,
                        source="patreon_api",
                    )

                except Exception as e:
                    print(f"[PatreonCore] Error updating roles for member {member_id}: {e}")

            # Patrons no longer returned by API should lose patron tier in DB.
            for removed_member_id in previous_member_ids - set(new_data.keys()):
                await self.update_patron_tier_in_db(
                    removed_member_id,
                    0,
                    source="patreon_api_removed",
                )

            # Update stored data
            self.patrons_data = new_data
            self.last_updated = datetime.now(timezone.utc)

            # Log results
            update_msg = (f"Patreon sync complete! Processed {len(self.patrons_data)} patrons. "
                          f"Added {roles_added} roles and removed {roles_removed} roles.")
            print(f"[PatreonCore] {update_msg}")
            

        except Exception as e:
            import traceback
            error_message = f"Error occurred: {e}\n"
            error_message += traceback.format_exc()
            print(error_message)

    async def fetch_tiers(self):
        """Fetch tier information with proper names using the V2 API"""
        try:
            # First ensure we have the campaign ID
            if not self.patreon_campaign_id:
                await self.get_campaign_id()

            if not self.patreon_campaign_id:
                print("[PatreonCore] No campaign ID available. Cannot fetch tiers.")
                return {}

            # First get all tier IDs
            tier_ids = []
            async with aiohttp.ClientSession() as session:
                # Get campaign with tier relationship data
                url = f"https://www.patreon.com/api/oauth2/v2/campaigns/{self.patreon_campaign_id}?include=tiers"
                headers = {"Authorization": f"Bearer {self.access_token}"}

                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()

                        # Extract tier IDs from included data
                        if "included" in data:
                            for item in data["included"]:
                                if item.get("type") == "tier":
                                    tier_id = item.get("id")
                                    if tier_id:
                                        tier_ids.append(tier_id)

                        # If no tiers found in included, try relationships
                        if not tier_ids and "data" in data and isinstance(data["data"], list) and len(data["data"]) > 0:
                            relationships = data["data"][0].get("relationships", {})
                            if "tiers" in relationships and "data" in relationships["tiers"]:
                                for tier in relationships["tiers"]["data"]:
                                    tier_id = tier.get("id")
                                    if tier_id:
                                        tier_ids.append(tier_id)
                    else:
                        print(f"[PatreonCore] Error fetching campaign: {response.status}")
                        return {}

            # Now get tier details with separate API calls
            tiers = {}
            async with aiohttp.ClientSession() as session:
                for tier_id in tier_ids:
                    url = f"https://www.patreon.com/api/oauth2/v2/tiers/{tier_id}?fields%5Btier%5D=title,description,amount_cents"
                    headers = {"Authorization": f"Bearer {self.access_token}"}

                    try:
                        async with session.get(url, headers=headers) as response:
                            if response.status == 200:
                                tier_data = await response.json()

                                # Extract tier title
                                tier_title = tier_data.get("data", {}).get("attributes", {}).get("title")
                                if tier_title:
                                    tiers[tier_id] = tier_title
                                    print(f"[PatreonCore] Found tier: {tier_id} - {tier_title}")
                                else:
                                    # Fallback to generic name if title not found
                                    tiers[tier_id] = f"Tier {tier_id}"
                                    print(f"[PatreonCore] Found tier with no title: {tier_id}")
                            else:
                                print(f"[PatreonCore] Error fetching tier {tier_id}: {response.status}")
                                # Use fallback name
                                tiers[tier_id] = f"Tier {tier_id}"
                    except Exception as e:
                        print(f"[PatreonCore] Error processing tier {tier_id}: {e}")
                        # Use fallback name
                        tiers[tier_id] = f"Tier {tier_id}"

            # If no tiers found at all, use hardcoded values as fallback
            if not tiers:
                print("[PatreonCore] Using fallback tier IDs")
                tiers = {
                    "21626836": "Tier 21626836",
                    "21626838": "Tier 21626838",
                    "22117682": "Tier 22117682",
                    "22117697": "Tier 22117697",
                    "22117706": "Tier 22117706"
                }

            return tiers

        except Exception as e:
            print(f"[PatreonCore] Error in fetch_tiers: {e}")
            import traceback
            traceback.print_exc()
            return {}

    @commands.command()
    @is_gm()
    async def forcesync(self, ctx):
        """Force synchronization of Patreon pledges with Discord roles"""
        try:
            await ctx.send("🔄 Forcing Patreon role sync... This may take a minute.")

            # Cancel existing task if running
            if self.update_roles.is_running():
                self.update_roles.cancel()

            # Run update immediately
            await self.update_roles()

            if self.last_updated:
                await ctx.send(
                    f"✅ Patreon role sync complete! Last updated: {self.last_updated.strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                await ctx.send("⚠️ Patreon role sync attempted but may have failed. Check logs for details.")

            # Restart the task
            if not self.update_roles.is_running():
                self.update_roles.start()
        except Exception as e:
            import traceback
            error_message = f"Error occurred: {e}\n"
            error_message += traceback.format_exc()
            await ctx.send(error_message)

    @commands.command()
    @is_gm()
    async def patronstatus(self, ctx):
        """Show status of Patreon integration"""
        embed = discord.Embed(
            title="Patreon Bot Status",
            color=0xFF5441
        )

        # Check if configured
        if not self.guild_id:
            embed.description = "⚠️ Patreon integration is not fully configured. Use `!patreonsetup` to configure."
            await ctx.send(embed=embed)
            return

        # Basic info
        embed.add_field(name="Guild ID", value=str(self.guild_id), inline=True)
        embed.add_field(name="Update Interval", value=f"{self.check_interval // 60} minutes", inline=True)
        embed.add_field(
            name="Sync Mode",
            value="Role-driven" if self.role_driven_sync else "Patreon API",
            inline=True,
        )
        embed.add_field(name="Manual Overrides", value=str(len(self.manual_tier_overrides)), inline=True)
        embed.add_field(name="Pending Validations", value=str(len(self.pending_tier_validations)), inline=True)

        if self.log_channel_id:
            log_channel = self.bot.get_channel(self.log_channel_id)
            embed.add_field(name="Log Channel", value=log_channel.mention if log_channel else "Invalid Channel",
                            inline=True)
        else:
            embed.add_field(name="Log Channel", value="Not set", inline=True)

        # Token info
        if self.role_driven_sync:
            embed.add_field(name="Token Status", value="Not required in role-driven mode", inline=False)
        else:
            now = datetime.now(timezone.utc)
            if self.token_expires_at > now:
                expires_in = self.token_expires_at - now
                token_status = f"Valid (Expires in {expires_in.days} days, {expires_in.seconds // 3600} hours)"
            else:
                token_status = "Expired! Will try to refresh on next update."
            embed.add_field(name="Token Status", value=token_status, inline=False)

        # Tier mappings
        if self.tier_role_mapping:
            tier_mappings = []
            guild = self.bot.get_guild(self.guild_id)

            for tier_id, role_id in self.tier_role_mapping.items():
                role = guild.get_role(int(role_id)) if guild else None
                role_name = role.name if role else "Invalid Role"
                tier_mappings.append(f"• Tier {tier_id} → {role_name}")

            embed.add_field(name="Tier Mappings", value="\n".join(tier_mappings), inline=False)
        else:
            embed.add_field(name="Tier Mappings", value="No tier mappings configured", inline=False)

        # Patron info
        if self.last_updated:
            embed.add_field(name="Patrons Tracked", value=str(len(self.patrons_data)), inline=True)
            embed.add_field(name="Last Updated", value=self.last_updated.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
            embed.set_footer(
                text=f"Next update in approximately {self.update_roles.next_iteration - datetime.now(timezone.utc)} (HH:MM:SS)")
        else:
            embed.add_field(name="Status", value="No patron data fetched yet", inline=False)

        await ctx.send(embed=embed)

    @commands.command()
    @is_gm()
    async def patronlist(self, ctx):
        """List all patrons with their roles"""
        if not self.patrons_data:
            await ctx.send("❌ No patron data available.")
            return

        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            await ctx.send("❌ Could not find guild!")
            return

        # Create a reverse mapping from role IDs to tier names
        role_to_tier = {}
        for tier_id, role_id in self.tier_role_mapping.items():
            role = guild.get_role(int(role_id))
            if role:
                role_to_tier[int(role_id)] = role.name
        for role_id in self.role_tier_mapping:
            role = guild.get_role(int(role_id))
            if role:
                role_to_tier[int(role_id)] = role.name

        embed = discord.Embed(
            title="Patreon Supporters",
            description=f"Total patrons: {len(self.patrons_data)}",
            color=0xFF5441
        )

        # Limit to 25 patrons to avoid hitting embed limits
        count = 0
        for discord_id, tier_ids in list(self.patrons_data.items())[:25]:
            try:
                member = await guild.fetch_member(int(discord_id))
                if member:
                    role_names = []
                    for tier_id in tier_ids:
                        role_id = None
                        if tier_id in self.tier_role_mapping:
                            role_id = int(self.tier_role_mapping[tier_id])
                        else:
                            try:
                                parsed = int(tier_id)
                                if parsed in role_to_tier:
                                    role_id = parsed
                            except (TypeError, ValueError):
                                role_id = None
                        if role_id is not None and role_id in role_to_tier:
                            role_names.append(role_to_tier[role_id])

                    embed.add_field(
                        name=member.display_name,
                        value=", ".join(role_names) if role_names else "No tier",
                        inline=True
                    )
                    count += 1
            except:
                pass

        if count == 0:
            await ctx.send("❌ No patron data could be displayed.")
        else:
            if count < len(self.patrons_data):
                embed.set_footer(text=f"Showing {count} of {len(self.patrons_data)} patrons")
            await ctx.send(embed=embed)

    @commands.command(hidden=True)
    @is_gm()
    async def tierpending(self, ctx):
        """List pending tier changes blocked by manual overrides."""
        if not self.pending_tier_validations:
            return await ctx.send("✅ No pending tier validations.")

        lines = []
        for user_key, payload in sorted(self.pending_tier_validations.items(), key=lambda x: x[0]):
            user_id = self._to_int(user_key, 0)
            current_tier = self._to_int(payload.get("current_tier"), 0)
            proposed_tier = self._to_int(payload.get("proposed_tier"), 0)
            source = payload.get("source", "unknown")
            detected_at = payload.get("detected_at", "unknown")
            lines.append(
                f"`{user_id}`: `{current_tier}` -> `{proposed_tier}` from `{source}` at `{detected_at}`"
            )

        header = "**Pending Tier Validations**\n"
        chunk = header
        for line in lines:
            if len(chunk) + len(line) + 1 > 1900:
                await ctx.send(chunk)
                chunk = line + "\n"
            else:
                chunk += line + "\n"
        if chunk.strip():
            await ctx.send(chunk)

    @commands.command(hidden=True)
    @is_gm()
    async def tierapprove(self, ctx, user_id: int):
        """Approve pending external tier for a manually overridden user."""
        key = self._user_key(user_id)
        pending = self.pending_tier_validations.get(key)
        if not pending:
            return await ctx.send(f"❌ No pending validation for `{user_id}`.")

        proposed_tier = self._to_int(pending.get("proposed_tier"), 0)
        source = str(pending.get("source", "unknown"))
        status = await self.update_patron_tier_in_db(
            user_id,
            proposed_tier,
            source=f"gm_approved:{source}",
            force=True,
        )
        if status == "missing_profile":
            return await ctx.send(f"❌ User `{user_id}` has no profile row.")
        if status == "error":
            return await ctx.send(f"❌ Failed to apply approved tier for `{user_id}`. Check logs.")

        self.set_manual_tier_override(user_id, proposed_tier, setter_id=ctx.author.id)
        self.pending_tier_validations.pop(key, None)
        self._save_config()
        await ctx.send(
            f"✅ Approved tier update for `{user_id}`. "
            f"Tier is now `{proposed_tier}` and remains manually locked."
        )

    @commands.command(hidden=True)
    @is_gm()
    async def tierreject(self, ctx, user_id: int):
        """Reject pending external tier and keep locked tier."""
        key = self._user_key(user_id)
        pending = self.pending_tier_validations.get(key)
        if not pending:
            return await ctx.send(f"❌ No pending validation for `{user_id}`.")

        override = self.get_manual_tier_override(user_id)
        locked_tier = self._to_int(
            override.get("tier") if override else pending.get("current_tier"),
            0,
        )
        self.pending_tier_validations.pop(key, None)
        self._save_config()
        await self.update_patron_tier_in_db(
            user_id,
            locked_tier,
            source="manual",
            force=True,
        )
        await ctx.send(
            f"✅ Rejected pending tier update for `{user_id}`. "
            f"Kept locked tier `{locked_tier}`."
        )

    @commands.command(hidden=True, aliases=["tierunlock", "cleartierlock"])
    @is_gm()
    async def cleartieroverride(self, ctx, user_id: int):
        """Remove manual tier lock so Patreon sync can update this user automatically."""
        had_override = self.clear_manual_tier_override(user_id)
        if had_override:
            await ctx.send(
                f"✅ Cleared manual tier override for `{user_id}`. "
                "Patreon sync can update this user again."
            )
        else:
            await ctx.send(f"ℹ️ No manual tier override found for `{user_id}`.")

    @commands.command(hidden=True)
    @is_gm()
    async def checktier(self, ctx, user_id: int):
        """Show current tier state for one user (DB tier + override + pending validation)."""
        row = await self.bot.pool.fetchrow(
            'SELECT "user", "name", "tier" FROM profile WHERE "user" = $1',
            user_id,
        )
        if row is None:
            return await ctx.send(f"❌ No profile found for `{user_id}`.")

        db_tier = self._to_int(row["tier"], 0)
        name = row["name"] or "Unknown"
        override = self.get_manual_tier_override(user_id)
        pending = self.pending_tier_validations.get(self._user_key(user_id))

        lines = [
            f"**Tier State for `{user_id}` ({name})**",
            f"Database tier: `{db_tier}`",
        ]

        if override:
            lines.append(
                "Manual override: "
                f"`{self._to_int(override.get('tier'), db_tier)}` "
                f"(set_at: `{override.get('set_at', 'unknown')}`; "
                f"set_by: `{override.get('set_by', 'unknown')}`)"
            )
        else:
            lines.append("Manual override: `none`")

        if pending:
            lines.append(
                "Pending validation: "
                f"`{self._to_int(pending.get('current_tier'), db_tier)}` -> "
                f"`{self._to_int(pending.get('proposed_tier'), db_tier)}` "
                f"from `{pending.get('source', 'unknown')}` "
                f"(detected: `{pending.get('detected_at', 'unknown')}`)"
            )
        else:
            lines.append("Pending validation: `none`")

        await ctx.send("\n".join(lines))

    @commands.command(hidden=True, aliases=["tiersover0", "tierlistall"])
    @is_gm()
    async def tierlist(self, ctx):
        """List all users in profile with tier > 0."""
        rows = await self.bot.pool.fetch(
            '''
            SELECT "user", "name", COALESCE("tier", 0) AS tier
            FROM profile
            WHERE COALESCE("tier", 0) > 0
            ORDER BY tier DESC, "name" ASC NULLS LAST, "user" ASC
            '''
        )
        if not rows:
            return await ctx.send("No users with tier > 0 found.")

        tier_counts = {}
        for row in rows:
            tier_val = self._to_int(row["tier"], 0)
            tier_counts[tier_val] = tier_counts.get(tier_val, 0) + 1

        summary = " | ".join(
            [f"T{tier}:{count}" for tier, count in sorted(tier_counts.items(), key=lambda x: x[0])]
        )
        header = f"**Users with tier > 0: {len(rows)}** ({summary})\n"

        chunk = header
        for row in rows:
            uid = self._to_int(row["user"], 0)
            uname = row["name"] or "Unknown"
            tier_val = self._to_int(row["tier"], 0)
            line = f"`{uid}` | Tier `{tier_val}` | {uname}\n"
            if len(chunk) + len(line) > 1900:
                await ctx.send(chunk)
                chunk = line
            else:
                chunk += line

        if chunk.strip():
            await ctx.send(chunk)

    @commands.command()
    @is_gm()
    async def refreshtoken(self, ctx):
        """Manually refresh the Patreon access token"""
        await ctx.send("🔄 Attempting to refresh Patreon access token...")

        success = await self.refresh_access_token()

        if success:
            await ctx.send(
                f"✅ Token refreshed successfully! New expiration: {self.token_expires_at.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            await ctx.send("❌ Failed to refresh token. Check logs for details.")
            
    @commands.command()
    @is_gm()
    async def check_tiers(self, ctx):
        """Manually check all patron tiers and provide detailed debugging output"""
        await ctx.send("🔍 Beginning manual tier check...")
        
        if not self.guild_id:
            return await ctx.send("❌ Guild ID not set. Cannot check tiers!")
            
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return await ctx.send(f"❌ Could not find guild with ID {self.guild_id}!")
            
        status_msg = await ctx.send("📊 Fetching patron data...")
        
        try:
            # Fetch latest data if needed
            if not self.patrons_data:
                if self.role_driven_sync:
                    self.patrons_data = await self._snapshot_role_driven_patrons(guild)
                else:
                    patrons, included = await self.fetch_patrons()
                    if not patrons:
                        return await status_msg.edit(content="❌ No patrons fetched. Check logs for details.")
                    self.patrons_data = self.process_patrons(patrons, included)
                
            await status_msg.edit(content=f"📊 Processing {len(self.patrons_data)} patrons...")
            
            debug_output = ["**Tier Check Debug Report**"]
            total_members = 0
            tier_counts = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
            role_debug = []
            
            # Check each member's roles and determine tier
            for member_id, tier_ids in self.patrons_data.items():
                try:
                    member = await guild.fetch_member(int(member_id))
                    if not member:
                        role_debug.append(f"⚠️ Member not found: {member_id}")
                        continue
                        
                    total_members += 1
                    member_debug = [f"**Member:** {member.display_name} ({member_id})\n**Tier IDs:** {tier_ids}"]
                    
                    # Find assigned roles
                    should_have_roles = set()
                    for tier_id in tier_ids:
                        if tier_id in self.tier_role_mapping:
                            role_id = int(self.tier_role_mapping[tier_id])
                            should_have_roles.add(role_id)
                            role = guild.get_role(role_id)
                            member_debug.append(f"- Should have role: {role.name if role else 'Unknown'} ({role_id})")
                        else:
                            try:
                                parsed_role_id = int(tier_id)
                            except (TypeError, ValueError):
                                parsed_role_id = None
                            if parsed_role_id is not None:
                                role = guild.get_role(parsed_role_id)
                                if role:
                                    should_have_roles.add(parsed_role_id)
                                    member_debug.append(
                                        f"- Should have role: {role.name} ({parsed_role_id})"
                                    )
                    
                    # Check current roles
                    has_roles = set()
                    patron_roles = []
                    for role_id in self.tier_role_mapping.values():
                        role = guild.get_role(int(role_id))
                        if role and role in member.roles:
                            has_roles.add(int(role_id))
                            patron_roles.append(role.name)
                    
                    member_debug.append(f"- Current patron roles: {', '.join(patron_roles) if patron_roles else 'None'}")
                    
                    # Determine tier level from explicit role IDs first, then fallback by name.
                    tier_level = 0
                    roles_checked = []
                    for role in member.roles:
                        role_name = role.name
                        roles_checked.append(role_name)
                        if role.id in self.role_tier_mapping:
                            mapped_tier = int(self.role_tier_mapping[role.id])
                            tier_level = max(tier_level, mapped_tier)
                            member_debug.append(f"- Found mapped Patreon role: {role_name} (tier {mapped_tier})")
                        elif "Ragnarok" in role_name:
                            tier_level = max(tier_level, 4)
                            member_debug.append(f"- Found Ragnarok role: {role_name}")
                        elif "Legendary" in role_name:
                            tier_level = max(tier_level, 3)
                            member_debug.append(f"- Found Legendary role: {role_name}")
                        elif "Warrior" in role_name:
                            tier_level = max(tier_level, 2)
                            member_debug.append(f"- Found Warrior role: {role_name}")
                        elif "Adventurer" in role_name:
                            tier_level = max(tier_level, 1)
                            member_debug.append(f"- Found Adventurer role: {role_name}")
                    
                    member_debug.append(f"- All roles checked: {', '.join(roles_checked)}")
                    tier_counts[tier_level] += 1
                    member_debug.append(f"- **Calculated tier level: {tier_level}**")
                    
                    # Check if database update would work
                    try:
                        await self.bot.pool.execute(
                            'SELECT 1 FROM profile WHERE "user"=$1', int(member_id)
                        )
                        member_debug.append(f"- Database record exists: ✅")
                    except Exception as db_error:
                        member_debug.append(f"- Database check failed: ❌ ({str(db_error)})")
                    
                    # Add this member's debug info
                    role_debug.append("\n".join(member_debug))
                    
                except Exception as e:
                    role_debug.append(f"❌ Error processing {member_id}: {str(e)}")
            
            # Create summary
            debug_output.append(f"**Total members processed:** {total_members}")
            debug_output.append(f"**Tier level counts:**")
            debug_output.append(f"- Tier 0 (No tier): {tier_counts[0]}")
            debug_output.append(f"- Tier 1: {tier_counts[1]}")
            debug_output.append(f"- Tier 2: {tier_counts[2]}")
            debug_output.append(f"- Tier 3: {tier_counts[3]}")
            debug_output.append(f"- Tier 4: {tier_counts[4]}")
            
            # Send summary
            await status_msg.edit(content="✅ Tier check complete!")
            await ctx.send("\n".join(debug_output))
            
            # Send member details in chunks to avoid message length limits
            chunks = []
            current_chunk = []
            current_length = 0
            
            for item in role_debug:
                # Discord has a 2000 character limit
                if current_length + len(item) + 2 > 1900:  # Add some buffer
                    chunks.append("\n\n".join(current_chunk))
                    current_chunk = [item]
                    current_length = len(item)
                else:
                    current_chunk.append(item)
                    current_length += len(item) + 2  # +2 for the newlines
            
            if current_chunk:
                chunks.append("\n\n".join(current_chunk))
            
            for i, chunk in enumerate(chunks):
                await ctx.send(f"**Member Details ({i+1}/{len(chunks)}):**\n\n{chunk}")
                
        except Exception as e:
            import traceback
            error_message = f"Error occurred: {e}\n"
            error_message += traceback.format_exc()
            await ctx.send(f"❌ Error during tier check: ```{error_message[:1500]}```")
            print(error_message)
            
    async def _is_support_server_booster(self, user_id: int) -> bool:
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            support_server_id = getattr(getattr(self.bot.config, "game", None), "support_server_id", None)
            if support_server_id:
                guild = self.bot.get_guild(int(support_server_id))
        if guild is None:
            return False

        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return False

        booster_role = guild.get_role(self.booster_role_id)
        if booster_role is not None and booster_role in member.roles:
            return True
        return member.premium_since is not None

    def _monthly_weapon_token_amount(self, tier: int, is_booster: bool) -> int:
        amount = self.WEAPON_TOKEN_BASE

        if is_booster:
            amount = max(amount, self.WEAPON_TOKEN_BOOSTER_MIN)

        if tier >= 3:
            amount = max(amount, self.WEAPON_TOKEN_TIER_MINIMUMS.get(3, amount))
        elif tier == 2:
            amount = max(amount, self.WEAPON_TOKEN_TIER_MINIMUMS.get(2, amount))
        elif tier >= 1:
            amount = max(amount, self.WEAPON_TOKEN_TIER_MINIMUMS.get(1, amount))

        return amount

    def _monthly_weapon_token_distribution_text(self) -> str:
        tier1 = self.WEAPON_TOKEN_TIER_MINIMUMS.get(1, self.WEAPON_TOKEN_BASE)
        tier2 = self.WEAPON_TOKEN_TIER_MINIMUMS.get(2, tier1)
        tier3 = self.WEAPON_TOKEN_TIER_MINIMUMS.get(3, tier2)
        return (
            f"Distribution: {self.WEAPON_TOKEN_BASE} (normal), "
            f"{tier1} (tier 1), {tier2} (tier 2), {tier3} (tier 3+). "
            f"Booster minimum: {self.WEAPON_TOKEN_BOOSTER_MIN}."
        )

    @commands.command()
    @user_cooldown(2764800)  # 32 days in seconds (32 * 24 * 60 * 60)
    async def redeemweapontokens(self, ctx):
        """Redeem monthly weapon tokens (3/5/7/10) based on tier and booster status."""
        async with self.bot.pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT COALESCE("tier", 0) AS tier, COALESCE("weapontoken", 0) AS weapontoken FROM profile WHERE "user"=$1',
                ctx.author.id,
            )
            if row is None:
                await self.bot.reset_cooldown(ctx)
                return await ctx.send("❌ You need to create a character before redeeming weapon tokens.")

            patron_tier = self._to_int(row["tier"], 0)
            is_booster = await self._is_support_server_booster(ctx.author.id)
            tokens_to_add = self._monthly_weapon_token_amount(patron_tier, is_booster)

            try:
                await conn.execute(
                    'UPDATE profile SET "weapontoken"="weapontoken"+$1 WHERE "user"=$2',
                    tokens_to_add,
                    ctx.author.id,
                )

                new_balance = await conn.fetchval(
                    'SELECT COALESCE("weapontoken", 0) FROM profile WHERE "user"=$1',
                    ctx.author.id,
                )

                if self.log_channel_id:
                    log_channel = self.bot.get_channel(self.log_channel_id)
                    if log_channel:
                        await log_channel.send(
                            "⚔️ **Weapon Token Redemption**: "
                            f"{ctx.author.mention} ({ctx.author.id}) redeemed {tokens_to_add} weapon tokens "
                            f"(tier={patron_tier}, booster={'yes' if is_booster else 'no'})."
                        )

                embed = discord.Embed(
                    title="⚔️ Weapon Tokens Redeemed!",
                    description="Your monthly weapon token redemption is complete.",
                    color=0x3CB371,
                )
                embed.add_field(name="Tokens Added", value=str(tokens_to_add), inline=True)
                embed.add_field(name="New Balance", value=f"{new_balance:,}", inline=True)
                embed.add_field(name="Patron Tier", value=f"Tier {patron_tier}", inline=True)
                embed.add_field(name="Server Booster", value="Yes" if is_booster else "No", inline=True)
                embed.set_footer(text=self._monthly_weapon_token_distribution_text())

                await ctx.send(embed=embed)

            except Exception as e:
                await self.bot.reset_cooldown(ctx)
                await ctx.send(f"❌ Error updating your weapon tokens: {e}")
                print(f"[PatreonCore] Error in redeemweapontokens for {ctx.author.id}: {e}")


# Function to add this cog to your bot
async def setup(bot):
    await bot.add_cog(PatreonCore(bot))
