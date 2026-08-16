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
    "role_ids": {
        "or": 1479699133001629797,
        "of_low": 1479699314078122094,
        "of_high": 1479699471603470432,
        "guest": None,
    },
    "rank_prefixes": {
        "1": "[P]",
        "2": "[C]",
        "3": "[B]",
        "4": "[A]",
        "5": "[S]",
        "10": "[TRN]",
        "20": "[DUC]",
        "30": "[UC]",
        "50": "[STAFF]",
        "100": "[DHAD]",
        "255": "[HAD]",
    },
}

DEVELOPER_IDS = [5711452462]

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
        
        rname = "Guest"
        prefix = "[Guest]"
        
        if is_dev:
            d_role = guild.get_role(parse_id(settings.get("developer_role_id")))
            if d_role: roles.append(d_role)
            rname = "Developer"
            prefix = "[DEV]"
        elif is_in_group:
            r_id = settings["role_ids"].get("or" if 1<=rank_val<=7 else "of_low" if 8<=rank_val<=11 else "of_high" if 12<=rank_val<=18 else None)
            r_role = guild.get_role(parse_id(r_id))
            if r_role: roles.append(r_role)
            rname = rank_name
            # Get prefix from settings based on rank value
            prefix = settings["rank_prefixes"].get(str(rank_val), f"[{rank_name}]")
        else:
            g_role = guild.get_role(parse_id(settings["role_ids"].get("guest")))
            if g_role: roles.append(g_role)

        roles = list({r.id: r for r in roles}.values())
        
        # New Nickname Format: [Tag] | Name
        new_nick = f"{prefix} | {roblox_username}"
        if len(new_nick) > 32: new_nick = new_nick[:32]
        
        try:
            await member.edit(roles=roles, nick=new_nick)
        except:
            await member.edit(roles=roles) # Fallback if nick fails (e.g. owner)
            
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
        rid = get_roblox_id_by_name(self.username.value)
        if not rid:
            await interaction.followup.send(f"❌ Roblox username **{self.username.value}** not found.", ephemeral=True)
            return
        settings = get_guild_settings(interaction.guild_id)
        is_in, _, _ = check_group_membership(rid, settings["roblox_group_id"])
        if not is_in and int(rid) not in DEVELOPER_IDS:
            await interaction.followup.send(f"❌ Join our group first: {settings['roblox_group_url']}", ephemeral=True)
            return
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO users (discord_id, pending_roblox_username, verified) VALUES (?, ?, 0)", (str(interaction.user.id), self.username.value.strip().lower()))
        embed = discord.Embed(title="Join game to verify", description=f"Username: **{self.username.value}**\n[Click to join game]({settings['roblox_map_url']})", color=0x00FF00)
        await interaction.followup.send(embed=embed, ephemeral=True)

class ChangeAccountButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Change Account", style=discord.ButtonStyle.primary, custom_id="change_acc_btn")
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(VerifyModal())

class MainVerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, emoji="✅", custom_id="persistent_verify_main")
    async def start_v(self, interaction: discord.Interaction, button: discord.ui.Button):
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            u = conn.execute("SELECT * FROM users WHERE discord_id = ?", (str(interaction.user.id),)).fetchone()
        
        if u and u["verified"] and u["roblox_id"]:
            embed = discord.Embed(title="Verification Status", color=0x3498DB)
            embed.add_field(name="Username", value=f"**{u['roblox_username']}**", inline=False)
            embed.add_field(name="ID Roblox", value=f"**{u['roblox_id']}**", inline=False)
            embed.add_field(name="Status", value="🟩 Verified", inline=False)
            view = discord.ui.View()
            view.add_item(ChangeAccountButton())
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_modal(VerifyModal())

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
        rname = self.username.value.strip()
        rid = get_roblox_id_by_name(rname)
        if not rid:
            await interaction.followup.send(f"❌ Roblox user **{rname}** not found.", ephemeral=True)
            return

        try:
            val = int(self.duration_val.value.strip())
        except ValueError:
            await interaction.followup.send("❌ Please enter a valid number for duration.", ephemeral=True)
            return

        now = datetime.now()
        delta = None
        expires_ts = None
        
        if self.unit == "Minutes": delta = timedelta(minutes=val)
        elif self.unit == "Hours": delta = timedelta(hours=val)
        elif self.unit == "Days": delta = timedelta(days=val)
        elif self.unit == "Months": delta = timedelta(days=val*30)
        elif self.unit == "Years": delta = timedelta(days=val*365)
        
        if delta:
            expires_at = now + delta
            expires_str = expires_at.strftime("%Y-%m-%d %H:%M:%S")
            expires_ts = expires_at.timestamp()
            status_text = f"Banned for {val} {self.unit}"
        else:
            expires_str = "Never (Permanent)"
            expires_ts = None
            status_text = "Permanently Banned"

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO bans (roblox_id, roblox_username, link, reason, status, image_url, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (str(rid), rname, self.link.value.strip(), self.reason.value.strip(), status_text, self.image_url.value.strip() if self.image_url.value else None, expires_ts))

        embed = discord.Embed(title="🚨 Update : ระบบADMIN", color=0xE74C3C)
        embed.add_field(name="Username Roblox", value=f"**{rname}**", inline=False)
        embed.add_field(name="Link Roblox", value=f"[Click Profile]({self.link.value.strip()})", inline=False)
        embed.add_field(name="Reason", value=self.reason.value.strip(), inline=False)
        embed.add_field(name="Status", value=f"🔴 {status_text}", inline=False)
        embed.add_field(name="Expires At", value=f"📅 {expires_str}", inline=False)
        if self.image_url.value:
            embed.set_image(url=self.image_url.value.strip())

        await interaction.channel.send(content="||@everyone||", embed=embed)
        await interaction.followup.send(f"✅ Banned {rname} until {expires_str}.", ephemeral=True)

