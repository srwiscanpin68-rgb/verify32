import os
import asyncio
import json
import re
import sqlite3
import io
import requests
import discord
import uvicorn
from datetime import datetime, timedelta
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
    "roblox_group_id": 726824718,
    "roblox_group_url": "https://www.roblox.com/groups/726824718",
    "roblox_map_url": "https://www.roblox.com/th/games/74415906392980/unnamed",
    "verified_role_id": 1508479215908028543,
    "developer_role_id": 1508479215995977759,
    "ticket_staff_role_id": 1508479215908028544,
    "transcript_channel_id": 1537110830871613500,
    "allowed_ban_channel_id": 1538165546145677382,
    "ticket_category_id": None,
    "ticket_image_url": None,
    "verified_emoji": "✅",
    "admin_ids": "", 
    "role_ids": {
        "or": 1479699133001629797,
        "of_low": 1479699314078122094,
        "of_high": 1479699471603470432,
        "guest": None,
    },
    "rank_prefixes": {
        "1": "[P]", "2": "[C]", "3": "[B]", "4": "[A]", "5": "[S]",
        "10": "[TRN]", "20": "[DUC]", "30": "[UC]", "50": "[STAFF]",
        "100": "[DHAD]", "255": "[HAD]",
    },
}

DEVELOPER_IDS = [5711452462, 11388802001, 909811599]

