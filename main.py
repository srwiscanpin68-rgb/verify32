import os, asyncio, sqlite3, requests, discord, uvicorn
from discord.ext import commands
from fastapi import FastAPI, Request
from contextlib import asynccontextmanager

# --- CONFIG ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
PORT = int(os.getenv("PORT", 8888))
ROBLOX_GROUP_ID = 35646818
VERIFIED_ROLE_ID = 1532801945981423847
RANK_ROLES = {"OR": 1532804608777523200, "OF_LOW": 1532804655375978646, "OF_HIGH": 1532806100611629076}
DB_PATH = "database.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS users (discord_id TEXT PRIMARY KEY, roblox_id TEXT, roblox_username TEXT, verified INTEGER DEFAULT 0, pending_roblox_username TEXT)")
    conn.commit(); conn.close()

def update_pending(discord_id, username):
    conn = sqlite3.connect(DB_PATH)
    # ลบเว้นวรรคและทำให้เป็นตัวพิมพ์เล็กก่อนบันทึก
    clean_name = str(username).strip().lower()
    conn.execute("INSERT OR REPLACE INTO users (discord_id, pending_roblox_username, verified) VALUES (?, ?, 0)", (str(discord_id), clean_name))
    conn.commit(); conn.close()
    print(f"[DEBUG] บันทึกชื่อลงฐานข้อมูลแล้ว: '{clean_name}'")

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
        rank_val, rank_name, nick_prefix = 0, "ทหารไทย", ""
        if group_info:
            rank_val = group_info["role"]["rank"]
            if 1 <= rank_val <= 7:
                nick_prefix = "| OR-1, PC | OR-2, PEC | OR-3, CPL | OR-4, SGT | OR-5 SSG | OR-6/OR-7, SFC | OR-8/OR-9, MSG"
                roles_to_add.append(guild.get_role(RANK_ROLES["OR"]))
            elif 8 <= rank_val <= 11:
                nick_prefix = "| OF-1A, LTP | OF-1B, 1LT | OF-2, CPT"
                roles_to_add.append(guild.get_role(RANK_ROLES["OF_LOW"]))
            elif 12 <= rank_val <= 18:
                nick_prefix = "| OF-3, MAJ | OF-4, LTC | OF-5, COL | OF-6, SRCOL | OF-7, PMG | OF-8, MG | OF-9, GEN"
                roles_to_add.append(guild.get_role(RANK_ROLES["OF_HIGH"]))
            nick = f"{nick_prefix} {roblox_username}"
            rank_name = group_info["role"]["name"]
        else: nick = f"Guest | {roblox_username}"
        await member.edit(roles=[r for r in roles_to_add if r], nick=nick[:32])
        return rank_val, member.display_name, rank_name
    except Exception as e:
        print(f"Error: {e}"); return None, None, None

class VerifyModal(discord.ui.Modal, title='ยืนยันตัวตน Roblox'):
    username = discord.ui.TextInput(label='ใส่ชื่อใน Roblox', placeholder='ตัวอย่าง: 8II7V3', required=True)
    async def on_submit(self, interaction: discord.Interaction):
        update_pending(interaction.user.id, self.username.value)
        await interaction.response.send_message(f"บันทึกชื่อ **{self.username.value}** แล้ว! กรุณากดปุ่มใน Roblox อีกครั้ง", ephemeral=True)

class VerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label='ยืนยันตัวตน', style=discord.ButtonStyle.success, custom_id='start_v')
    async def start_v(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerifyModal())

@bot.event
async def on_ready():
    bot.add_view(VerifyView())
    print(f'Bot {bot.user} Ready!')

@bot.command()
async def setup_verify(ctx):
    await ctx.send(embed=discord.Embed(title="ระบบยืนยันตัวตน", description="กดปุ่มเพื่อเริ่มยืนยันตัวตน", color=0x00ff00), view=VerifyView())

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
    rid, rname = data.get("robloxId"), str(data.get("robloxUsername", "")).strip()
    search_name = rname.lower()
    
    print(f"[DEBUG] กำลังค้นหาชื่อใน DB: '{search_name}'")
    
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    # ค้นหาแบบไม่สนเว้นวรรค
    row = conn.execute("SELECT discord_id FROM users WHERE LOWER(TRIM(pending_roblox_username)) = ?", (search_name,)).fetchone()
    conn.close()
    
    if not row:
        return {"ok": False, "message": f"ไม่พบชื่อ '{rname}' ในรายการรอ"}
    
    rank, d_name, r_name = await update_member_status(row["discord_id"], rid, rname)
    if rank is not None:
        return {"ok": True, "discord_username": d_name, "current_rank": r_name}
    return {"ok": False, "message": "บอทไม่มีสิทธิ์เปลี่ยนยศ"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
