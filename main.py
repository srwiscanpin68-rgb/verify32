# เครดิต
# By.ivzex
# By.patxez
# DEV.manpop79
# DEV.Fugus1234
# ฝากติดตามRoblox พวกผมด้วยนะค้าบ
# นำไปขายต่อได้ ให้เครดิตพวกผมด้วยนะค้าบ❤️
import os
import asyncio
import json
import re
import sqlite3
import io
import requests
import discord
import uvicorn
from discord.ext import commands
from discord import app_commands
from fastapi import FastAPI, Request
from contextlib import asynccontextmanager

# =========================
# CONFIGURATION
# =========================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
PORT = int(os.getenv("PORT", 8888))
DB_PATH = os.getenv("DB_PATH", "database.db")

DEFAULT_SETTINGS = {
    "roblox_group_id": 226834839,
    "roblox_group_url": "https://www.roblox.com/groups/226834839",
    "roblox_map_url": "https://www.roblox.com/th/games/78189317414125/By",
    "verified_role_id": 1479443343367995579,
    "developer_role_id": 1479469155399766129,
    "ticket_staff_role_id": 1508479215908028544,
    "transcript_channel_id": None,
    "ticket_category_id": None,
    "role_ids": {
        "or": 1479699133001629797,
        "of_low": 1479699314078122094,
        "of_high": 1479699471603470432,
        "guest": None,
    },
    "rank_prefixes": {
        "or-1": "OR-1, PC",
        "or-2": "OR-2, PEC",
        "or-3": "OR-3, CPL",
        "or-4": "OR-4, SGT",
        "or-5": "OR-5, SSG",
        "or-6": "OR-6/OR-7, SFC",
        "or-7": "OR-6/OR-7, SFC",
        "or-8": "OR-8/OR-9, MSG",
        "or-9": "OR-8/OR-9, MSG",
        "of-1a": "OF-1A, LTP",
        "of-1b": "OF-1B, 1LT",
        "of-2": "OF-2, CPT",
        "of-3": "OF-3, MAJ",
        "of-4": "OF-4, LTC",
        "of-5": "OF-5, COL",
        "of-6": "OF-6, SRCOL",
        "of-7": "OF-7, PMG",
        "of-8": "OF-8, MG",
        "of-9": "OF-9, GEN",
    },
}

DEVELOPER_IDS = [5711452462]
VERIFIED_EMOJI = "✅"


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                discord_id TEXT PRIMARY KEY,
                roblox_id TEXT,
                roblox_username TEXT,
                verified INTEGER DEFAULT 0,
                pending_roblox_username TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id TEXT PRIMARY KEY,
                settings_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS active_tickets (
                channel_id TEXT PRIMARY KEY,
                guild_id TEXT,
                user_id TEXT,
                ticket_type TEXT
            )
            """
        )


def get_guild_settings(guild_id):
    if not guild_id:
        return json.loads(json.dumps(DEFAULT_SETTINGS))
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("SELECT settings_json FROM guild_settings WHERE guild_id = ?", (str(guild_id),))
        row = cursor.fetchone()
        
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    if row:
        try:
            saved = json.loads(row[0])
            if isinstance(saved, dict):
                settings.update({k: v for k, v in saved.items() if k not in {"role_ids", "rank_prefixes"}})
                if isinstance(saved.get("role_ids"), dict):
                    settings["role_ids"].update(saved["role_ids"])
                if isinstance(saved.get("rank_prefixes"), dict):
                    settings["rank_prefixes"].update(saved["rank_prefixes"])
        except Exception as e:
            print(f"Error parsing settings for guild {guild_id}: {e}")
    return settings


def save_guild_settings(guild_id, settings):
    if not guild_id:
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO guild_settings (guild_id, settings_json)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET settings_json = excluded.settings_json
            """,
            (str(guild_id), json.dumps(settings, ensure_ascii=False))
        )


def parse_id(value):
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group()) if match else None