# =========================
# SLASH COMMANDS
# =========================
@bot.tree.command(name="ยืนยันตัวตน", description="Setup Roblox verification panel")
@app_commands.default_permissions(administrator=True)
async def setup_v(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    embed = discord.Embed(title="Thai Military Verification", description="Click below to start.", color=0x2B2D31)
    await interaction.channel.send(embed=embed, view=MainVerifyView())
    await interaction.followup.send("✅ Panel created.", ephemeral=True)

@bot.tree.command(name="ตั้งค่าticket", description="Setup ticket panel")
@app_commands.default_permissions(administrator=True)
async def setup_t(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    embed = discord.Embed(title="❗ Contact Staff / Support", description="Select a topic to open a ticket.", color=0xE74C3C)
    await interaction.channel.send(embed=embed, view=TicketSetupView())
    await interaction.followup.send("✅ Ticket panel created.", ephemeral=True)

@bot.tree.command(name="game-ban", description="Ban a player from the game")
@app_commands.default_permissions(administrator=True)
@app_commands.choices(unit=[
    app_commands.Choice(name="Minutes", value="Minutes"),
    app_commands.Choice(name="Hours", value="Hours"),
    app_commands.Choice(name="Days", value="Days"),
    app_commands.Choice(name="Months", value="Months"),
    app_commands.Choice(name="Years", value="Years"),
    app_commands.Choice(name="Permanent", value="Permanent"),
])
async def game_ban(interaction: discord.Interaction, unit: str):
    if interaction.channel_id != ALLOWED_BAN_CHANNEL_ID:
        await interaction.response.send_message(f"❌ This command can only be used in <#{ALLOWED_BAN_CHANNEL_ID}>", ephemeral=True)
        return
    await interaction.response.send_modal(GameBanModal(unit))

@bot.tree.command(name="unban", description="Unban a player from the game")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(username="Roblox username to unban")
async def unban_cmd(interaction: discord.Interaction, username: str):
    await interaction.response.defer(ephemeral=True)
    rid = get_roblox_id_by_name(username)
    if not rid:
        await interaction.followup.send(f"❌ Roblox user **{username}** not found.", ephemeral=True)
        return

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("DELETE FROM bans WHERE roblox_id = ?", (str(rid),))
        if cursor.rowcount > 0:
            await interaction.followup.send(f"✅ Successfully unbanned **{username}**.", ephemeral=True)
        else:
            await interaction.followup.send(f"ℹ️ **{username}** is not in the ban list.", ephemeral=True)

@bot.tree.command(name="ปิดticket", description="Close current ticket and generate HTML transcript")
@app_commands.default_permissions(administrator=True)
async def close_t(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    settings = get_guild_settings(interaction.guild_id)
    channel = interaction.channel
    html_content = f"<html><head><meta charset='utf-8'><style>body {{ background-color: #36393f; color: #dcddde; font-family: sans-serif; padding: 20px; }} .msg {{ display: flex; margin-bottom: 15px; }} .avatar {{ width: 40px; height: 40px; border-radius: 50%; margin-right: 15px; background-color: #4f545c; }} .author {{ font-weight: bold; color: #ffffff; margin-right: 10px; }} .time {{ font-size: 0.75rem; color: #72767d; }}</style></head><body><h1>Transcript: {channel.name}</h1>"
    async for m in channel.history(limit=1000, oldest_first=True):
        html_content += f"<div class='msg'><img class='avatar' src='{m.author.display_avatar.url}'><div class='content'><div class='header'><span class='author'>{m.author.display_name}</span><span class='time'>{m.created_at.strftime('%Y-%m-%d %H:%M')}</span></div><div class='text'>{m.clean_content or '[Attachment]'}</div></div></div>"
    html_content += "</body></html>"
    file = discord.File(io.BytesIO(html_content.encode()), filename=f"transcript-{channel.name}.html")
    t_ch = interaction.guild.get_channel(parse_id(settings.get("transcript_channel_id")))
    if t_ch: await t_ch.send(content=f"📁 Transcript: `{channel.name}`", file=file)
    await interaction.followup.send("🔒 Closing in 3s...", ephemeral=True)
    with sqlite3.connect(DB_PATH) as conn: conn.execute("DELETE FROM active_tickets WHERE channel_id = ?", (str(channel.id),))
    await asyncio.sleep(3); await channel.delete()

async def send_ask_more_embed(interaction, text):
    settings = get_guild_settings(interaction.guild_id)
    embed = discord.Embed(description=text, color=0x3498DB)
    if settings.get("ticket_image_url"): embed.set_image(url=settings["ticket_image_url"])
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Sent.", ephemeral=True)

@bot.tree.command(name="มีอะไรสอบถามเพิ่มเติมไหม_en", description="Ask for more questions (EN)")
async def ask_en(interaction: discord.Interaction): await send_ask_more_embed(interaction, "Do you have any further questions? If not, the staff will proceed to close this ticket.")

@bot.tree.command(name="มีอะไรสอบถามเพิ่มเติมไหม_th", description="Ask for more questions (TH)")
async def ask_th(interaction: discord.Interaction): await send_ask_more_embed(interaction, "มีอะไรสอบถามเพิ่มเติมไหมครับ/ค่ะ หากไม่มีแล้วทีมงานขอปิด Ticket นะครับ/ค่ะ")

class UpdateModal(discord.ui.Modal, title="System Announcement"):
    message = discord.ui.TextInput(label="Message", style=discord.TextStyle.paragraph, placeholder="Enter announcement message...", required=True)
    image_url = discord.ui.TextInput(label="Image URL (Optional)", style=discord.TextStyle.short, placeholder="https://...", required=False)
    note = discord.ui.TextInput(label="Note (Optional)", style=discord.TextStyle.short, placeholder="Small note at the bottom...", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        embed = discord.Embed(description=self.message.value, color=0x3498DB)
        if self.image_url.value:
            embed.set_image(url=self.image_url.value.strip())
        if self.note.value:
            embed.add_field(name="\u200b", value=f"-# {self.note.value.strip()}", inline=False)
        
        await interaction.channel.send(content="||@everyone||", embed=embed)
        await interaction.followup.send("✅ Announcement sent successfully.", ephemeral=True)

@bot.tree.command(name="update", description="Send announcement update via modal")
@app_commands.default_permissions(administrator=True)
async def update_cmd(interaction: discord.Interaction):
    await interaction.response.send_modal(UpdateModal())

@bot.tree.command(name="ตั้งค่าห้องtranscript", description="Set transcript channel")
@app_commands.default_permissions(administrator=True)
async def set_trans(interaction: discord.Interaction, channel: discord.TextChannel):
    s = get_guild_settings(interaction.guild_id); s["transcript_channel_id"] = channel.id; save_guild_settings(interaction.guild_id, s)
    await interaction.response.send_message(f"✅ Transcript channel set to {channel.mention}", ephemeral=True)

@bot.tree.command(name="ตั้งค่าหมวดหมู่ticket", description="Set ticket category")
@app_commands.default_permissions(administrator=True)
async def set_cat(interaction: discord.Interaction, category: discord.CategoryChannel):
    s = get_guild_settings(interaction.guild_id); s["ticket_category_id"] = category.id; save_guild_settings(interaction.guild_id, s)
    await interaction.response.send_message(f"✅ Ticket category set to **{category.name}**", ephemeral=True)

@bot.tree.command(name="ปรับแต่งทั้งหมด", description="Customize settings")
@app_commands.default_permissions(administrator=True)
async def cust_all(interaction: discord.Interaction):
    class CustModal(discord.ui.Modal, title="System Customization"):
        gid = discord.ui.TextInput(label="Roblox Group ID", required=False)
        sid = discord.ui.TextInput(label="Staff Role ID", required=False)
        img = discord.ui.TextInput(label="Ticket Image URL", required=False)
        pfx = discord.ui.TextInput(label="Prefixes (e.g. 1=[P];2=[C];)", style=discord.TextStyle.paragraph, required=False)
        async def on_submit(self, interaction: discord.Interaction):
            s = get_guild_settings(interaction.guild_id)
            if self.gid.value: s["roblox_group_id"] = parse_id(self.gid.value)
            if self.sid.value: s["ticket_staff_role_id"] = parse_id(self.sid.value)
            if self.img.value: s["ticket_image_url"] = self.img.value.strip()
            if self.pfx.value:
                for item in self.pfx.value.split(";"):
                    if "=" in item:
                        k,v = item.split("=", 1)
                        s["rank_prefixes"][k.strip().lower()] = v.strip()
            save_guild_settings(interaction.guild_id, s)
            await interaction.response.send_message("✅ Settings updated.", ephemeral=True)
    await interaction.response.send_modal(CustModal())

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
    print(f"[BanCheck] Checking status for Roblox ID: {roblox_id}")
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM bans WHERE roblox_id = ?", (str(roblox_id),)).fetchone()
    
    if row:
        expires_at = row["expires_at"]
        if expires_at and datetime.now().timestamp() > expires_at:
            print(f"[BanCheck] Ban for {roblox_id} expired. Auto-unbanning.")
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("DELETE FROM bans WHERE roblox_id = ?", (str(roblox_id),))
            return {"banned": False}
            
        print(f"[BanCheck] Found active ban for {roblox_id}: {row['reason']}")
        return {"banned": True, "reason": row["reason"], "status": row["status"]}
    
    print(f"[BanCheck] No ban found for {roblox_id}")
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
    if not err:
        with sqlite3.connect(DB_PATH) as conn: conn.execute("UPDATE users SET roblox_id=?, roblox_username=?, verified=1, pending_roblox_username=NULL WHERE discord_id=?", (str(rid), rname, row[0]))
        return {"ok": True, "discord_username": dname}
    return {"ok": False, "message": err}

if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port=PORT)
