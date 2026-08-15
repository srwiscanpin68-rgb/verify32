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
            settings.update(saved)
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
        print(f"[Bot] Logged in as {self.user}")

bot = MyBot()

# =========================
# UTILS
# =========================
def check_group_membership(roblox_id, group_id):
    try:
        resp = requests.get(f"https://groups.roblox.com/v1/users/{roblox_id}/groups/roles", timeout=10)
        for group in resp.json().get("data", []):
            if group["group"]["id"] == int(group_id):
                return True, group["role"]["name"]
    except: pass
    return False, None

async def update_member_status(discord_id, roblox_id, roblox_username, guild_id=None):
    guild = bot.get_guild(int(guild_id)) if guild_id else (bot.guilds[0] if bot.guilds else None)
    if not guild: return None, "Discord Guild not found."
    settings = get_guild_settings(guild.id)
    try:
        member = await guild.fetch_member(int(discord_id))
        is_in_group, _ = check_group_membership(roblox_id, settings["roblox_group_id"])
        is_dev = int(roblox_id) in DEVELOPER_IDS

        if not is_in_group and not is_dev:
            return None, "Not in Roblox Group."

        v_role_id = parse_id(settings.get("verified_role_id", 1508479215908028543))
        v_role = guild.get_role(v_role_id)
        if v_role: await member.add_roles(v_role)
        return member.display_name, None
    except Exception as e: return None, str(e)

# =========================
# UI COMPONENTS
# =========================
class VerifyModal(discord.ui.Modal, title="Roblox Verification"):
    username = discord.ui.TextInput(label="Roblox Username", placeholder="Enter username...", min_length=3, max_length=20, required=True)
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        resp = requests.post("https://users.roblox.com/v1/usernames/users", json={"usernames": [self.username.value], "excludeBannedUsers": True})
        data = resp.json()
        if not data.get("data"):
            await interaction.followup.send(f"❌ User {self.username.value} not found.", ephemeral=True)
            return
        
        settings = get_guild_settings(interaction.guild_id)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO users (discord_id, pending_roblox_username, verified) VALUES (?, ?, 0)", (str(interaction.user.id), self.username.value.strip().lower()))
        
        embed = discord.Embed(title="Join game to verify", description=f"Username: **{self.username.value}**\n\n[Click to join game]({settings['roblox_map_url']})", color=0x00FF00)
        await interaction.followup.send(embed=embed, ephemeral=True)

class MainVerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, emoji="✅", custom_id="persistent_verify_main")
    async def start_v(self, interaction: discord.Interaction, button: discord.ui.Button):
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            u = conn.execute("SELECT * FROM users WHERE discord_id = ?", (str(interaction.user.id),)).fetchone()
        
        if u and u["verified"]:
            embed = discord.Embed(title="Verification Status", color=0x3498DB)
            embed.add_field(name="Username", value=f"**{u['roblox_username']}**", inline=False)
            embed.add_field(name="ID Roblox", value=f"**{u['roblox_id']}**", inline=False)
            embed.add_field(name="Status", value="🟩 Verified", inline=False)
            
            view = discord.ui.View()
            btn = discord.ui.Button(label="Change Account", style=discord.ButtonStyle.primary)
            btn.callback = lambda i: i.response.send_modal(VerifyModal())
            view.add_item(btn)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_modal(VerifyModal())

# Ticket logic (kept minimal for stability)
class TicketSetupView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.select(placeholder="Contact Support", options=[discord.SelectOption(label="Support", value="support", emoji="🛡️")], custom_id="ticket_select")
    async def callback(self, interaction, select):
        await interaction.response.send_message("Ticket system is active.", ephemeral=True)

# =========================
# FASTAPI & WEBHOOK
# =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    asyncio.create_task(bot.start(DISCORD_TOKEN))
    yield
    await bot.close()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"status": "online", "bot": str(bot.user), "endpoints": ["/verify (POST)"]}

@app.post("/verify")
async def verify_ep(request: Request):
    try:
        data = await request.json()
        print(f"[Webhook] Request: {data}")
        rid, rname, gid = data.get("robloxId"), str(data.get("robloxUsername", "")).strip(), data.get("guildId")
        
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT discord_id FROM users WHERE LOWER(TRIM(pending_roblox_username)) = ? ORDER BY rowid DESC LIMIT 1", (rname.lower(),)).fetchone()
        
        if not row:
            print(f"[Webhook] User {rname} not in pending list.")
            return {"ok": False, "message": "Verify on Discord first!"}
        
        dname, err = await update_member_status(row["discord_id"], rid, rname, gid)
        if not err:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("UPDATE users SET roblox_id=?, roblox_username=?, verified=1, pending_roblox_username=NULL WHERE discord_id=?", (str(rid), rname, row["discord_id"]))
            print(f"[Webhook] Success for {rname}")
            return {"ok": True, "discord_username": dname}
        
        print(f"[Webhook] Error: {err}")
        return {"ok": False, "message": err}
    except Exception as e:
        print(f"[Webhook] Critical: {e}")
        return {"ok": False, "message": "Server Error"}

# =========================
# SLASH COMMANDS
# =========================
@bot.tree.command(name="ยืนยันตัวตน", description="Setup verification panel")
@app_commands.default_permissions(administrator=True)
async def setup_v(interaction: discord.Interaction):
    await interaction.response.send_message("✅ Panel created.", ephemeral=True)
    await interaction.channel.send(embed=discord.Embed(title="Roblox Verification", description="Click below to start.", color=0x2B2D31), view=MainVerifyView())

@bot.tree.command(name="ปรับแต่งทั้งหมด", description="Customize settings")
@app_commands.default_permissions(administrator=True)
async def cust_all(interaction: discord.Interaction):
    class CustModal(discord.ui.Modal, title="Settings"):
        gid = discord.ui.TextInput(label="Roblox Group ID", required=False)
        url = discord.ui.TextInput(label="Map URL", required=False)
        async def on_submit(self, interaction: discord.Interaction):
            s = get_guild_settings(interaction.guild_id)
            if self.gid.value: s["roblox_group_id"] = parse_id(self.gid.value)
            if self.url.value: s["roblox_map_url"] = self.url.value.strip()
            save_guild_settings(interaction.guild_id, s)
            await interaction.response.send_message("✅ Updated.", ephemeral=True)
    await interaction.response.send_modal(CustModal())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
