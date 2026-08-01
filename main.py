import os
import asyncio
import sqlite3
import requests
import discord
from discord.ext import commands
from fastapi import FastAPI, Request, HTTPException, Header
import uvicorn
from contextlib import asynccontextmanager

# ==========================================================
# ⚙️ CONFIGURATION (ใส่ Token ใน Railway Variables)
# ==========================================================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "ใส่_TOKEN_ของคุณที่นี่")
PORT = int(os.getenv("PORT", 8888))

ROBLOX_GROUP_ID = 35646818
VERIFIED_ROLE_ID = 1532801945981423847

# Rank Mapping ตามความต้องการ
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
    conn.commit()
    conn.close()

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

# ==========================================================
# 🤖 DISCORD BOT
# ==========================================================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

async def update_member_status(discord_id, roblox_id, roblox_username):
    if not bot.guilds: return None
    guild = bot.guilds[0]
    try:
        member = await guild.fetch_member(int(discord_id))
        resp = requests.get(f"https://groups.roblox.com/v1/users/{roblox_id}/groups/roles" ).json()
        group_info = next((g for g in resp['data'] if g['group']['id'] == ROBLOX_GROUP_ID), None)
        
        roles_to_add = [guild.get_role(VERIFIED_ROLE_ID)]
        rank_val = 0
        nick_prefix = ""
        
        if group_info:
            rank_val = group_info['role']['rank']
            # กำหนด Prefix และ Role ตาม Rank ID
            if 1 <= rank_val <= 7: # OR-1 to OR-9
                nick_prefix = "| OR-1, PC | OR-2, PEC | OR-3, CPL | OR-4, SGT | OR-5 SSG | OR-6/OR-7, SFC | OR-8/OR-9, MSG"
                roles_to_add.append(guild.get_role(RANK_ROLES["OR"]))
            elif 8 <= rank_val <= 11: # OF-1A to OF-2
                nick_prefix = "| OF-1A, LTP | OF-1B, 1LT | OF-2, CPT"
                roles_to_add.append(guild.get_role(RANK_ROLES["OF_LOW"]))
            elif 12 <= rank_val <= 18: # OF-3 to OF-9
                nick_prefix = "| OF-3, MAJ | OF-4, LTC | OF-5, COL | OF-6, SRCOL | OF-7, PMG | OF-8, MG | OF-9, GEN"
                roles_to_add.append(guild.get_role(RANK_ROLES["OF_HIGH"]))
            
            nick = f"{nick_prefix} {roblox_username}"
        else:
            nick = f"Guest | {roblox_username}"

        await member.edit(roles=[r for r in roles_to_add if r], nick=nick[:32])
        return rank_val, member.display_name
    except Exception as e:
        print(f"Update Error: {e}")
        return None, None

