import os
import asyncio
import sqlite3
import requests
import uvicorn
import discord
from discord import app_commands
from discord.ext import commands
from fastapi import FastAPI, Request

# ==========================================================
# ⚙️ CONFIGURATION (ใส่ Token และตั้งค่าที่นี่)
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

# ==========================================================
# 🗄️ DATABASE SYSTEM
# ==========================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            discord_id TEXT PRIMARY KEY,
            roblox_id TEXT,
            roblox_username TEXT,
            verified INTEGER DEFAULT 0,
            pending_roblox_username TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_user(discord_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE discord_id = ?", (str(discord_id),))
    row = cursor.fetchone()
    conn.close()
    return row

def update_pending(discord_id, username):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO users (discord_id, pending_roblox_username, verified) VALUES (?, ?, 0)", (str(discord_id), username))
    conn.commit()
    conn.close()

def verify_user_db(roblox_id, roblox_username):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT discord_id FROM users WHERE pending_roblox_username = ?", (roblox_username,))
    row = cursor.fetchone()
    if row:
        discord_id = row['discord_id']
        cursor.execute("UPDATE users SET roblox_id = ?, roblox_username = ?, verified = 1, pending_roblox_username = NULL WHERE discord_id = ?", (str(roblox_id), roblox_username, discord_id))
        conn.commit()
        conn.close()
        return discord_id
    conn.close()
    return None

# ==========================================================
# 🤖 BOT & API SYSTEM
# ==========================================================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
app = FastAPI()

@app.get("/")
async def root(): return {"status": "online"}

@app.post("/verify")
async def verify_endpoint(request: Request):
    data = await request.json()
    discord_id = verify_user_db(data.get("robloxId"), data.get("robloxUsername"))
    if discord_id:
        asyncio.create_task(update_member_status(discord_id, data.get("robloxId"), data.get("robloxUsername")))
        return {"success": True}
    return {"success": False}

async def update_member_status(discord_id, roblox_id, roblox_username):
    if not bot.guilds: return False
    guild = bot.guilds[0]
    try:
        member = await guild.fetch_member(int(discord_id))
        resp = requests.get(f"https://groups.roblox.com/v1/users/{roblox_id}/groups/roles").json()
        group_info = next((g for g in resp['data'] if g['group']['id'] == ROBLOX_GROUP_ID), None)
        roles = [guild.get_role(VERIFIED_ROLE_ID)]
        rank_name, rank_val = "Guest", 0
        if group_info:
            rank_val = group_info['role']['rank']
            rank_name = RANK_NAMES.get(rank_val, group_info['role']['name'])
            if rank_val in RANK_MAPPING['OR_RANKS']: roles.append(guild.get_role(RANK_MAPPING['OR_ROLE_ID']))
            elif rank_val in RANK_MAPPING['OF_LOW_RANKS']: roles.append(guild.get_role(RANK_MAPPING['OF_LOW_ROLE_ID']))
            elif rank_val in RANK_MAPPING['OF_HIGH_RANKS']: roles.append(guild.get_role(RANK_MAPPING['OF_HIGH_ROLE_ID']))
        await member.edit(roles=[r for r in roles if r], nick=f"{rank_name} | {roblox_username}"[:32])
        return True
    except Exception as e:
        print(f"Update Error: {e}"); return False

# --- UI COMPONENTS ---
class VerifyModal(discord.ui.Modal, title='ยืนยันตัวตน Roblox'):
    username = discord.ui.TextInput(label='ใส่ชื่อใน Roblox', placeholder='ตัวอย่าง: manpop79', required=True)
    async def on_submit(self, interaction: discord.Interaction):
        update_pending(interaction.user.id, self.username.value)
        embed = discord.Embed(title="ข้อมูลการยืนยันตัวตน", color=discord.Color.gold())
        embed.add_field(name="ชื่อ:", value=self.username.value, inline=True)
        embed.add_field(name="เข้าแมพ:", value="เพื่อยืนยันตัวตน", inline=True)
        embed.description = "กรุณาเข้าแมพ Roblox และพิมพ์ในช่องแชทว่า `ยืนยันตัวตน`"
        await interaction.response.send_message(embed=embed, ephemeral=True)

class VerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label='ยืนยันตัวตน', style=discord.ButtonStyle.success, emoji='✅', custom_id='start_verify')
    async def start_verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = get_user(interaction.user.id)
        if user and user['verified']:
            embed = discord.Embed(title="#พบข้อมูล Roblox Account อยู่แล้ว", color=discord.Color.blue())
            embed.add_field(name="Roblox:", value=user['roblox_username'], inline=True)
            embed.add_field(name="Roblox ID:", value=user['roblox_id'], inline=True)
            embed.add_field(name="สถานะ:", value=f"{VERIFIED_EMOJI} ยืนยันแล้ว", inline=False)
            await interaction.response.send_message(embed=embed, view=ReVerifyView(), ephemeral=True)
        else: await interaction.response.send_modal(VerifyModal())

class ReVerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label='เปลี่ยน Account', style=discord.ButtonStyle.primary, custom_id='change_account')
    async def change_account(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerifyModal())
    @discord.ui.button(label='อัพเดทยศ', style=discord.ButtonStyle.success, custom_id='update_rank')
    async def update_rank(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("กำลังอัพเดทยศ รอสักครู่...", ephemeral=True)
        user = get_user(interaction.user.id)
        if not user: return await interaction.edit_original_response(content="ไม่พบข้อมูลของคุณ")
        success = await update_member_status(interaction.user.id, user['roblox_id'], user['roblox_username'])
        if success:
            embed = discord.Embed(color=discord.Color.green())
            embed.description = f"{VERIFIED_EMOJI} **Role ของคุณเป็นปัจจุบันแล้ว**\n\n**ข้อมูลปัจจุบัน:**\nRoblox: `{user['roblox_username']}`\nRoblox ID: `{user['roblox_id']}`\nสถานะ: {VERIFIED_EMOJI} ยืนยันแล้ว"
            await interaction.edit_original_response(content=None, embed=embed)
        else: await interaction.edit_original_response(content="เกิดข้อผิดพลาด")

@bot.event
async def on_ready():
    init_db()
    bot.add_view(VerifyView())
    bot.add_view(ReVerifyView())
    print(f'Bot {bot.user} is Ready! (Port: {PORT})')

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_verify(ctx):
    await ctx.send(embed=discord.Embed(title="ระบบยืนยันตัวตน", description="กรุณากดปุ่มด้านล่างเพื่อเริ่มการยืนยันตัวตน", color=discord.Color.green()), view=VerifyView())

# ==========================================================
# 🚀 RUNNER
# ==========================================================
async def start():
    init_db()
    asyncio.create_task(bot.start(DISCORD_TOKEN))
    config_uvicorn = uvicorn.Config(app, host="0.0.0.0", port=PORT, log_level="info")
    server = uvicorn.Server(config_uvicorn)
    await server.serve()

if __name__ == "__main__":
    if DISCORD_TOKEN == "ใส่_TOKEN_ของคุณที่นี่" and not os.getenv("DISCORD_TOKEN"):
        print("!!! กรุณาใส่ Discord Token ในไฟล์ bot.py ก่อนรัน !!!")
    else:
        try: asyncio.run(start())
        except KeyboardInterrupt: pass
