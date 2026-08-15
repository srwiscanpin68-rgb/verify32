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
    "verified_role_id": 1508479215908028543,
    "ticket_staff_role_id": 1508479215908028544,
    "transcript_channel_id": None,
    "ticket_category_id": None,
    "ticket_image_url": None,
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

def get_guild_settings(guild_id):
    if not guild_id: return json.loads(json.dumps(DEFAULT_SETTINGS))
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT settings_json FROM guild_settings WHERE guild_id = ?", (str(guild_id),)).fetchone()
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    if row:
        try:
            saved = json.loads(row[0])
            settings.update({k: v for k, v in saved.items()})
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
        self.add_view(VerifyView())
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
        if data.get("data"): return data["data"][0]["id"]
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
        is_in_group, _, _ = check_group_membership(roblox_id, settings["roblox_group_id"])
        is_dev = int(roblox_id) in DEVELOPER_IDS

        if not is_in_group and not is_dev:
            return None, "User is not in the required Roblox group."

        verified_role_id = parse_id(settings.get("verified_role_id", 1508479215908028543))
        verified_role = guild.get_role(verified_role_id)
        if verified_role and verified_role not in member.roles:
            await member.add_roles(verified_role)

        return member.display_name, None
    except discord.HTTPException as e:
        msg = "Missing Permissions" if e.code == 50013 else "Member not found" if e.code == 10007 else f"Discord Error {e.code}"
        return None, msg
    except Exception as e: return None, str(e)

# =========================
# UI COMPONENTS
# =========================
class VerifyModal(discord.ui.Modal, title="Roblox Verification"):
    username = discord.ui.TextInput(label="Roblox Username", placeholder="Enter your Roblox username...", min_length=3, max_length=20, required=True)
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        rid = get_roblox_id_by_name(self.username.value)
        if not rid:
            await interaction.followup.send(f"❌ Roblox username **{self.username.value}** not found.", ephemeral=True)
            return
        settings = get_guild_settings(interaction.guild_id)
        is_in, _, _ = check_group_membership(rid, settings["roblox_group_id"])
        if not is_in and int(rid) not in DEVELOPER_IDS:
            await interaction.followup.send(f"❌ You are not in the Roblox group yet!\n[Click here to join]({settings['roblox_group_url']})", ephemeral=True)
            return
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO users (discord_id, pending_roblox_username, verified) VALUES (?, ?, 0)", (str(interaction.user.id), self.username.value.strip().lower()))
        embed = discord.Embed(title="Join game to verify", description=f"Username: **{self.username.value}**\n\n[Click to join game]({settings['roblox_map_url']})", color=0x00FF00)
        await interaction.followup.send(embed=embed, ephemeral=True)

class VerifyView(discord.ui.View):
    def __init__(self, user_data=None):
        super().__init__(timeout=None)
        if user_data and user_data["verified"]:
            self.add_item(ChangeAccountButton())

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

# Ticket Components
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
        staff_role = guild.get_role(parse_id(settings.get("ticket_staff_role_id", 1508479215908028544)))
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