# =========================
# DATABASE LOGIC
# =========================
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (discord_id TEXT PRIMARY KEY, roblox_id TEXT, roblox_username TEXT, verified INTEGER DEFAULT 0, pending_roblox_username TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS guild_settings (guild_id TEXT PRIMARY KEY, settings_json TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS active_tickets (channel_id TEXT PRIMARY KEY, guild_id TEXT, user_id TEXT, ticket_type TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS bans (roblox_id TEXT PRIMARY KEY, roblox_username TEXT, link TEXT, reason TEXT, status TEXT, image_url TEXT, expires_at TIMESTAMP)")

def get_guild_settings(guild_id):
    if not guild_id: return json.loads(json.dumps(DEFAULT_SETTINGS))
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT settings_json FROM guild_settings WHERE guild_id = ?", (str(guild_id),)).fetchone()
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    if row:
        try:
            saved = json.loads(row[0])
            settings.update({k: v for k, v in saved.items() if k not in {"role_ids", "rank_prefixes"}})
            if "role_ids" in saved: settings["role_ids"].update(saved["role_ids"])
            if "rank_prefixes" in saved: settings["rank_prefixes"].update(saved["rank_prefixes"])
        except: pass
    return settings

def save_guild_settings(guild_id, settings):
    if not guild_id: return
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO guild_settings (guild_id, settings_json) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET settings_json = excluded.settings_json", (str(guild_id), json.dumps(settings, ensure_ascii=False)))

def parse_id(value):
    if value is None: return None
    match = re.search(r"\d+", str(value))
    return int(match.group()) if match else None

def get_safe_emoji(emoji_str):
    if not emoji_str: return "✅"
    if isinstance(emoji_str, str) and emoji_str.startswith("<") and emoji_str.endswith(">"):
        try: return discord.PartialEmoji.from_str(emoji_str)
        except: return "✅"
    return emoji_str

# =========================
# PERMISSION CHECK (Robust Version)
# =========================
def is_admin(interaction: discord.Interaction):
    # 1. Owner or Administrator permission
    if interaction.user.id == interaction.guild.owner_id: return True
    if interaction.user.guild_permissions.administrator: return True
    
    # 2. Check Role Names (Admin, Owner, Staff, Moderator)
    admin_keywords = {"admin", "owner", "staff", "moderator", "dev"}
    for role in interaction.user.roles:
        if any(kw in role.name.lower() for kw in admin_keywords): return True
        
    # 3. Check Whitelist in Settings
    settings = get_guild_settings(interaction.guild_id)
    
    # Check configured Staff Role ID
    staff_role_id = parse_id(settings.get("ticket_staff_role_id"))
    if staff_role_id and any(r.id == staff_role_id for r in interaction.user.roles): return True
    
    # Check manual Admin IDs
    admin_str = str(settings.get("admin_ids", ""))
    admin_list = [x.strip() for x in admin_str.split(";") if x.strip()]
    uid = str(interaction.user.id)
    user_role_ids = [str(r.id) for r in interaction.user.roles]
    
    if uid in admin_list or any(rid in admin_list for rid in user_role_ids): return True
    
    return False

# =========================
# BOT CLASS
# =========================
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.add_view(MainVerifyView())
        self.add_view(TicketSetupView())
        self.add_view(CustomizeSelectorView())
        await self.tree.sync()
        print(f"Bot synced as {self.user}")

bot = MyBot()

# =========================
# UTILS
# =========================
def get_roblox_id_by_name(username):
    try:
        resp = requests.post("https://users.roblox.com/v1/usernames/users", json={"usernames": [username], "excludeBannedUsers": True}, timeout=10)
        data = resp.json()
        if data.get("data"): return str(data["data"][0]["id"])
    except: pass
    return None

def check_group_membership(roblox_id, group_id):
    try:
        resp = requests.get(f"https://groups.roblox.com/v1/users/{roblox_id}/groups/roles", timeout=10)
        for group in resp.json().get("data", []):
            if group["group"]["id"] == int(group_id):
                return True, group["role"]["rank"], group["role"]["name"]
    except: pass
    return False, 0, None

async def update_member_status(discord_id, roblox_id, roblox_username, guild_id=None):
    guild = bot.get_guild(int(guild_id)) if guild_id else (bot.guilds[0] if bot.guilds else None)
    if not guild: return None, "Server not found"
    settings = get_guild_settings(guild.id)
    try:
        member = await guild.fetch_member(int(discord_id))
        is_in_group, rank_val, rank_name = check_group_membership(roblox_id, settings["roblox_group_id"])
        is_dev = int(roblox_id) in DEVELOPER_IDS
        
        managed_role_ids = {parse_id(settings.get("verified_role_id")), parse_id(settings.get("developer_role_id")), *{parse_id(v) for v in settings.get("role_ids", {}).values()}}
        managed_role_ids.discard(None)
        
        roles = [r for r in member.roles if r != guild.default_role and r.id not in managed_role_ids]
        v_role = guild.get_role(parse_id(settings.get("verified_role_id")))
        if v_role: roles.append(v_role)
        
        prefix = "[Guest]"
        if is_dev:
            d_role = guild.get_role(parse_id(settings.get("developer_role_id")))
            if d_role: roles.append(d_role)
            prefix = "[DEV]"
        elif is_in_group:
            r_id = settings["role_ids"].get("or" if 1<=rank_val<=7 else "of_low" if 8<=rank_val<=11 else "of_high" if 12<=rank_val<=18 else None)
            r_role = guild.get_role(parse_id(r_id))
            if r_role: roles.append(r_role)
            prefix = settings["rank_prefixes"].get(str(rank_val), f"[{rank_name}]")
        else:
            g_role = guild.get_role(parse_id(settings["role_ids"].get("guest")))
            if g_role: roles.append(g_role)

        roles = list({r.id: r for r in roles}.values())
        new_nick = f"{prefix} | {roblox_username}"
        if len(new_nick) > 32: new_nick = new_nick[:32]
        
        try: await member.edit(roles=roles, nick=new_nick)
        except: await member.edit(roles=roles)
        return member.display_name, prefix, None
    except Exception as e: return None, None, str(e)

# =========================
# UI COMPONENTS
# =========================
class VerifyModal(discord.ui.Modal, title="Roblox Verification"):
    username = discord.ui.TextInput(label="Roblox Username", placeholder="Enter username...", min_length=3, max_length=20, required=True)
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        rid = get_roblox_id_by_name(self.username.value)
        if not rid:
            await interaction.followup.send(f"❌ Roblox username **{self.username.value}** not found.", ephemeral=True)
            return
        settings = get_guild_settings(interaction.guild_id)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO users (discord_id, pending_roblox_username, verified) VALUES (?, ?, 0)", (str(interaction.user.id), self.username.value.strip().lower()))
        embed = discord.Embed(title="Join game to verify", description=f"Username: **{self.username.value}**\n[Click to join game]({settings['roblox_map_url']})", color=0x00FF00)
        await interaction.followup.send(embed=embed, ephemeral=True)

class ChangeAccountButton(discord.ui.Button):
    def __init__(self): super().__init__(label="Change Account", style=discord.ButtonStyle.primary, custom_id="change_acc_btn")
    async def callback(self, interaction: discord.Interaction): await interaction.response.send_modal(VerifyModal())

class MainVerifyView(discord.ui.View):
    def __init__(self, emoji_str="✅"):
        super().__init__(timeout=None)
        try: self.start_v_btn.emoji = get_safe_emoji(emoji_str)
        except: pass

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, emoji="✅", custom_id="persistent_verify_main")
    async def start_v_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            u = conn.execute("SELECT * FROM users WHERE discord_id = ?", (str(interaction.user.id),)).fetchone()
        if u and u["verified"] and u["roblox_id"]:
            settings = get_guild_settings(interaction.guild_id)
            v_emoji = get_safe_emoji(settings.get("verified_emoji", "✅"))
            embed = discord.Embed(title="Verification Status", color=0x3498DB)
            embed.add_field(name="Username", value=f"**{u['roblox_username']}**", inline=False)
            embed.add_field(name="ID Roblox", value=f"**{u['roblox_id']}**", inline=False)
            embed.add_field(name="Status", value=f"{v_emoji} Verified", inline=False)
            view = discord.ui.View(); view.add_item(ChangeAccountButton())
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else: await interaction.response.send_modal(VerifyModal())

class TicketSelect(discord.ui.Select):
    def __init__(self):
        opts = [
            discord.SelectOption(label="Report Cheater", value="report_cheater", emoji="❗"),
            discord.SelectOption(label="Claim Reward", value="claim_reward", emoji="⭐"),
            discord.SelectOption(label="General Contact", value="general_contact", emoji="💬"),
            discord.SelectOption(label="Receive an award", value="receive_award", emoji="🎁"),
        ]
        super().__init__(placeholder="Select a topic to contact", min_values=1, max_values=1, options=opts, custom_id="ticket_select_menu")
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild, settings = interaction.guild, get_guild_settings(interaction.guild_id)
        staff_role = guild.get_role(parse_id(settings.get("ticket_staff_role_id")))
        category = guild.get_channel(parse_id(settings.get("ticket_category_id")))
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False), interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True), guild.me: discord.PermissionOverwrite(view_channel=True, manage_channels=True)}
        if staff_role: overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        try:
            ch = await guild.create_text_channel(name=f"ticket-{interaction.user.name}-{self.values[0]}"[:30], category=category, overwrites=overwrites)
            with sqlite3.connect(DB_PATH) as conn: conn.execute("INSERT INTO active_tickets VALUES (?, ?, ?, ?)", (str(ch.id), str(guild.id), str(interaction.user.id), self.values[0]))
            embed = discord.Embed(title=f"Ticket: {self.values[0].replace('_',' ').title()}", description=f"Hello {interaction.user.mention}, staff will assist you shortly.", color=0x3498DB)
            await ch.send(content=f"{staff_role.mention if staff_role else ''} {interaction.user.mention}", embed=embed)
            await interaction.followup.send(f"✅ Ticket created: {ch.mention}", ephemeral=True)
        except Exception as e: await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

