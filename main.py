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
ALLOWED_BAN_CHANNEL_ID = 1538165546145677382

DEFAULT_SETTINGS = {
    "roblox_group_id": 726824718,
    "roblox_group_url": "https://www.roblox.com/groups/726824718",
    "roblox_map_url": "https://www.roblox.com/th/games/74415906392980/unnamed",
    "verified_role_id": 1508479215908028543,
    "developer_role_id": 1508479215995977759,
    "ticket_staff_role_id": 1508479215908028544,
    "transcript_channel_id": 1537110830871613500,
    "ticket_category_id": None,
    "ticket_image_url": None,
    "emojis": {
        "verify_btn": "✅",
        "verify_success": "✅",
        "verify_failed": "❌",
        "ticket_header": "❗",
        "ticket_report": "❗",
        "ticket_reward": "⭐",
        "ticket_contact": "💬",
        "ticket_award": "🎁",
        "ban_status": "🔴",
        "ban_expiry": "📅",
        "info": "ℹ️",
        "folder": "📁",
        "lock": "🔒",
        "success": "✅",
        "error": "❌",
        "loading": "⌛"
    },
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
        try: conn.execute("ALTER TABLE bans ADD COLUMN expires_at TIMESTAMP")
        except: pass

def get_guild_settings(guild_id):
    if not guild_id: return json.loads(json.dumps(DEFAULT_SETTINGS))
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT settings_json FROM guild_settings WHERE guild_id = ?", (str(guild_id),)).fetchone()
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    if row:
        try:
            saved = json.loads(row[0])
            settings.update({k: v for k, v in saved.items() if k not in {"role_ids", "rank_prefixes", "emojis"}})
            if "role_ids" in saved: settings["role_ids"].update(saved["role_ids"])
            if "rank_prefixes" in saved: settings["rank_prefixes"].update(saved["rank_prefixes"])
            if "emojis" in saved: settings["emojis"].update(saved["emojis"])
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
        await self.tree.sync()
        print(f"Bot synced as {self.user}")

bot = MyBot()

# =========================
# UTILS
# =========================
def get_roblox_info_by_name(username):
    try:
        resp = requests.post("https://users.roblox.com/v1/usernames/users", json={"usernames": [username], "excludeBannedUsers": True}, timeout=10)
        data = resp.json()
        if data.get("data"): return str(data["data"][0]["id"]), data["data"][0]["name"]
    except: pass
    return None, None

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
        
        rname, prefix = "Guest", "[Guest]"
        if is_dev:
            d_role = guild.get_role(parse_id(settings.get("developer_role_id")))
            if d_role: roles.append(d_role)
            rname, prefix = "Developer", "[DEV]"
        elif is_in_group:
            r_id = settings["role_ids"].get("or" if 1<=rank_val<=7 else "of_low" if 8<=rank_val<=11 else "of_high" if 12<=rank_val<=18 else None)
            r_role = guild.get_role(parse_id(r_id))
            if r_role: roles.append(r_role)
            rname = rank_name
            prefix = settings["rank_prefixes"].get(str(rank_val), f"[{rank_name}]")
        else:
            g_role = guild.get_role(parse_id(settings["role_ids"].get("guest")))
            if g_role: roles.append(g_role)

        roles = list({r.id: r for r in roles}.values())
        new_nick = f"{prefix} | {roblox_username}"
        if len(new_nick) > 32: new_nick = new_nick[:32]
        try: await member.edit(roles=roles, nick=new_nick)
        except: await member.edit(roles=roles)
        return member.display_name, rname, None
    except discord.HTTPException as e:
        msg = "Missing Permissions" if e.code == 50013 else "Member not found" if e.code == 10007 else f"Discord Error {e.code}"
        return None, None, msg
    except Exception as e: return None, None, str(e)

# =========================
# UI COMPONENTS
# =========================
class VerifyModal(discord.ui.Modal, title="Roblox Verification"):
    username = discord.ui.TextInput(label="Roblox Username", placeholder="Enter username...", min_length=3, max_length=20, required=True)
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        rid, correct_name = get_roblox_info_by_name(self.username.value.strip())
        if not rid:
            await interaction.followup.send(f"❌ Roblox username **{self.username.value}** not found.", ephemeral=True)
            return
        settings = get_guild_settings(interaction.guild_id)
        is_in, _, _ = check_group_membership(rid, settings["roblox_group_id"])
        if not is_in and int(rid) not in DEVELOPER_IDS:
            await interaction.followup.send(f"❌ Join our group first: {settings['roblox_group_url']}", ephemeral=True)
            return
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO users (discord_id, roblox_id, roblox_username, pending_roblox_username, verified) VALUES (?, ?, ?, ?, 0)", 
                         (str(interaction.user.id), str(rid), correct_name, correct_name.lower()))
        embed = discord.Embed(title="Join game to verify", description=f"Username: **{correct_name}**\n[Click to join game]({settings['roblox_map_url']})", color=0x00FF00)
        await interaction.followup.send(embed=embed, ephemeral=True)

class ChangeAccountButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Change Account", style=discord.ButtonStyle.primary, custom_id="change_acc_btn")
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(VerifyModal())

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
            v_emoji = get_safe_emoji(settings["emojis"].get("verify_success", "✅"))
            embed = discord.Embed(title="Verification Status", color=0x3498DB)
            embed.add_field(name="Username", value=f"**{u['roblox_username']}**", inline=False)
            embed.add_field(name="ID Roblox", value=f"**{u['roblox_id']}**", inline=False)
            embed.add_field(name="Status", value=f"{v_emoji} Verified", inline=False)
            view = discord.ui.View(); view.add_item(ChangeAccountButton())
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_modal(VerifyModal())

class TicketSelect(discord.ui.Select):
    def __init__(self, emojis):
        opts = [
            discord.SelectOption(label="Report Cheater", value="report_cheater", emoji=get_safe_emoji(emojis.get("ticket_report", "❗"))),
            discord.SelectOption(label="Claim Reward", value="claim_reward", emoji=get_safe_emoji(emojis.get("ticket_reward", "⭐"))),
            discord.SelectOption(label="General Contact", value="general_contact", emoji=get_safe_emoji(emojis.get("ticket_contact", "💬"))),
            discord.SelectOption(label="Receive an award", value="receive_award", emoji=get_safe_emoji(emojis.get("ticket_award", "🎁"))),
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
    def __init__(self, emojis=None): 
        super().__init__(timeout=None)
        if not emojis: emojis = DEFAULT_SETTINGS["emojis"]
        self.add_item(TicketSelect(emojis))

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
        rname = self.username.value.strip()
        rid, _ = get_roblox_info_by_name(rname)
        if not rid: await interaction.followup.send(f"❌ Roblox user **{rname}** not found.", ephemeral=True); return
        try: val = int(self.duration_val.value.strip())
        except: await interaction.followup.send("❌ Invalid duration.", ephemeral=True); return
        now = datetime.now(); delta = None
        if self.unit == "Minutes": delta = timedelta(minutes=val)
        elif self.unit == "Hours": delta = timedelta(hours=val)
        elif self.unit == "Days": delta = timedelta(days=val)
        elif self.unit == "Months": delta = timedelta(days=val*30)
        elif self.unit == "Years": delta = timedelta(days=val*365)
        if delta: expires_at = now + delta; expires_str = expires_at.strftime("%Y-%m-%d %H:%M:%S"); expires_ts = expires_at.timestamp(); status_text = f"Banned for {val} {self.unit}"
        else: expires_str = "Never (Permanent)"; expires_ts = None; status_text = "Permanently Banned"
        with sqlite3.connect(DB_PATH) as conn: conn.execute("INSERT OR REPLACE INTO bans (roblox_id, roblox_username, link, reason, status, image_url, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (str(rid), rname, self.link.value.strip(), self.reason.value.strip(), status_text, self.image_url.value.strip() if self.image_url.value else None, expires_ts))
        
        s = get_guild_settings(interaction.guild_id)
        b_emoji = s["emojis"].get("ban_status", "🔴")
        e_emoji = s["emojis"].get("ban_expiry", "📅")
        
        embed = discord.Embed(title="🚨 Update : ระบบADMIN", color=0xE74C3C)
        embed.add_field(name="Username Roblox", value=f"**{rname}**", inline=False)
        embed.add_field(name="Link Roblox", value=f"[Click Profile]({self.link.value.strip()})", inline=False)
        embed.add_field(name="Reason", value=self.reason.value.strip(), inline=False)
        embed.add_field(name="Status", value=f"{b_emoji} {status_text}", inline=False)
        embed.add_field(name="Expires At", value=f"{e_emoji} {expires_str}", inline=False)
        if self.image_url.value: embed.set_image(url=self.image_url.value.strip())
        await interaction.channel.send(content="||@everyone||", embed=embed)
        await interaction.followup.send(f"✅ Banned {rname} until {expires_str}.", ephemeral=True)

# =========================
# SLASH COMMANDS
# =========================
@bot.tree.command(name="ยืนยันตัวตน", description="Setup Roblox verification panel")
@app_commands.default_permissions(administrator=True)
async def setup_v(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    settings = get_guild_settings(interaction.guild_id)
    v_emoji = settings["emojis"].get("verify_btn", "✅")
    embed = discord.Embed(title="Thai Military Verification", description="Click below to start.", color=0x2B2D31)
    await interaction.channel.send(embed=embed, view=MainVerifyView(v_emoji))
    await interaction.followup.send("✅ Panel created.", ephemeral=True)

@bot.tree.command(name="ปรับแต่งอีโมจิ", description="เปลี่ยนอีโมจิที่มีอยู่ในระบบ (Administrator Only)")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(รายการ="เลือกรายการที่ต้องการเปลี่ยนอีโมจิ", อีโมจิ="ใส่อีโมจิธรรมดา หรือ Custom Emoji")
@app_commands.choices(รายการ=[
    app_commands.Choice(name="ปุ่มยืนยันตัวตน", value="verify_btn"),
    app_commands.Choice(name="สถานะยืนยันแล้ว", value="verify_success"),
    app_commands.Choice(name="สถานะยืนยันไม่สำเร็จ", value="verify_failed"),
    app_commands.Choice(name="หัวข้อ Ticket (❗)", value="ticket_header"),
    app_commands.Choice(name="Ticket: รายงานคนโกง", value="ticket_report"),
    app_commands.Choice(name="Ticket: รับรางวัล", value="ticket_reward"),
    app_commands.Choice(name="Ticket: ติดต่อสอบถาม", value="ticket_contact"),
    app_commands.Choice(name="Ticket: รับรางวัลพิเศษ", value="ticket_award"),
    app_commands.Choice(name="Ban: สถานะการแบน", value="ban_status"),
    app_commands.Choice(name="Ban: วันหมดอายุ", value="ban_expiry"),
    app_commands.Choice(name="ข้อความแจ้งเตือน (Info)", value="info"),
    app_commands.Choice(name="ไอคอนโฟลเดอร์ (Transcript)", value="folder"),
    app_commands.Choice(name="ไอคอนแม่กุญแจ (Close)", value="lock"),
    app_commands.Choice(name="ไอคอนสำเร็จ (Success)", value="success"),
    app_commands.Choice(name="ไอคอนผิดพลาด (Error)", value="error"),
    app_commands.Choice(name="ไอคอนโหลด (Loading)", value="loading"),
])
async def set_emoji(interaction: discord.Interaction, รายการ: app_commands.Choice[str], อีโมจิ: str):
    s = get_guild_settings(interaction.guild_id)
    s["emojis"][รายการ.value] = อีโมจิ.strip()
    save_guild_settings(interaction.guild_id, s)
    safe_e = get_safe_emoji(อีโมจิ.strip())
    await interaction.response.send_message(f"✅ ตั้งค่าอีโมจิสำหรับ **{รายการ.name}** เป็น {safe_e} เรียบร้อยแล้ว!", ephemeral=True)

@bot.tree.command(name="ตั้งค่าticket", description="Setup ticket panel")
@app_commands.default_permissions(administrator=True)
async def setup_t(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    settings = get_guild_settings(interaction.guild_id)
    h_emoji = settings["emojis"].get("ticket_header", "❗")
    embed = discord.Embed(title=f"{h_emoji} Contact Staff / Support", description="Select a topic to open a ticket.", color=0xE74C3C)
    await interaction.channel.send(embed=embed, view=TicketSetupView(settings.get("emojis")))
    await interaction.followup.send("✅ Ticket panel created.", ephemeral=True)

@bot.tree.command(name="game-ban", description="Ban a player from the game")
@app_commands.default_permissions(administrator=True)
@app_commands.choices(unit=[app_commands.Choice(name=u, value=u) for u in ["Minutes", "Hours", "Days", "Months", "Years", "Permanent"]])
async def game_ban(interaction: discord.Interaction, unit: str):
    if interaction.channel_id != ALLOWED_BAN_CHANNEL_ID: await interaction.response.send_message(f"❌ Only in <#{ALLOWED_BAN_CHANNEL_ID}>", ephemeral=True); return
    await interaction.response.send_modal(GameBanModal(unit))

@bot.tree.command(name="unban", description="Unban a player")
@app_commands.default_permissions(administrator=True)
async def unban_cmd(interaction: discord.Interaction, username: str):
    await interaction.response.defer(ephemeral=True)
    rid, _ = get_roblox_info_by_name(username)
    if not rid: await interaction.followup.send(f"❌ Not found.", ephemeral=True); return
    
    s = get_guild_settings(interaction.guild_id)
    v_emoji = s["emojis"].get("verify_success", "✅")
    i_emoji = s["emojis"].get("info", "ℹ️")
    
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("DELETE FROM bans WHERE roblox_id = ?", (str(rid),))
        if cursor.rowcount > 0: await interaction.followup.send(f"{v_emoji} Successfully unbanned **{username}**.", ephemeral=True)
        else: await interaction.followup.send(f"{i_emoji} **{username}** is not in the ban list.", ephemeral=True)

@bot.tree.command(name="ปิดticket", description="Close ticket")
@app_commands.default_permissions(administrator=True)
async def close_t(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True); s = get_guild_settings(interaction.guild_id); ch = interaction.channel
    f_emoji = s["emojis"].get("folder", "📁")
    l_emoji = s["emojis"].get("lock", "🔒")
    
    html = f"<html><body><h1>Transcript: {ch.name}</h1>"
    async for m in ch.history(limit=1000, oldest_first=True): html += f"<p><b>{m.author.display_name}</b>: {m.clean_content}</p>"
    html += "</body></html>"
    file = discord.File(io.BytesIO(html.encode()), filename=f"transcript-{ch.name}.html")
    t_ch = interaction.guild.get_channel(parse_id(s.get("transcript_channel_id")))
    if t_ch: await t_ch.send(content=f"{f_emoji} Transcript: `{ch.name}`", file=file)
    await interaction.followup.send(f"{l_emoji} Closing in 3s...", ephemeral=True)
    with sqlite3.connect(DB_PATH) as conn: conn.execute("DELETE FROM active_tickets WHERE channel_id = ?", (str(ch.id),))
    await asyncio.sleep(3); await ch.delete()

@bot.tree.command(name="มีอะไรสอบถามเพิ่มเติมไหม_en", description="Ask for more questions (EN)")
async def ask_en(interaction: discord.Interaction):
    s = get_guild_settings(interaction.guild_id)
    i_emoji = s["emojis"].get("info", "ℹ️")
    embed = discord.Embed(title=f"{i_emoji} Further Assistance", description="Do you have any further questions? If not, the staff will proceed to close this ticket.", color=0x3498DB)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="มีอะไรสอบถามเพิ่มเติมไหม_th", description="ส่งข้อความสอบถามเพิ่มเติม (ภาษาไทย)")
async def ask_th(interaction: discord.Interaction):
    s = get_guild_settings(interaction.guild_id)
    i_emoji = s["emojis"].get("info", "ℹ️")
    embed = discord.Embed(title=f"{i_emoji} สอบถามเพิ่มเติม", description="มีอะไรสอบถามเพิ่มเติมไหมครับ/ค่ะ หากไม่มีแล้วทีมงานขอปิด Ticket นะครับ/ค่ะ", color=0x3498DB)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="update", description="Send announcement update")
@app_commands.default_permissions(administrator=True)
async def update_cmd(interaction: discord.Interaction, message: str):
    s = get_guild_settings(interaction.guild_id)
    u_emoji = s["emojis"].get("loading", "⌛")
    embed = discord.Embed(title=f"{u_emoji} Announcement Update", description=message, color=0xF1C40F, timestamp=datetime.now())
    await interaction.channel.send(content="@everyone", embed=embed)
    await interaction.response.send_message("✅ Announcement sent.", ephemeral=True)

@bot.tree.command(name="ตั้งค่าห้องtranscript", description="Set transcript channel")
@app_commands.default_permissions(administrator=True)
async def set_trans(interaction: discord.Interaction, channel: discord.TextChannel):
    s = get_guild_settings(interaction.guild_id)
    s["transcript_channel_id"] = channel.id
    save_guild_settings(interaction.guild_id, s)
    await interaction.response.send_message(f"✅ Transcript channel set to {channel.mention}", ephemeral=True)

@bot.tree.command(name="ตั้งค่าหมวดหมู่ticket", description="Set ticket category")
@app_commands.default_permissions(administrator=True)
async def set_cat(interaction: discord.Interaction, category: discord.CategoryChannel):
    s = get_guild_settings(interaction.guild_id)
    s["ticket_category_id"] = category.id
    save_guild_settings(interaction.guild_id, s)
    await interaction.response.send_message(f"✅ Ticket category set to **{category.name}**", ephemeral=True)

@bot.tree.command(name="อื่นๆ", description="ดูคำสั่งเพิ่มเติมอื่นๆ ของระบบ")
async def others_cmd(interaction: discord.Interaction):
    s = get_guild_settings(interaction.guild_id)
    i_emoji = s["emojis"].get("info", "ℹ️")
    embed = discord.Embed(title=f"{i_emoji} คำสั่งเพิ่มเติมอื่นๆ", color=0x95A5A6)
    embed.add_field(name="/มีอะไรสอบถามเพิ่มเติมไหม_th", value="ส่งข้อความถามผู้ใช้ใน Ticket (ไทย)", inline=False)
    embed.add_field(name="/มีอะไรสอบถามเพิ่มเติมไหม_en", value="ส่งข้อความถามผู้ใช้ใน Ticket (EN)", inline=False)
    embed.add_field(name="/update", value="ส่งประกาศแจ้งเตือน @everyone", inline=False)
    embed.add_field(name="/ตั้งค่าห้องtranscript", value="ตั้งค่าห้องเก็บประวัติ Ticket", inline=False)
    embed.add_field(name="/ตั้งค่าหมวดหมู่ticket", value="ตั้งค่าหมวดหมู่สำหรับสร้าง Ticket", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="ปรับแต่งทั้งหมด", description="Customize settings")
@app_commands.default_permissions(administrator=True)
async def cust_all(interaction: discord.Interaction):
    class CustModal(discord.ui.Modal, title="System Customization"):
        gid = discord.ui.TextInput(label="Roblox Group ID", required=False)
        sid = discord.ui.TextInput(label="Staff Role ID", required=False)
        img = discord.ui.TextInput(label="Ticket Image URL", required=False)
        async def on_submit(self, interaction: discord.Interaction):
            s = get_guild_settings(interaction.guild_id)
            if self.gid.value: s["roblox_group_id"] = parse_id(self.gid.value)
            if self.sid.value: s["ticket_staff_role_id"] = parse_id(self.sid.value)
            if self.img.value: s["ticket_image_url"] = self.img.value.strip()
            save_guild_settings(interaction.guild_id, s); await interaction.response.send_message("✅ Updated.", ephemeral=True)
    await interaction.response.send_modal(CustModal())

# =========================
# WEBHOOK & API
# =========================
@asynccontextmanager
async def lifespan(app: FastAPI): init_db(); asyncio.create_task(bot.start(DISCORD_TOKEN)); yield; await bot.close()
app = FastAPI(lifespan=lifespan)
@app.get("/")
async def root(): return {"status": "online"}

@app.post("/verify")
async def verify_ep(request: Request):
    data = await request.json(); rid, rname, gid = data.get("robloxId"), str(data.get("robloxUsername", "")).strip(), data.get("guildId")
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        # ค้นหาด้วย ID (แม่นยำที่สุด)
        row = conn.execute("SELECT discord_id FROM users WHERE roblox_id = ? AND verified = 0", (str(rid),)).fetchone()
        if not row: # สำรองด้วยชื่อ
            row = conn.execute("SELECT discord_id FROM users WHERE LOWER(pending_roblox_username) = ? AND verified = 0", (rname.lower(),)).fetchone()
    if not row: return {"ok": False, "message": "Verify on Discord first!"}
    dname, rank, err = await update_member_status(row[0], rid, rname, gid)
    if dname:
        with sqlite3.connect(DB_PATH) as conn: conn.execute("UPDATE users SET roblox_id=?, roblox_username=?, verified=1, pending_roblox_username=NULL WHERE discord_id=?", (str(rid), rname, row[0]))
        return {"ok": True, "discord_username": dname}
    return {"ok": False, "message": err}

if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port=PORT)