class VerifyModal(discord.ui.Modal, title='ยืนยันตัวตน Roblox'):
    username = discord.ui.TextInput(label='ใส่ชื่อใน Roblox', placeholder='ตัวอย่าง: manpop79', required=True)
    async def on_submit(self, interaction: discord.Interaction):
        update_pending(interaction.user.id, self.username.value)
        embed = discord.Embed(color=discord.Color.gold())
        embed.add_field(name="ชื่อ:", value=self.username.value, inline=True)
        embed.add_field(name="เข้าแมพ:", value="รอแปปเย่ดแม่", inline=True)
        embed.description = "กรุณาเข้าแมพ Roblox และพิมพ์ในช่องแชทว่า `ยืนยันตัวตน`"
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ReVerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    
    @discord.ui.button(label='เปลี่ยน Account', style=discord.ButtonStyle.primary, custom_id='change_acc')
    async def change_acc(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerifyModal())

    @discord.ui.button(label='อัพเดทยศ', style=discord.ButtonStyle.success, custom_id='upd_rank')
    async def upd_rank(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("กำลังอัพเดทยศรอสักครู่...", ephemeral=True)
        u = get_user(interaction.user.id)
        if u:
            rank_val, discord_display_name = await update_member_status(interaction.user.id, u['roblox_id'], u['roblox_username'])
            embed = discord.Embed(color=discord.Color.green())
            embed.description = f"{VERIFIED_EMOJI} Role ของคุณเป็นปัจจุบันแล้ว\n\n**ข้อมูลปัจจุบัน:**\nRoblox: {u['roblox_username']}\nRoblox ID: {u['roblox_id']}\nRank: {rank_val}\nสถานะ: {VERIFIED_EMOJI} ยืนยันแล้ว"
            await interaction.edit_original_response(content=None, embed=embed)
        else:
            await interaction.edit_original_response(content="ไม่พบข้อมูลของคุณ")

class VerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    
    @discord.ui.button(label='ยืนยันตัวตน', style=discord.ButtonStyle.success, emoji='✅', custom_id='start_v')
    async def start_v(self, interaction: discord.Interaction, button: discord.ui.Button):
        u = get_user(interaction.user.id)
        if u and u['verified']:
            embed = discord.Embed(title="#พบข้อมูล Roblox Account อยู่แล้ว", color=discord.Color.blue())
            embed.description = f"Roblox: {u['roblox_username']}\nRoblox ID: {u['roblox_id']}\nสถานะ {VERIFIED_EMOJI} ยืนยันแล้ว\n\nต้องการเปลี่ยน Account? กดปุ่มด้านล่าง"
            await interaction.response.send_message(embed=embed, view=ReVerifyView(), ephemeral=True)
        else:
            await interaction.response.send_modal(VerifyModal())

@bot.event
async def on_ready():
    bot.add_view(VerifyView()); bot.add_view(ReVerifyView())
    print(f'Bot {bot.user} Ready!')

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_verify(ctx):
    await ctx.send(embed=discord.Embed(title="ระบบยืนยันตัวตน", description="กรุณากดปุ่มด้านล่างเพื่อเริ่มการยืนยันตัวตนกับ Roblox", color=discord.Color.green()), view=VerifyView())

# ==========================================================
# 🌐 FASTAPI & API
# ==========================================================

# Add your API key for Roblox verification
ROBLOX_VERIFICATION_API_KEY = os.getenv("ROBLOX_VERIFICATION_API_KEY")

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
async def verify_endpoint(request: Request, x_roblox_verification_key: str = Header(None)):
    if not ROBLOX_VERIFICATION_API_KEY or x_roblox_verification_key != ROBLOX_VERIFICATION_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid API Key")

    data = await request.json()
    roblox_id = data.get("robloxId")
    roblox_username = data.get("robloxUsername")
    selected_division = data.get("selected_division") # New field from Roblox

    if not roblox_id or not roblox_username or not selected_division:
        raise HTTPException(status_code=400, detail="Missing robloxId, robloxUsername, or selected_division")

    # Find the Discord user associated with this Roblox username (pending verification)
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    user_row = conn.execute("SELECT discord_id FROM users WHERE pending_roblox_username = ?", (roblox_username,)).fetchone()
    conn.close()

    if not user_row:
        return {"ok": False, "message": "ไม่พบผู้ใช้ Discord ที่รอการยืนยันด้วยชื่อ Roblox นี้"}

    discord_id = user_row['discord_id']

    # Verify the user and update their status in Discord
    rank_val, discord_display_name = await update_member_status(discord_id, roblox_id, roblox_username)

    if rank_val is None:
        return {"ok": False, "message": "ไม่สามารถอัปเดตสถานะ Discord ได้"}

    # Check if the selected division matches the assigned roles/rank
    # This is a simplified check. You might need more complex logic here
    # based on your specific rank mapping and division requirements.
    is_verified_for_division = False
    if selected_division == "army" and 1 <= rank_val <= 7: # OR-1 to OR-9
        is_verified_for_division = True
    elif selected_division == "police" and 8 <= rank_val <= 11: # OF-1A to OF-2
        is_verified_for_division = True
    elif selected_division == "air_force" and 12 <= rank_val <= 18: # OF-3 to OF-9
        is_verified_for_division = True

    if not is_verified_for_division:
        return {"ok": False, "message": "คุณไม่มีสิทธิ์ในหน่วยงานที่เลือก หรือยศไม่ตรงกัน"}

    # If all checks pass, update the database as verified
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE users SET roblox_id = ?, roblox_username = ?, verified = 1, pending_roblox_username = NULL WHERE discord_id = ?", (str(roblox_id), roblox_username, discord_id))
    conn.commit(); conn.close()

    # Determine current rank display name for Roblox
    current_rank_display = ""
    if 1 <= rank_val <= 7:
        current_rank_display = "ทหารไทย"
    elif 8 <= rank_val <= 11:
        current_rank_display = "ตำรวจไทย"
    elif 12 <= rank_val <= 18:
        current_rank_display = "ทหารอากาศไทย"
    else:
        current_rank_display = "ไม่ทราบยศ"

    return {"ok": True, "discord_username": discord_display_name, "current_rank": current_rank_display}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
    
