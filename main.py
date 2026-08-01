import os
import asyncio
import sqlite3
import requests
import discord
from discord.ext import commands
from fastapi import FastAPI, Request
from contextlib import asynccontextmanager
import uvicorn

# ==========================================================
# ⚙️ CONFIGURATION
# ==========================================================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "ใส่_TOKEN_ของคุณที่นี่")
PORT = int(os.getenv("PORT", 8888))

ROBLOX_GROUP_ID = 35646818
VERIFIED_ROLE_ID = 1532801945981423847

RANK_MAPPING = {
    "OR_ROLE_ID": 1532804608777523200,
    "OR_RANKS": [1, 2, 3, 4, 5, 6, 7],
    "OF_LOW_ROLE_ID": 1532804655375978646,
    "OF_LOW_RANKS": [8, 9, 10, 11],
    "OF_HIGH_ROLE_ID": 1532806100611629076,
    "OF_HIGH_RANKS": [12, 13, 14, 15, 16, 17, 18]
}

RANK_NAMES = {
    1: "PC", 2: "PEC", 3: "CPL", 4: "SGT", 5: "SSG", 6: "SFC", 7: "MSG",
    8: "CADET", 9: "LTP", 10: "1LT", 11: "CPT", 12: "MAJ", 13: "LTC",
    14: "COL", 15: "SRCOL", 16: "PMG", 17: "MG", 18: "GEN"
}

VERIFIED_EMOJI = ":__~242:"
DB_PATH = "database.db"

# --- Database ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS users (discord_id TEXT PRIMARY KEY, roblox_id TEXT, roblox_username TEXT, verified INTEGER DEFAULT 0, pending_roblox_username TEXT)")
    conn.commit(); conn.close()

def get_user(discord_id):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM users WHERE discord_id = ?", (str(discord_id),)).fetchone()
    conn.close(); return row

def update_pending(discord_id, username):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO users (discord_id, pending_roblox_username, verified) VALUES (?, ?, 0)", (str(discord_id), username))
    conn.commit(); conn.close()

def verify_user_db(roblox_id, roblox_username):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT discord_id FROM users WHERE pending_roblox_username = ?", (roblox_username,)).fetchone()
    if row:
        did = row['discord_id']
        conn.execute("UPDATE users SET roblox_id = ?, roblox_username = ?, verified = 1, pending_roblox_username = NULL WHERE discord_id = ?", (str(roblox_id), roblox_username, did))
        conn.commit(); conn.close(); return did
    conn.close(); return None

# --- Bot ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

async def update_member_status(discord_id, roblox_id, roblox_username):
    if not bot.guilds: return
    guild = bot.guilds[0]
    try:
        member = await guild.fetch_member(int(discord_id))
        resp = requests.get(f"https://groups.roblox.com/v1/users/{roblox_id}/groups/roles").json()
        group_info = next((g for g in resp['data'] if g['group']['id'] == ROBLOX_GROUP_ID), None)
        roles = [guild.get_role(VERIFIED_ROLE_ID)]
        rank_name = "Guest"
        if group_info:
            rv = group_info['role']['rank']
            rank_name = RANK_NAMES.get(rv, group_info['role']['name'])
            if rv in RANK_MAPPING['OR_RANKS']: roles.append(guild.get_role(RANK_MAPPING['OR_ROLE_ID']))
            elif rv in RANK_MAPPING['OF_LOW_RANKS']: roles.append(guild.get_role(RANK_MAPPING['OF_LOW_ROLE_ID']))
            elif rv in RANK_MAPPING['OF_HIGH_RANKS']: roles.append(guild.get_role(RANK_MAPPING['OF_HIGH_ROLE_ID']))
        await member.edit(roles=[r for r in roles if r], nick=f"{rank_name} | {roblox_username}"[:32])
    except Exception as e: print(f"Error: {e}")

class VerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label='ยืนยันตัวตน', style=discord.ButtonStyle.success, emoji='✅', custom_id='start_verify')
    async def start_verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        u = get_user(interaction.user.id)
        if u and u['verified']:
            emb = discord.Embed(title="#พบข้อมูล Roblox Account อยู่แล้ว", color=discord.Color.blue())
            emb.add_field(name="Roblox:", value=u['roblox_username'], inline=True)
            await interaction.response.send_message(embed=emb, view=ReVerifyView(), ephemeral=True)
        else:
            modal = discord.ui.Modal(title='ยืนยันตัวตน Roblox')
            name_input = discord.ui.TextInput(label='ใส่ชื่อใน Roblox', placeholder='ตัวอย่าง: manpop79', required=True)
            async def on_submit(it: discord.Interaction):
                update_pending(it.user.id, name_input.value)
                await it.response.send_message(f"ชื่อ: {name_input.value}\nกรุณาเข้าแมพ Roblox และพิมพ์ว่า `ยืนยันตัวตน`", ephemeral=True)
            modal.on_submit = on_submit
            await interaction.response.send_modal(modal)

class ReVerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label='เปลี่ยน Account', style=discord.ButtonStyle.primary, custom_id='change_account')
    async def change_account(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = discord.ui.Modal(title='เปลี่ยนบัญชี Roblox')
        name_input = discord.ui.TextInput(label='ใส่ชื่อใหม่ใน Roblox', required=True)
        async def on_submit(it: discord.Interaction):
            update_pending(it.user.id, name_input.value)
            await it.response.send_message(f"เปลี่ยนเป็นชื่อ: {name_input.value}\nกรุณาเข้าแมพใหม่เพื่อยืนยัน", ephemeral=True)
        modal.on_submit = on_submit
        await interaction.response.send_modal(modal)
    @discord.ui.button(label='อัพเดทยศ', style=discord.ButtonStyle.success, custom_id='update_rank')
    async def update_rank(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("กำลังอัพเดทยศ...", ephemeral=True)
        u = get_user(interaction.user.id)
        if u: await update_member_status(interaction.user.id, u['roblox_id'], u['roblox_username'])
        await interaction.edit_original_response(content="อัพเดทเรียบร้อย!")

@bot.event
async def on_ready():
    bot.add_view(VerifyView()); bot.add_view(ReVerifyView())
    print(f'Bot {bot.user} is Online!')

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_verify(ctx):
    await ctx.send(embed=discord.Embed(title="ระบบยืนยันตัวตน", description="กดปุ่มด้านล่างเพื่อเริ่ม", color=discord.Color.green()), view=VerifyView())

# --- FastAPI ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    asyncio.create_task(bot.start(DISCORD_TOKEN))
    yield
    await bot.close()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root(): return {"status": "online"}

@app.post("/verify")
async def verify_endpoint(request: Request):
    data = await request.json()
    did = verify_user_db(data.get("robloxId"), data.get("robloxUsername"))
    if did:
        asyncio.create_task(update_member_status(did, data.get("robloxId"), data.get("robloxUsername")))
        return {"success": True}
    return {"success": False}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, log_level="info")