# =========================
# SLASH COMMANDS
# =========================
@bot.tree.command(name="ยืนยันตัวตน", description="Setup Roblox verification panel")
@app_commands.default_permissions(administrator=True)
async def setup_v(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        embed = discord.Embed(title="Thai Military Verification", description="Click below to start verification.", color=0x2B2D31)
        await interaction.channel.send(embed=embed, view=MainVerifyView())
        await interaction.followup.send("✅ Verification panel created.", ephemeral=True)
    except Exception as e: await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

@bot.tree.command(name="ตั้งค่าticket", description="Setup ticket panel")
@app_commands.default_permissions(administrator=True)
async def setup_t(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        embed = discord.Embed(title="❗ Contact Staff / Support", description="Select a topic to open a ticket.", color=0xE74C3C)
        await interaction.channel.send(embed=embed, view=TicketSetupView())
        await interaction.followup.send("✅ Ticket panel created.", ephemeral=True)
    except Exception as e: await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

@bot.tree.command(name="ปิดticket", description="Close current ticket and generate HTML transcript")
@app_commands.default_permissions(administrator=True)
async def close_t(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    settings = get_guild_settings(interaction.guild_id)
    channel = interaction.channel
    
    html_content = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ background-color: #36393f; color: #dcddde; font-family: sans-serif; padding: 20px; }}
            .msg {{ display: flex; margin-bottom: 15px; }}
            .avatar {{ width: 40px; height: 40px; border-radius: 50%; margin-right: 15px; background-color: #4f545c; }}
            .content {{ display: flex; flex-direction: column; }}
            .header {{ display: flex; align-items: center; margin-bottom: 5px; }}
            .author {{ font-weight: bold; color: #ffffff; margin-right: 10px; }}
            .time {{ font-size: 0.75rem; color: #72767d; }}
            .text {{ line-height: 1.4; white-space: pre-wrap; word-break: break-word; }}
            .ticket-header {{ border-bottom: 1px solid #4f545c; padding-bottom: 10px; margin-bottom: 20px; }}
        </style>
    </head>
    <body>
        <div class="ticket-header">
            <h1>Transcript: {channel.name}</h1>
            <p>Closed by: {interaction.user.name} | Date: {discord.utils.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
        </div>
    """
    async for m in channel.history(limit=1000, oldest_first=True):
        timestamp = m.created_at.strftime('%Y-%m-%d %H:%M')
        content = m.clean_content or "[Embed or Attachment]"
        avatar_url = m.author.display_avatar.url if m.author.display_avatar else ""
        html_content += f"""
        <div class="msg">
            <img class="avatar" src="{avatar_url}">
            <div class="content">
                <div class="header">
                    <span class="author">{m.author.display_name}</span>
                    <span class="time">{timestamp}</span>
                </div>
                <div class="text">{content}</div>
            </div>
        </div>
        """
    html_content += "</body></html>"
    file = discord.File(io.BytesIO(html_content.encode("utf-8")), filename=f"transcript-{channel.name}.html")
    t_ch_id = parse_id(settings.get("transcript_channel_id"))
    if t_ch_id:
        t_ch = interaction.guild.get_channel(t_ch_id)
        if t_ch: await t_ch.send(content=f"📁 **HTML Transcript for:** `{channel.name}`", file=file)
    
    await interaction.followup.send("🔒 Closing ticket and saving HTML transcript in 3s...", ephemeral=True)
    with sqlite3.connect(DB_PATH) as conn: conn.execute("DELETE FROM active_tickets WHERE channel_id = ?", (str(channel.id),))
    await asyncio.sleep(3); await channel.delete()

async def send_ask_more_embed(interaction, text):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT * FROM active_tickets WHERE channel_id = ?", (str(interaction.channel_id),)).fetchone()
    if not row:
        await interaction.response.send_message("❌ This command can only be used inside an active ticket channel.", ephemeral=True)
        return
    settings = get_guild_settings(interaction.guild_id)
    embed = discord.Embed(description=text, color=0x3498DB)
    if settings.get("ticket_image_url"): embed.set_image(url=settings["ticket_image_url"])
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Message sent.", ephemeral=True)

@bot.tree.command(name="มีอะไรสอบถามเพิ่มเติมไหม_en", description="Ask if user needs further assistance (English)")
async def ask_more_en(interaction: discord.Interaction):
    await send_ask_more_embed(interaction, "Do you have any further questions? If not, the staff will proceed to close this ticket.")

@bot.tree.command(name="มีอะไรสอบถามเพิ่มเติมไหม_th", description="Ask if user needs further assistance (Thai)")
async def ask_more_th(interaction: discord.Interaction):
    await send_ask_more_embed(interaction, "มีอะไรสอบถามเพิ่มเติมไหมครับ/ค่ะ หากไม่มีแล้วทีมงานขอปิด Ticket นะครับ/ค่ะ")

@bot.tree.command(name="ตั้งค่าห้องtranscript", description="Set transcript channel")
@app_commands.default_permissions(administrator=True)
async def set_trans(interaction: discord.Interaction, channel: discord.TextChannel):
    s = get_guild_settings(interaction.guild_id); s["transcript_channel_id"] = channel.id; save_guild_settings(interaction.guild_id, s)
    await interaction.response.send_message(f"✅ Transcript channel set to {channel.mention}", ephemeral=True)

@bot.tree.command(name="ตั้งค่าหมวดหมู่ticket", description="Set ticket category")
@app_commands.default_permissions(administrator=True)
async def set_cat(interaction: discord.Interaction, category: discord.CategoryChannel):
    s = get_guild_settings(interaction.guild_id); s["ticket_category_id"] = category.id; save_guild_settings(interaction.guild_id, s)
    await interaction.response.send_message(f"✅ Category set to **{category.name}**", ephemeral=True)

@bot.tree.command(name="ดูการตั้งค่า", description="Show current settings")
@app_commands.default_permissions(administrator=True)
async def show_s(interaction: discord.Interaction):
    s = get_guild_settings(interaction.guild_id)
    e = discord.Embed(title="Current Server Settings", color=0x3498DB)
    e.add_field(name="Group ID", value=str(s["roblox_group_id"]), inline=False)
    e.add_field(name="Verified Role ID", value=str(s["verified_role_id"]), inline=False)
    e.add_field(name="Staff Role", value=f"<@&{s['ticket_staff_role_id']}>", inline=False)
    await interaction.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="ปรับแต่งทั้งหมด", description="Customize all settings")
@app_commands.default_permissions(administrator=True)
async def cust_all(interaction: discord.Interaction):
    class CustModal(discord.ui.Modal, title="System Customization"):
        gid = discord.ui.TextInput(label="Roblox Group ID", required=False)
        sid = discord.ui.TextInput(label="Staff Role ID", required=False)
        vrid = discord.ui.TextInput(label="Verified Role ID", required=False)
        img = discord.ui.TextInput(label="Ticket Image URL", required=False)
        async def on_submit(self, interaction: discord.Interaction):
            s = get_guild_settings(interaction.guild_id)
            if self.gid.value: s["roblox_group_id"] = parse_id(self.gid.value)
            if self.sid.value: s["ticket_staff_role_id"] = parse_id(self.sid.value)
            if self.vrid.value: s["verified_role_id"] = parse_id(self.vrid.value)
            if self.img.value: s["ticket_image_url"] = self.img.value.strip()
            save_guild_settings(interaction.guild_id, s)
            await interaction.response.send_message("✅ Settings updated.", ephemeral=True)
    await interaction.response.send_modal(CustModal())

# =========================
# WEBHOOK
# =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(); asyncio.create_task(bot.start(DISCORD_TOKEN))
    yield
    await bot.close()

app = FastAPI(lifespan=lifespan)

@app.post("/verify")
async def verify_ep(request: Request):
    data = await request.json()
    rid, rname, gid = data.get("robloxId"), str(data.get("robloxUsername", "")).strip(), data.get("guildId")
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT discord_id FROM users WHERE LOWER(TRIM(pending_roblox_username)) = ? ORDER BY rowid DESC LIMIT 1", (rname.lower(),)).fetchone()
    if not row: return {"ok": False, "message": "Username not found in pending list."}
    
    dname, err = await update_member_status(row[0], rid, rname, gid)
    if not err:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE users SET roblox_id=?, roblox_username=?, verified=1, pending_roblox_username=NULL WHERE discord_id=?", (str(rid), rname, row[0]))
        return {"ok": True, "discord_username": dname, "roblox_id": str(rid)}
    return {"ok": False, "message": err}

if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port=PORT)