def get_user(discord_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM users WHERE discord_id = ?", (str(discord_id),)).fetchone()


def update_pending(discord_id, username):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO users (discord_id, pending_roblox_username, verified)
            VALUES (?, ?, 0)
            ON CONFLICT(discord_id) DO UPDATE SET
                pending_roblox_username = excluded.pending_roblox_username,
                verified = 0
            """,
            (str(discord_id), str(username).strip().lower()),
        )


# =========================
# BOT SETUP
# =========================
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.add_view(VerifyView())
        self.add_view(ReVerifyView())
        self.add_view(TicketSetupView())
        await self.tree.sync()
        print(f"Dev System All-in-One v8 slash commands synced for {self.user}")


bot = MyBot()


def get_roblox_id_by_name(username):
    try:
        response = requests.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username], "excludeBannedUsers": True},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("data"):
            return data["data"][0]["id"]
    except (requests.RequestException, ValueError) as error:
        print(f"Error fetching Roblox ID: {error}")
    return None


def check_group_membership(roblox_id, group_id):
    try:
        response = requests.get(
            f"https://groups.roblox.com/v1/users/{roblox_id}/groups/roles",
            timeout=15,
        )
        response.raise_for_status()
        for group in response.json().get("data", []):
            if group["group"]["id"] == int(group_id):
                return True, group["role"]["rank"], group["role"]["name"]
    except (requests.RequestException, ValueError, KeyError, TypeError) as error:
        print(f"Error checking group membership: {error}")
    return False, 0, None


def get_prefix_for_rank(rank_val, rank_name, settings):
    prefixes = settings.get("rank_prefixes", {})
    normalized_name = str(rank_name or "").strip().lower()
    numeric_rank = int(rank_val or 0)

    rank_aliases = {
        1: {"or-1"}, 2: {"or-2"}, 3: {"or-3"}, 4: {"or-4"}, 5: {"or-5"},
        6: {"or-6", "or-7"}, 7: {"or-6", "or-7"},
        8: {"of-1a"}, 9: {"of-1b"}, 10: {"of-2"}, 11: {"of-2"},
        12: {"of-3"}, 13: {"of-4"}, 14: {"of-5"}, 15: {"of-6"},
        16: {"of-7"}, 17: {"of-8"}, 18: {"of-9"},
    }

    for rank_key, prefix in prefixes.items():
        key = str(rank_key).strip().lower()
        if key in normalized_name or key in rank_aliases.get(numeric_rank, set()):
            return str(prefix).strip()

    fallback = {
        1: "OR-1, PC", 2: "OR-2, PEC", 3: "OR-3, CPL", 4: "OR-4, SGT",
        5: "OR-5, SSG", 6: "OR-6/OR-7, SFC", 7: "OR-6/OR-7, SFC",
        8: "OF-1A, LTP", 9: "OF-1B, 1LT", 10: "OF-2, CPT", 11: "OF-2, CPT",
        12: "OF-3, MAJ", 13: "OF-4, LTC", 14: "OF-5, COL", 15: "OF-6, SRCOL",
        16: "OF-7, PMG", 17: "OF-8, MG", 18: "OF-9, GEN",
    }
    return fallback.get(numeric_rank, "")


async def update_member_status(discord_id, roblox_id, roblox_username, guild_id=None):
    guild = bot.get_guild(int(guild_id)) if guild_id else None
    if guild is None and bot.guilds:
        guild = bot.guilds[0]
    if guild is None:
        return None, None, None, "Discord server not found"

    settings = get_guild_settings(guild.id)

    try:
        member = await guild.fetch_member(int(discord_id))
        is_in_group, rank_val, rank_name = check_group_membership(roblox_id, settings["roblox_group_id"])
        is_dev = int(roblox_id) in DEVELOPER_IDS

        managed_role_ids = {
            parse_id(settings.get("verified_role_id")),
            parse_id(settings.get("developer_role_id")),
            *{parse_id(value) for value in settings.get("role_ids", {}).values()},
        }
        managed_role_ids.discard(None)

        roles_to_add = [
            role for role in member.roles
            if role != guild.default_role and role.id not in managed_role_ids
        ]
        verified_role = guild.get_role(parse_id(settings.get("verified_role_id")))
        if verified_role:
            roles_to_add.append(verified_role)

        if is_dev:
            developer_role = guild.get_role(parse_id(settings.get("developer_role_id")))
            if developer_role:
                roles_to_add.append(developer_role)
            nickname = f"Dev | {roblox_username}"
            display_rank_name = "Developer"
        elif is_in_group:
            if 1 <= rank_val <= 7:
                rank_role = guild.get_role(parse_id(settings["role_ids"].get("or")))
            elif 8 <= rank_val <= 11:
                rank_role = guild.get_role(parse_id(settings["role_ids"].get("of_low")))
            elif 12 <= rank_val <= 18:
                rank_role = guild.get_role(parse_id(settings["role_ids"].get("of_high")))
            else:
                rank_role = None
            if rank_role:
                roles_to_add.append(rank_role)

            prefix = get_prefix_for_rank(rank_val, rank_name, settings)
            nickname = f"{prefix} | {roblox_username}" if prefix else roblox_username
            display_rank_name = rank_name or "Unknown Rank"
        else:
            guest_role = guild.get_role(parse_id(settings["role_ids"].get("guest")))
            if guest_role:
                roles_to_add.append(guest_role)
            nickname = f"Guest | {roblox_username}"
            display_rank_name = "Guest"

        unique_roles = list({role.id: role for role in roles_to_add}.values())
        await member.edit(roles=unique_roles, nick=nickname[:32])
        return rank_val if not is_dev else 999, member.display_name, display_rank_name, None
    except discord.HTTPException as error:
        if error.code == 50013:
            msg = "Bot lacks permissions or bot role is lower than target role."
        elif error.code == 10007:
            msg = "Member not found in this Discord server."
        else:
            msg = f"Discord Error {error.code}"
        print(f"Update Error [{error.code}]: {error}")
        return None, None, None, msg
    except Exception as error:
        msg = f"Error: {str(error)}"
        print(f"Update Error: {error}")
        return None, None, None, msg


# =========================
# UI: VERIFICATION
# =========================
class VerifyModal(discord.ui.Modal, title="Roblox Verification"):
    username = discord.ui.TextInput(
        label="Roblox Username",
        placeholder="Enter your Roblox username...",
        min_length=3,
        max_length=20,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        input_name = self.username.value
        roblox_id = get_roblox_id_by_name(input_name)
        if not roblox_id:
            await interaction.response.send_message(
                f"❌ Roblox username not found: **{input_name}**. Please check spelling.",
                ephemeral=True,
            )
            return

        settings = get_guild_settings(interaction.guild_id)
        is_dev = int(roblox_id) in DEVELOPER_IDS
        is_in_group, _, _ = check_group_membership(roblox_id, settings["roblox_group_id"])
        
        if not is_in_group and not is_dev:
            embed = discord.Embed(
                title="❌ Roblox Group Required",
                description=(
                    "You are not in our group yet! We have sent you the group link via DM.\n\n"
                    f"**Group Link:** [Click here to join]({settings['roblox_group_url']})"
                ),
                color=0xFF0000,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            try:
                await interaction.user.send(
                    f"Please join our Roblox group before verifying: {settings['roblox_group_url']}"
                )
            except discord.HTTPException:
                pass
            return

        update_pending(interaction.user.id, input_name)
        embed = discord.Embed(title="Please join the game to verify", color=0x00FF00)
        embed.add_field(name="Username", value=f"**{input_name}**", inline=False)
        embed.add_field(
            name="Map",
            value=f"[Click here to join game]({settings['roblox_map_url']})",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ReVerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Update Rank", style=discord.ButtonStyle.success, custom_id="update_rank")
    async def update_rank(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Updating rank, please wait...", ephemeral=True)
        user = get_user(interaction.user.id)
        if not user or not user["roblox_id"]:
            await interaction.edit_original_response(content="❌ Verification data not found.")
            return

        guild_id = interaction.guild.id if interaction.guild else None
        result = await update_member_status(
            interaction.user.id,
            user["roblox_id"],
            user["roblox_username"],
            guild_id,
        )
        rank_val, display_name, rank_name, err_msg = result
        if rank_val is None:
            await interaction.edit_original_response(content=f"❌ Error: {err_msg}")
            return

        embed = discord.Embed(title=f"{VERIFIED_EMOJI} Rank Updated Successfully", color=0x00FF00)
        embed.description = (
            f"Your data is now up to date.\n\n"
            f"**Roblox:** {user['roblox_username']}\n**Current Rank:** {rank_name}"
        )
        await interaction.edit_original_response(content=None, embed=embed)

    @discord.ui.button(label="Change Account", style=discord.ButtonStyle.primary, custom_id="change_acc")
    async def change_acc(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerifyModal())


class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Verify",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="persistent_verify",
    )
    async def start_v(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = get_user(interaction.user.id)
        if user and user["verified"]:
            embed = discord.Embed(title="Roblox Account Already Linked", color=0x3498DB)
            embed.add_field(
                name="Current Data:",
                value=(
                    f"**Roblox:** {user['roblox_username']}\n"
                    f"**Roblox ID:** {user['roblox_id']}\n"
                    f"**Status:** Verified {VERIFIED_EMOJI}"
                ),
                inline=False,
            )
            embed.description = "Want to change account or update rank? Click below."
            await interaction.response.send_message(embed=embed, view=ReVerifyView(), ephemeral=True)
        else:
            await interaction.response.send_modal(VerifyModal())


# =========================
# UI: TICKET SYSTEM (ENGLISH)
# =========================
class TicketSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Report Cheater", value="report_cheater", description="Report players using cheats or rule violations", emoji="❗️"),
            discord.SelectOption(label="Claim Reward", value="claim_reward", description="Claim rewards from events or giveaways", emoji="🪄"),
            discord.SelectOption(label="General Contact", value="general_contact", description="Contact staff for general inquiries", emoji="💭"),
            discord.SelectOption(label="Receive an award", value="receive_award", description="Contact to receive special awards", emoji="🎁"),
        ]
        super().__init__(placeholder="Select a topic to contact", min_values=1, max_values=1, options=options, custom_id="ticket_select_menu")

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return

        settings = get_guild_settings(guild.id)
        staff_role_id = parse_id(settings.get("ticket_staff_role_id", 1508479215908028544))
        category_id = parse_id(settings.get("ticket_category_id"))
        
        category = guild.get_channel(category_id) if category_id else None
        
        # Overwrites for ticket channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
        }
        
        staff_role = guild.get_role(staff_role_id)
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        ticket_type_label = self.values[0]
        channel_name = f"ticket-{interaction.user.name}-{ticket_type_label}"[:30]

        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"Ticket created by {interaction.user.id} | Type: {ticket_type_label}"
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Failed to create ticket channel: {e}", ephemeral=True)
            return

        # Save active ticket
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO active_tickets (channel_id, guild_id, user_id, ticket_type) VALUES (?, ?, ?, ?)",
                (str(ticket_channel.id), str(guild.id), str(interaction.user.id), ticket_type_label)
            )

        tag_mention = f"<@&{staff_role_id}>" if staff_role else ""
        embed = discord.Embed(
            title=f"Ticket: {ticket_type_label.replace('_', ' ').title()}",
            description=f"Hello {interaction.user.mention},\nStaff will assist you shortly. Please describe your issue in detail.",
            color=0x3498DB
        )
        embed.set_footer(text="Use /close_ticket to close this ticket.")
        
        await ticket_channel.send(content=f"{tag_mention} {interaction.user.mention}", embed=embed)
        await interaction.response.send_message(f"✅ Your ticket has been created: {ticket_channel.mention}", ephemeral=True)


class TicketSetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())


class CustomizeAllModal(discord.ui.Modal, title="System Customization"):
    discord_guild_id = discord.ui.TextInput(
        label="Discord Server ID (Guild ID)",
        required=False,
        placeholder="e.g. 123456789",
    )
    group_id = discord.ui.TextInput(
        label="Roblox Group ID",
        required=False,
        placeholder="e.g. 226834839",
    )
    group_url = discord.ui.TextInput(
        label="Roblox Group URL",
        required=False,
        placeholder="https://www.roblox.com/groups/...",
    )
    prefixes = discord.ui.TextInput(
        label="Rank Prefixes (e.g. OF-3=MAJ; OF-4=LTC)",
        required=False,
        style=discord.TextStyle.paragraph,
        placeholder="or-1=PC; of-3=MAJ",
    )
    role_ids = discord.ui.TextInput(
        label="Role IDs (verified=ID; or=ID; staff=ID)",
        required=False,
        style=discord.TextStyle.paragraph,
        placeholder="verified=ID; or=ID; ticket_staff=ID",
    )

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        if not guild_id:
            await interaction.response.send_message("❌ This command must be used inside a server.", ephemeral=True)
            return

        settings = get_guild_settings(guild_id)
        
        if self.discord_guild_id.value.strip():
            gid = parse_id(self.discord_guild_id.value.strip())
            if gid: settings["discord_guild_id"] = gid
            
        if self.group_id.value.strip():
            gid = parse_id(self.group_id.value.strip())
            if gid: settings["roblox_group_id"] = gid
            
        if self.group_url.value.strip():
            settings["roblox_group_url"] = self.group_url.value.strip()

        if self.prefixes.value.strip():
            for item in self.prefixes.value.split(";"):
                if "=" not in item: continue
                k, v = item.split("=", 1)
                k, v = k.strip().lower(), v.strip()
                if k and v:
                    if "," not in v and "-" in k:
                        settings["rank_prefixes"][k] = f"{k.upper()}, {v}"
                    else:
                        settings["rank_prefixes"][k] = v

        if self.role_ids.value.strip():
            for item in self.role_ids.value.split(";"):
                if "=" not in item: continue
                rtype, rid_raw = item.split("=", 1)
                rtype = rtype.strip().lower()
                rid = parse_id(rid_raw)
                if not rid: continue
                
                if rtype in {"verified", "developer", "ticket_staff"}:
                    settings[f"{rtype}_role_id"] = rid
                elif rtype in {"or", "of_low", "of_high", "guest"}:
                    settings["role_ids"][rtype] = rid

        save_guild_settings(guild_id, settings)
        await interaction.response.send_message(
            "✅ Server settings updated successfully!",
            ephemeral=True,
        )


# =========================
# SLASH COMMANDS (ROBLOX & TICKET)
# =========================
@bot.tree.command(name="ยืนยันตัวตน", description="Setup Roblox verification panel (Admin Only)")
@app_commands.default_permissions(administrator=True)
async def setup_verify(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Thai Military Verification",
        description="Please click the button below to start Roblox verification.",
        color=0x2B2D31,
    )
    await interaction.channel.send(embed=embed, view=VerifyView())
    await interaction.response.send_message("✅ Verification panel set up successfully.", ephemeral=True)


async def clear_verification_data(interaction: discord.Interaction):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM users")
    await interaction.response.send_message(
        "⚠️ [Admin] All verification data cleared! Everyone must verify again.",
        ephemeral=True,
    )


@bot.tree.command(name="ล้างข้อมูล", description="Clear all verification data")
@app_commands.default_permissions(administrator=True)
async def reset_db_short(interaction: discord.Interaction):
    await clear_verification_data(interaction)


@bot.tree.command(name="ล้างข้อมูลทั้งหมด", description="Clear all verification data (Legacy)")
@app_commands.default_permissions(administrator=True)
async def reset_db_legacy(interaction: discord.Interaction):
    await clear_verification_data(interaction)


@bot.tree.command(name="ใส่โรล", description="Set role IDs for server categories")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    ประเภท="verified, developer, ticket_staff, or, of_low, of_high, guest",
    โรล="Select Discord Role",
)
@app_commands.choices(
    ประเภท=[
        app_commands.Choice(name="Verified", value="verified"),
        app_commands.Choice(name="Developer", value="developer"),
        app_commands.Choice(name="Ticket Staff", value="ticket_staff"),
        app_commands.Choice(name="OR", value="or"),
        app_commands.Choice(name="OF Low", value="of_low"),
        app_commands.Choice(name="OF High", value="of_high"),
        app_commands.Choice(name="Guest", value="guest"),
    ]
)
async def set_role(interaction: discord.Interaction, ประเภท: app_commands.Choice[str], โรล: discord.Role):
    if not interaction.guild_id:
        await interaction.response.send_message("❌ This command must be used inside a server.", ephemeral=True)
        return

    settings = get_guild_settings(interaction.guild_id)
    role_type = ประเภท.value
    if role_type in {"verified", "developer", "ticket_staff"}:
        settings[f"{role_type}_role_id"] = โรล.id
    else:
        settings["role_ids"][role_type] = โรล.id
    save_guild_settings(interaction.guild_id, settings)
    await interaction.response.send_message(
        f"✅ Set role **{โรล.name}** for **{ประเภท.name}** successfully.",
        ephemeral=True,
    )


@bot.tree.command(name="ใส่คำนำหน้า", description="Add or edit rank prefixes")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    ยศ="Rank code e.g. OF-3",
    คำนำหน้า="Title e.g. MAJ",
)
async def set_prefix(interaction: discord.Interaction, ยศ: str, คำนำหน้า: str):
    if not interaction.guild_id:
        await interaction.response.send_message("❌ This command must be used inside a server.", ephemeral=True)
        return

    rank_code = ยศ.strip()
    title = คำนำหน้า.strip()
    if not rank_code or not title:
        await interaction.response.send_message("❌ Please provide both rank and title.", ephemeral=True)
        return

    settings = get_guild_settings(interaction.guild_id)
    settings["rank_prefixes"][rank_code.lower()] = f"{rank_code}, {title}"
    save_guild_settings(interaction.guild_id, settings)
    await interaction.response.send_message(
        f"✅ Added prefix **{rank_code}, {title}** successfully.",
        ephemeral=True,
    )


@bot.tree.command(name="ปรับแต่งทั้งหมด", description="Open modal to customize settings")
@app_commands.default_permissions(administrator=True)
async def customize_all(interaction: discord.Interaction):
    await interaction.response.send_modal(CustomizeAllModal())


@bot.tree.command(name="ดูการตั้งค่า", description="Show current server settings")
@app_commands.default_permissions(administrator=True)
async def show_settings(interaction: discord.Interaction):
    if not interaction.guild_id:
        await interaction.response.send_message("❌ This command must be used inside a server.", ephemeral=True)
        return

    settings = get_guild_settings(interaction.guild_id)
    role_ids = settings.get("role_ids", {})
    embed = discord.Embed(title="Current Server Settings", color=0x3498DB)
    embed.add_field(name="Roblox Group ID", value=str(settings.get("roblox_group_id")), inline=False)
    embed.add_field(name="Verified Role ID", value=str(settings.get("verified_role_id")), inline=False)
    embed.add_field(name="Ticket Staff Role ID", value=str(settings.get("ticket_staff_role_id")), inline=False)
    embed.add_field(
        name="Role IDs",
        value=(
            f"OR: `{role_ids.get('or')}`\n"
            f"OF Low: `{role_ids.get('of_low')}`\n"
            f"OF High: `{role_ids.get('of_high')}`\n"
            f"Guest: `{role_ids.get('guest')}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Rank Prefixes",
        value="\n".join(
            f"`{key}` → {value}" for key, value in settings.get("rank_prefixes", {}).items()
        )[:1024]
        or "None",
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# =========================
# TICKET SPECIFIC COMMANDS (ALL ENGLISH)
# =========================
@bot.tree.command(name="setup_ticket", description="Setup ticket panel (Admin Only)")
@app_commands.default_permissions(administrator=True)
async def setup_ticket(interaction: discord.Interaction):
    embed = discord.Embed(
        title="❗️ Contact Staff / Support",
        description="Please select a topic below to open a support ticket.",
        color=0xE74C3C
    )
    embed.set_footer(text="Eighty Six Games Support System")
    await interaction.channel.send(embed=embed, view=TicketSetupView())
    await interaction.response.send_message("✅ Ticket panel created successfully.", ephemeral=True)


@bot.tree.command(name="set_transcript_channel", description="Set the channel where closed ticket transcripts will be sent")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(channel="Select text channel for transcripts")
async def set_transcript_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.guild_id:
        await interaction.response.send_message("❌ This command must be used inside a server.", ephemeral=True)
        return
    settings = get_guild_settings(interaction.guild_id)
    settings["transcript_channel_id"] = channel.id
    save_guild_settings(interaction.guild_id, settings)
    await interaction.response.send_message(f"✅ Transcript channel set to {channel.mention}", ephemeral=True)


@bot.tree.command(name="set_ticket_category", description="Set the category where new tickets are created")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(category="Select category for tickets")
async def set_ticket_category(interaction: discord.Interaction, category: discord.CategoryChannel):
    if not interaction.guild_id:
        await interaction.response.send_message("❌ This command must be used inside a server.", ephemeral=True)
        return
    settings = get_guild_settings(interaction.guild_id)
    settings["ticket_category_id"] = category.id
    save_guild_settings(interaction.guild_id, settings)
    await interaction.response.send_message(f"✅ Ticket category set to **{category.name}**", ephemeral=True)


@bot.tree.command(name="anything_else", description="Ask if user needs further assistance before closing")
async def anything_else(interaction: discord.Interaction):
    if not interaction.guild_id:
        await interaction.response.send_message("❌ This command must be used inside a server.", ephemeral=True)
        return
    
    # Check if current channel is an active ticket
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT * FROM active_tickets WHERE channel_id = ?", (str(interaction.channel_id),)).fetchone()
    
    if not row:
        await interaction.response.send_message("❌ This command can only be used inside a support ticket channel.", ephemeral=True)
        return

    text_msg = "Do you have any further questions? If not, the staff will proceed to close this ticket."
    await interaction.response.send_message(content=text_msg)


@bot.tree.command(name="close_ticket", description="Close the current ticket and send transcript (Admin Only)")
@app_commands.default_permissions(administrator=True)
async def close_ticket(interaction: discord.Interaction):
    if not interaction.guild_id:
        await interaction.response.send_message("❌ This command must be used inside a server.", ephemeral=True)
        return

    channel = interaction.channel
    settings = get_guild_settings(interaction.guild_id)

    # Gather messages for transcript
    messages_transcript = []
    async for message in channel.history(limit=500, oldest_first=True):
        timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
        author = f"{message.author.name}#{message.author.discriminator}" if message.author.discriminator != "0" else message.author.name
        content = message.content or "[Embed or Attachment]"
        messages_transcript.append(f"[{timestamp}] {author}: {content}")

    transcript_text = f"=== TICKET TRANSCRIPT: {channel.name} ===\n" + "\n".join(messages_transcript)
    transcript_file = discord.File(
        io.BytesIO(transcript_text.encode("utf-8")),
        filename=f"transcript-{channel.name}.txt"
    )

    # Send transcript to designated channel
    transcript_channel_id = settings.get("transcript_channel_id")
    if transcript_channel_id:
        trans_channel = interaction.guild.get_channel(parse_id(transcript_channel_id))
        if trans_channel:
            await trans_channel.send(
                content=f"📁 **Transcript for closed ticket:** `{channel.name}`",
                file=transcript_file
            )

    await interaction.response.send_message("🔒 Closing ticket and deleting channel in 3 seconds...", ephemeral=True)
    
    # Remove from active tickets db
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM active_tickets WHERE channel_id = ?", (str(channel.id),))

    await asyncio.sleep(3)
    try:
        await channel.delete(reason=f"Closed by {interaction.user}")
    except discord.HTTPException:
        pass


# =========================
# FASTAPI WEBHOOK (ROBLOX VERIFY)
# =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    asyncio.create_task(bot.start(DISCORD_TOKEN))
    yield
    await bot.close()


app = FastAPI(lifespan=lifespan)


@app.post("/verify")
async def verify_endpoint(request: Request):
    data = await request.json()
    roblox_id = data.get("robloxId")
    roblox_username = str(data.get("robloxUsername", "")).strip()
    guild_id = data.get("guildId")
    search_name = roblox_username.lower()

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT discord_id FROM users
            WHERE LOWER(TRIM(pending_roblox_username)) = ?
            ORDER BY rowid DESC LIMIT 1
            """,
            (search_name,),
        ).fetchone()

    if not row:
        return {
            "ok": False,
            "message": f"Username '{roblox_username}' not found in pending list (Please click verify button in Discord first).",
        }

    rank, display_name, rank_name, err_msg = await update_member_status(
        row["discord_id"], roblox_id, roblox_username, guild_id
    )
    if rank is not None:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                UPDATE users
                SET roblox_id = ?, roblox_username = ?, verified = 1,
                    pending_roblox_username = NULL
                WHERE discord_id = ?
                """,
                (str(roblox_id), roblox_username, row["discord_id"]),
            )
        return {
            "ok": True,
            "discord_username": display_name,
            "current_rank": rank_name,
        }

    return {"ok": False, "message": err_msg or "Bot lacks permissions or server not found."}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