class TicketSetupView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None); self.add_item(TicketSelect())

class GameBanModal(discord.ui.Modal, title="Game Ban System"):
    def __init__(self, unit: str):
        super().__init__()
        self.unit = unit
        self.duration_val = discord.ui.TextInput(label=f"Number of {unit} to ban", placeholder="Enter a number (e.g. 7)", required=True)
        self.add_item(self.duration_val)

    username = discord.ui.TextInput(label="Username Roblox", placeholder="Enter Roblox username...", required=True)
    link = discord.ui.TextInput(label="Link Roblox", placeholder="https://www.roblox.com/users/...", required=True)
    reason = discord.ui.TextInput(label="Reason", style=discord.TextStyle.paragraph, placeholder="Reason for ban...", required=True)
    image_url = discord.ui.TextInput(label="ใส่ลิ้งรูป", placeholder="https://...", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        rname, rid = self.username.value.strip(), get_roblox_id_by_name(self.username.value.strip())
        if not rid:
            await interaction.followup.send(f"❌ Roblox user **{rname}** not found.", ephemeral=True)
            return
        try: val = int(self.duration_val.value.strip())
        except:
            await interaction.followup.send("❌ Please enter a valid number.", ephemeral=True)
            return

        now = datetime.now()
        delta = {"Minutes": timedelta(minutes=val), "Hours": timedelta(hours=val), "Days": timedelta(days=val), "Months": timedelta(days=val*30), "Years": timedelta(days=val*365)}.get(self.unit)
        expires_at = (now + delta).timestamp() if delta else None
        expires_str = (now + delta).strftime('%Y-%m-%d %H:%M') if delta else "Permanent"

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO bans VALUES (?, ?, ?, ?, ?, ?, ?)", (str(rid), rname, self.link.value, self.reason.value, f"Banned ({expires_str})", self.image_url.value, expires_at))
        
        embed = discord.Embed(title="🚨 Player Banned", color=0xFF0000)
        embed.add_field(name="Username Roblox", value=rname, inline=True)
        embed.add_field(name="Reason", value=self.reason.value, inline=True)
        embed.add_field(name="Status", value=f"Banned until {expires_str}", inline=False)
        if self.image_url.value: embed.set_image(url=self.image_url.value)
        await interaction.channel.send(content="||@everyone||", embed=embed)
        await interaction.followup.send("✅ Banned successfully.", ephemeral=True)

# =========================
# CUSTOMIZATION SYSTEM
# =========================
class CustomizeSelectorView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.select(placeholder="เลือกสิ่งที่ต้องการปรับแต่ง", options=[
        discord.SelectOption(label="ข้อมูลพื้นฐาน", description="Group ID, Group URL, Map URL, Staff Role, Admin IDs", value="basic"),
        discord.SelectOption(label="ตั้งค่าโรล (Roles)", description="Verified Role, Developer Role, OR, OF Low, OF High", value="roles"),
        discord.SelectOption(label="คำนำหน้า (Prefixes)", description="ตั้งค่าชื่อย่อยศ Rank ID", value="prefixes")
    ], custom_id="cust_selector")
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        if not is_admin(interaction): return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        s = get_guild_settings(interaction.guild_id)
        if select.values[0] == "basic":
            class BasicModal(discord.ui.Modal, title="ปรับแต่งข้อมูลพื้นฐาน"):
                gid = discord.ui.TextInput(label="Roblox Group ID", default=str(s['roblox_group_id']))
                gurl = discord.ui.TextInput(label="Roblox Group URL", default=s['roblox_group_url'])
                murl = discord.ui.TextInput(label="Roblox Map URL", default=s['roblox_map_url'])
                sid = discord.ui.TextInput(label="Staff Role ID", default=str(s['ticket_staff_role_id']))
                admins = discord.ui.TextInput(label="Admin IDs (Separate with ;)", default=s['admin_ids'], required=False)
                async def on_submit(self, interaction: discord.Interaction):
                    s = get_guild_settings(interaction.guild_id)
                    s['roblox_group_id'] = parse_id(self.gid.value); s['roblox_group_url'] = self.gurl.value.strip()
                    s['roblox_map_url'] = self.murl.value.strip(); s['ticket_staff_role_id'] = parse_id(self.sid.value)
                    s['admin_ids'] = self.admins.value.strip(); save_guild_settings(interaction.guild_id, s)
                    await interaction.response.send_message("✅ ข้อมูลพื้นฐานอัปเดตแล้ว", ephemeral=True)
            await interaction.response.send_modal(BasicModal())
        elif select.values[0] == "roles":
            class RoleModal(discord.ui.Modal, title="ตั้งค่าโรล (Role IDs)"):
                v_role = discord.ui.TextInput(label="Verified Role ID", default=str(s['verified_role_id']))
                d_role = discord.ui.TextInput(label="Developer Role ID", default=str(s['developer_role_id']))
                or_role = discord.ui.TextInput(label="OR Role ID", default=str(s['role_ids']['or']))
                of_l = discord.ui.TextInput(label="OF Low Role ID", default=str(s['role_ids']['of_low']))
                of_h = discord.ui.TextInput(label="OF High Role ID", default=str(s['role_ids']['of_high']))
                async def on_submit(self, interaction: discord.Interaction):
                    s = get_guild_settings(interaction.guild_id)
                    s['verified_role_id'] = parse_id(self.v_role.value); s['developer_role_id'] = parse_id(self.d_role.value)
                    s['role_ids']['or'] = parse_id(self.or_role.value); s['role_ids']['of_low'] = parse_id(self.of_l.value)
                    s['role_ids']['of_high'] = parse_id(self.of_h.value); save_guild_settings(interaction.guild_id, s)
                    await interaction.response.send_message("✅ ตั้งค่าโรลอัปเดตแล้ว", ephemeral=True)
            await interaction.response.send_modal(RoleModal())
        elif select.values[0] == "prefixes":
            class PrefixModal(discord.ui.Modal, title="ตั้งค่าคำนำหน้า (Prefixes)"):
                pfx = discord.ui.TextInput(label="Prefixes (e.g. 1=[P];2=[C];)", style=discord.TextStyle.paragraph, default="; ".join([f"{k}={v}" for k,v in s['rank_prefixes'].items()]))
                async def on_submit(self, interaction: discord.Interaction):
                    s = get_guild_settings(interaction.guild_id)
                    for item in self.pfx.value.split(";"):
                        if "=" in item: k,v = item.split("=", 1); s["rank_prefixes"][k.strip()] = v.strip()
                    save_guild_settings(interaction.guild_id, s); await interaction.response.send_message("✅ คำนำหน้าอัปเดตแล้ว", ephemeral=True)
            await interaction.response.send_modal(PrefixModal())

# =========================
# SLASH COMMANDS
# =========================
@bot.tree.command(name="ยืนยันตัวตน", description="สร้างแผงยืนยันตัวตน")
async def setup_v(interaction: discord.Interaction):
    if not is_admin(interaction): return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    settings = get_guild_settings(interaction.guild_id)
    embed = discord.Embed(title="🛡️ Roblox Verification", description="Click the button below to verify your account.", color=0x3498DB)
    await interaction.channel.send(embed=embed, view=MainVerifyView(settings.get("verified_emoji", "✅")))
    await interaction.response.send_message("✅ Panel created.", ephemeral=True)

@bot.tree.command(name="ล้างข้อมูล", description="ล้างข้อมูลผู้ใช้")
async def clear_u(interaction: discord.Interaction, user: discord.Member):
    if not is_admin(interaction): return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    with sqlite3.connect(DB_PATH) as conn: conn.execute("DELETE FROM users WHERE discord_id = ?", (str(user.id),))
    await interaction.response.send_message(f"✅ Cleared data for {user.mention}", ephemeral=True)

@bot.tree.command(name="ล้างข้อมูลทั้งหมด", description="ล้างข้อมูลผู้ใช้ทั้งหมด")
async def clear_all(interaction: discord.Interaction):
    if not is_admin(interaction): return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    with sqlite3.connect(DB_PATH) as conn: conn.execute("DELETE FROM users")
    await interaction.response.send_message("✅ All user data cleared.", ephemeral=True)

@bot.tree.command(name="ใส่โรล", description="ตั้งค่าโรลต่างๆ")
async def set_role(interaction: discord.Interaction, type: str, role: discord.Role):
    if not is_admin(interaction): return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    s = get_guild_settings(interaction.guild_id)
    if type in ["verified", "developer", "staff"]: s[f"{type}_role_id"] = role.id
    elif type in ["or", "of_low", "of_high", "guest"]: s["role_ids"][type] = role.id
    save_guild_settings(interaction.guild_id, s)
    await interaction.response.send_message(f"✅ Set {type} role to {role.mention}", ephemeral=True)

@set_role.autocomplete("type")
async def role_type_auto(interaction: discord.Interaction, current: str):
    types = ["verified", "developer", "staff", "or", "of_low", "of_high", "guest"]
    return [app_commands.Choice(name=t, value=t) for t in types if current.lower() in t.lower()]

@bot.tree.command(name="ใส่คำนำหน้า", description="ตั้งค่าคำนำหน้ายศ")
async def set_prefix(interaction: discord.Interaction, rank_id: str, prefix: str):
    if not is_admin(interaction): return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    s = get_guild_settings(interaction.guild_id); s["rank_prefixes"][rank_id] = prefix; save_guild_settings(interaction.guild_id, s)
    await interaction.response.send_message(f"✅ Set rank {rank_id} prefix to {prefix}", ephemeral=True)

@bot.tree.command(name="ดูการตั้งค่า", description="ดูการตั้งค่าปัจจุบัน")
async def view_settings(interaction: discord.Interaction):
    if not is_admin(interaction): return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    s = get_guild_settings(interaction.guild_id)
    text = f"**Group ID:** {s['roblox_group_id']}\n**Staff Role:** <@&{s['ticket_staff_role_id']}>\n**Admin IDs:** {s['admin_ids']}\n**Ban Channel:** <#{s['allowed_ban_channel_id']}>"
    await interaction.response.send_message(text, ephemeral=True)

@bot.tree.command(name="ตั้งค่าticket", description="สร้างแผง Ticket")
async def setup_t(interaction: discord.Interaction):
    if not is_admin(interaction): return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    embed = discord.Embed(title="❗ Contact Staff / Support", description="Select a topic to open a ticket.", color=0xE74C3C)
    await interaction.channel.send(embed=embed, view=TicketSetupView())
    await interaction.response.send_message("✅ Ticket panel created.", ephemeral=True)

@bot.tree.command(name="game-ban", description="แบนผู้เล่นออกจากเกม")
async def game_ban(interaction: discord.Interaction, unit: str):
    if not is_admin(interaction): return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    s = get_guild_settings(interaction.guild_id)
    if interaction.channel_id != parse_id(s.get("allowed_ban_channel_id", 0)):
        return await interaction.response.send_message(f"❌ Use in <#{s.get('allowed_ban_channel_id')}>", ephemeral=True)
    await interaction.response.send_modal(GameBanModal(unit))

@game_ban.autocomplete("unit")
async def ban_unit_auto(interaction: discord.Interaction, current: str):
    units = ["Minutes", "Hours", "Days", "Months", "Years", "Permanent"]
    return [app_commands.Choice(name=u, value=u) for u in units if current.lower() in u.lower()]

@bot.tree.command(name="unban", description="ปลดแบนผู้เล่น")
async def unban_cmd(interaction: discord.Interaction, username: str):
    if not is_admin(interaction): return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    rid = get_roblox_id_by_name(username)
    if not rid: return await interaction.followup.send(f"❌ User {username} not found.", ephemeral=True)
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.execute("DELETE FROM bans WHERE roblox_id = ?", (str(rid),))
        if c.rowcount > 0: await interaction.followup.send(f"✅ Unbanned {username}.", ephemeral=True)
        else: await interaction.followup.send(f"ℹ️ {username} not banned.", ephemeral=True)

@bot.tree.command(name="ปิดticket", description="ปิด Ticket")
async def close_t(interaction: discord.Interaction):
    if not is_admin(interaction): return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    settings = get_guild_settings(interaction.guild_id)
    ch = interaction.channel
    html = f"<html><body style='background:#36393f;color:#fff;font-family:sans-serif;'><h1>Transcript: {ch.name}</h1>"
    async for m in ch.history(limit=1000, oldest_first=True):
        html += f"<p><b>{m.author.name}</b> [{m.created_at}]: {m.clean_content}</p>"
    html += "</body></html>"
    file = discord.File(io.BytesIO(html.encode()), filename=f"transcript-{ch.name}.html")
    t_ch = interaction.guild.get_channel(parse_id(settings.get("transcript_channel_id")))
    if t_ch: await t_ch.send(content=f"📁 Transcript: `{ch.name}`", file=file)
    await interaction.followup.send("🔒 Closing...", ephemeral=True)
    await asyncio.sleep(3); await ch.delete()

@bot.tree.command(name="มีอะไรสอบถามเพิ่มเติมไหม_en", description="ถามเพิ่มเติม (EN)")
async def ask_en(interaction: discord.Interaction):
    if not is_admin(interaction): return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    settings = get_guild_settings(interaction.guild_id)
    embed = discord.Embed(description="Do you have any further questions? If not, the staff will proceed to close this ticket.", color=0x3498DB)
    if settings.get("ticket_image_url"): embed.set_image(url=settings["ticket_image_url"])
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Sent.", ephemeral=True)

@bot.tree.command(name="มีอะไรสอบถามเพิ่มเติมไหม_th", description="ถามเพิ่มเติม (TH)")
async def ask_th(interaction: discord.Interaction):
    if not is_admin(interaction): return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    settings = get_guild_settings(interaction.guild_id)
    embed = discord.Embed(description="มีอะไรสอบถามเพิ่มเติมไหมครับ/ค่ะ หากไม่มีแล้วทีมงานขอปิด Ticket นะครับ/ค่ะ", color=0x3498DB)
    if settings.get("ticket_image_url"): embed.set_image(url=settings["ticket_image_url"])
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Sent.", ephemeral=True)

@bot.tree.command(name="update", description="ประกาศอัปเดต")
async def update_cmd(interaction: discord.Interaction):
    if not is_admin(interaction): return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    class UpModal(discord.ui.Modal, title="Update"):
        msg = discord.ui.TextInput(label="Message", style=discord.TextStyle.paragraph)
        img = discord.ui.TextInput(label="Image URL", required=False)
        note = discord.ui.TextInput(label="Note", required=False)
        async def on_submit(self, interaction: discord.Interaction):
            emb = discord.Embed(description=self.msg.value, color=0x3498DB)
            if self.img.value: emb.set_image(url=self.img.value)
            if self.note.value: emb.add_field(name="\u200b", value=f"-# {self.note.value}")
            await interaction.channel.send(content="||@everyone||", embed=emb)
            await interaction.response.send_message("✅ Sent.", ephemeral=True)
    await interaction.response.send_modal(UpModal())

@bot.tree.command(name="ตั้งค่าห้องtranscript", description="ตั้งค่าห้อง Transcript")
async def set_trans(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_admin(interaction): return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    s = get_guild_settings(interaction.guild_id); s["transcript_channel_id"] = channel.id; save_guild_settings(interaction.guild_id, s)
    await interaction.response.send_message(f"✅ Transcript channel set to {channel.mention}", ephemeral=True)

@bot.tree.command(name="ตั้งค่าหมวดหมู่ticket", description="ตั้งค่าหมวดหมู่ Ticket")
async def set_cat(interaction: discord.Interaction, category: discord.CategoryChannel):
    if not is_admin(interaction): return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    s = get_guild_settings(interaction.guild_id); s["ticket_category_id"] = category.id; save_guild_settings(interaction.guild_id, s)
    await interaction.response.send_message(f"✅ Ticket category set to **{category.name}**", ephemeral=True)

@bot.tree.command(name="ปรับแต่งทั้งหมด", description="แผงควบคุมการตั้งค่าทั้งหมด")
async def cust_all_panel(interaction: discord.Interaction):
    if not is_admin(interaction): return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    await interaction.response.send_message("⚙️ **Settings Control Panel**\nโปรดเลือกหมวดหมู่ที่ต้องการปรับแต่งจากเมนูด้านล่าง:", view=CustomizeSelectorView(), ephemeral=True)

# =========================
# WEBHOOK & API
# =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(); asyncio.create_task(bot.start(DISCORD_TOKEN))
    yield
    await bot.close()

app = FastAPI(lifespan=lifespan)
@app.get("/")
async def root(): return {"status": "online"}

@app.get("/check-ban/{roblox_id}")
async def check_ban_ep(roblox_id: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM bans WHERE roblox_id = ?", (str(roblox_id),)).fetchone()
    if row:
        if row["expires_at"] and datetime.now().timestamp() > row["expires_at"]:
            with sqlite3.connect(DB_PATH) as conn: conn.execute("DELETE FROM bans WHERE roblox_id = ?", (str(roblox_id),))
            return {"banned": False}
        return {"banned": True, "reason": row["reason"], "status": row["status"]}
    return {"banned": False}

@app.post("/verify")
async def verify_ep(request: Request):
    data = await request.json()
    rid, rname, gid = data.get("robloxId"), str(data.get("robloxUsername", "")).strip(), data.get("guildId")
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT discord_id FROM users WHERE LOWER(TRIM(pending_roblox_username)) = ? ORDER BY rowid DESC LIMIT 1", (rname.lower(),)).fetchone()
    if not row: return {"ok": False, "message": "Verify on Discord first!"}
    dname, rank, err = await update_member_status(row[0], rid, rname, gid)
    if dname:
        with sqlite3.connect(DB_PATH) as conn: conn.execute("UPDATE users SET roblox_id=?, roblox_username=?, verified=1, pending_roblox_username=NULL WHERE discord_id=?", (str(rid), rname, row[0]))
        return {"ok": True, "discord_username": dname}
    return {"ok": False, "message": err}

if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port=PORT)
