import os
import asyncio
import sqlite3
import requests
import discord
from discord.ext import commands
from fastapi import FastAPI, Request
import uvicorn
from contextlib import asynccontextmanager

# ==========================================================
# ⚙️ CONFIGURATION (ใส่ Token ใน Railway Variables)
# ==========================================================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "ใส่_TOKEN_ของคุณที่นี่")
PORT = int(os.getenv("PORT", 8888))

ROBLOX_GROUP_ID = 35646818
VERIFIED_ROLE_ID = 1532801945981423847

RANK_ROLES = {
    "OR": 1532804608777523200,    # OR-1 ถึง OR-9
    "OF_LOW": 1532804655375978646, # OF-1A ถึง OF-2
    "OF_HIGH": 1532806100611629076 # OF-3 ถึง OF-9
}

VERIFIED_EMOJI = ":__~242:"
DB_PATH = "database.db"

# ==========================================================
# 🗄️ DATABASE
# ==========================================================
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

# ==========================================================
# 🤖 DISCORD BOT
# ==========================================================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

async def update_member_status(discord_id, roblox_id, roblox_username):
    if not bot.guilds: return None, None, None
    guild = bot.guilds[0]
    try:
        member = await guild.fetch_member(int(discord_id))
        resp = requests.get(f"https://groups.roblox.com/v1/users/{roblox_id}/groups/roles" ).json()
        group_info = next((g for g in resp["data"] if g["group"]["id"] == ROBLOX_GROUP_ID), None)
        
        roles_to_add = [guild.get_role(VERIFIED_ROLE_ID)]
        rank_val = 0
        nick_prefix = ""
        rank_name = "ทหารไทย"
        
        if group_info:
            rank_val = group_info["role"]["rank"]
            if 1 <= rank_val <= 7:
                nick_prefix = "| OR-1, PC | OR-2, PEC | OR-3, CPL | OR-4, SGT | OR-5 SSG | OR-6/OR-7, SFC | OR-8/OR-9, MSG"
                roles_to_add.append(guild.get_role(RANK_ROLES["OR"]))
                rank_name = "ทหารไทย (OR)"
            elif 8 <= rank_val <= 11:
                nick_prefix = "| OF-1A, LTP | OF-1B, 1LT | OF-2, CPT"
                roles_to_add.append(guild.get_role(RANK_ROLES["OF_LOW"]))
                rank_name = "ทหารไทย (OF-Low)"
            elif 12 <= rank_val <= 18:
                nick_prefix = "| OF-3, MAJ | OF-4, LTC | OF-5, COL | OF-6, SRCOL | OF-7, PMG | OF-8, MG | OF-9, GEN"
                roles_to_add.append(guild.get_role(RANK_ROLES["OF_HIGH"]))
                rank_name = "ทหารไทย (OF-High)"
            
            nick = f"{nick_prefix} {roblox_username}"
        else:
            nick = f"Guest | {roblox_username}"

        await member.edit(roles=[r for r in roles_to_add if r], nick=nick[:32])
        return rank_val, member.display_name, rank_name
    except Exception as e:
        print(f"Update Error: {e}")
        return None, None, None

# ... (UI Modal/View เหมือนเดิม) ...
class VerifyModal(discord.ui.Modal, title='ยืนยันตัวตน Roblox'):
    username = discord.ui.TextInput(label='ใส่ชื่อใน Roblox', placeholder='ตัวอย่าง: manpop79', required=True)
    async def on_submit(self, interaction: discord.Interaction):
        update_pending(interaction.user.id, self.username.value)
        embed = discord.Embed(color=discord.Color.gold(), description=f"กรุณาเข้าแมพ Roblox และพิมพ์ในช่องแชทว่า `ยืนยันตัวตน` เพื่อยืนยันบัญชี: **{self.username.value}**")
        await interaction.response.send_message(embed=embed, ephemeral=True)

class VerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label='ยืนยันตัวตน', style=discord.ButtonStyle.success, emoji='✅', custom_id='start_v')
    async def start_v(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerifyModal())

@bot.event
async def on_ready():
    bot.add_view(VerifyView())
    print(f'Bot {bot.user} Ready!')

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_verify(ctx):
    await ctx.send(embed=discord.Embed(title="ระบบยืนยันตัวตน", description="กดปุ่มด้านล่างเพื่อเริ่มการยืนยันตัวตน", color=discord.Color.green()), view=VerifyView())

# ==========================================================
# 🌐 FASTAPI & API (แบบไม่ต้องใช้ Key)
# ==========================================================
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
    rid = data.get("robloxId")
    rname = data.get("robloxUsername")

    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT discord_id FROM users WHERE pending_roblox_username = ?", (rname,)).fetchone()
    conn.close()

    if not row:
        return {"ok": False, "message": "ไม่พบชื่อ Roblox นี้ในรายการรอการยืนยัน (พิมพ์ !setup_verify ในดิสก่อน)"}

    did = row["discord_id"]
    rank_val, d_name, r_name = await update_member_status(did, rid, rname)

    if rank_val is not None:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE users SET roblox_id = ?, roblox_username = ?, verified = 1, pending_roblox_username = NULL WHERE discord_id = ?", (str(rid), rname, did))
        conn.commit(); conn.close()
        return {"ok": True, "discord_username": d_name, "current_rank": r_name}
    
    return {"ok": False, "message": "บอทไม่สามารถเปลี่ยนยศในดิสได้ (ตรวจสอบสิทธิ์บอท)"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
