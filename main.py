import os, asyncio, sqlite3, requests, discord, uvicorn
from discord.ext import commands
from discord import app_commands
from fastapi import FastAPI, Request
from contextlib import asynccontextmanager

# --- CONFIG ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
PORT = int(os.getenv("PORT", 8888))
ROBLOX_GROUP_ID = 226834839
ROBLOX_GROUP_URL = "https://www.roblox.com/groups/226834839"
ROBLOX_MAP_URL = "https://www.roblox.com/th/games/78189317414125/By"
VERIFIED_ROLE_ID = 1479443343367995579
RANK_ROLES = {"OR": 1479699133001629797, "OF_LOW": 1479699314078122094, "OF_HIGH": 1479699471603470432}
DB_PATH = "database.db"
VERIFIED_EMOJI = "✅"

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
    clean_name = str(username).strip().lower()
    conn.execute("INSERT OR REPLACE INTO users (discord_id, pending_roblox_username, verified) VALUES (?, ?, 0)", (str(discord_id), clean_name))
    conn.commit(); conn.close()

# --- BOT SETUP ---
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        print(f"Full System v4 slash commands synced for {self.user}")

bot = MyBot()

def get_roblox_id_by_name(username):
    try:
        resp = requests.post("https://users.roblox.com/v1/usernames/users", json={"usernames": [username], "excludeBannedUsers": True}).json()
        if resp.get("data") and len(resp["data"]) > 0:
            return resp["data"][0]["id"]
    except Exception as e:
        print(f"Error fetching Roblox ID: {e}")
    return None

def check_group_membership(roblox_id):
    try:
        resp = requests.get(f"https://groups.roblox.com/v1/users/{roblox_id}/groups/roles").json()
        for group in resp.get("data", []):
            if group["group"]["id"] == ROBLOX_GROUP_ID:
                return True, group["role"]["rank"], group["role"]["name"]
    except Exception as e:
        print(f"Error checking group membership: {e}")
    return False, 0, None

async def update_member_status(discord_id, roblox_id, roblox_username):
    if not bot.guilds: return None, None, None
    guild = bot.guilds[0]
    try:
        member = await guild.fetch_member(int(discord_id))
        is_in_group, rank_val, rank_name = check_group_membership(roblox_id)
        
        roles_to_add = [guild.get_role(VERIFIED_ROLE_ID)]
        nick_prefix = ""
        
        if is_in_group:
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
        else:
            nick = f"Guest | {roblox_username}"

        roles = [r for r in roles_to_add if r is not None]
        await member.edit(roles=roles, nick=nick[:32])
        return rank_val, member.display_name, rank_name
    except Exception as e:
        print(f"Update Error: {e}")
        return None, None, None

# --- UI COMPONENTS ---
class VerifyModal(discord.ui.Modal, title='ยืนยันตัวตน Roblox'):
    username = discord.ui.TextInput(label='ใส่ชื่อใน Roblox', placeholder='พิมพ์ชื่อของคุณที่นี่...', min_length=3, max_length=20, required=True)
    async def on_submit(self, interaction: discord.Interaction):
        input_name = self.username.value
        roblox_id = get_roblox_id_by_name(input_name)
        
        if not roblox_id:
            await interaction.response.send_message(f"❌ ไม่พบชื่อ Roblox: **{input_name}** กรุณาตรวจสอบการสะกดชื่ออีกครั้ง", ephemeral=True)
            return

        is_in_group, _, _ = check_group_membership(roblox_id)
        if not is_in_group:
            embed_error = discord.Embed(title="❌ กรุณาเข้ากลุ่ม Roblox", description=f"คุณยังไม่ได้เข้ากลุ่มของเรา! บอทได้ส่งลิงก์กลุ่มไปให้คุณทาง DM แล้วครับ\n\n**ลิงก์กลุ่ม:** [คลิกที่นี่เพื่อเข้ากลุ่ม]({ROBLOX_GROUP_URL})", color=0xff0000)
            await interaction.response.send_message(embed=embed_error, ephemeral=True)
            try: await interaction.user.send(f"สวัสดีครับ! กรุณาเข้ากลุ่ม Roblox ของเราก่อนยืนยันตัวตนนะครับ: {ROBLOX_GROUP_URL}")
            except: pass
            return

        update_pending(interaction.user.id, input_name)
        embed_success = discord.Embed(title="กรุณาเข้าแมพเพื่อยืนยันตัวตน", color=0x00ff00)
        embed_success.add_field(name="Username", value=f"**{input_name}**", inline=False)
        embed_success.add_field(name="Map", value=f"[คลิกที่นี่เพื่อเข้าเกม]({ROBLOX_MAP_URL})", inline=False)
        embed_success.set_footer(text="❤️❤️❤️")
        await interaction.response.send_message(embed=embed_success, ephemeral=True)

class ReVerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    
    @discord.ui.button(label='อัพเดทยศ', style=discord.ButtonStyle.success, custom_id='update_rank')
    async def update_rank(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("กำลังอัพเดทยศรอสักครู่...", ephemeral=True)
        u = get_user(interaction.user.id)
        if u and u['roblox_id']:
            rank_val, d_name, r_name = await update_member_status(interaction.user.id, u['roblox_id'], u['roblox_username'])
            if rank_val is not None:
                embed = discord.Embed(title=f"{VERIFIED_EMOJI} อัพเดทยศสำเร็จ", color=0x00ff00)
                embed.description = f"ข้อมูลของคุณเป็นปัจจุบันแล้ว\n\n**Roblox:** {u['roblox_username']}\n**ยศปัจจุบัน:** {r_name}"
                await interaction.edit_original_response(content=None, embed=embed)
            else:
                await interaction.edit_original_response(content="❌ เกิดข้อผิดพลาดในการอัพเดทยศ")
        else:
            await interaction.edit_original_response(content="❌ ไม่พบข้อมูลการยืนยันของคุณ")

    @discord.ui.button(label='เปลี่ยน Account', style=discord.ButtonStyle.primary, custom_id='change_acc')
    async def change_acc(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerifyModal())

class VerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label='ยืนยันตัวตน', style=discord.ButtonStyle.success, emoji='✅', custom_id='persistent_verify')
    async def start_v(self, interaction: discord.Interaction, button: discord.ui.Button):
        u = get_user(interaction.user.id)
        if u and u['verified']:
            embed = discord.Embed(title="#พบข้อมูล Roblox Account อยู่แล้ว", color=0x3498db)
            embed.add_field(name="ข้อมูลปัจจุบัน:", value=f"**Roblox:** {u['roblox_username']}\n**Roblox ID:** {u['roblox_id']}\n**สถานะ:** ยืนยันแล้ว {VERIFIED_EMOJI}", inline=False)
            embed.description = "ต้องการเปลี่ยน Account? กดปุ่มด้านล่าง"
            await interaction.response.send_message(embed=embed, view=ReVerifyView(), ephemeral=True)
        else:
            await interaction.response.send_modal(VerifyModal())

# --- CLEAN SLASH COMMANDS ---
@bot.tree.command(name="ยืนยันตัวตน", description="ตั้งค่าระบบยืนยันตัวตน (Administrator Only)")
@app_commands.default_permissions(administrator=True)
async def setup_verify(interaction: discord.Interaction):
    embed = discord.Embed(title="ระบบยืนยันตัวตนทหารไทย", description="กรุณากดปุ่มด้านล่างเพื่อเริ่มการยืนยันตัวตนกับ Roblox", color=0x2b2d31)
    await interaction.channel.send(embed=embed, view=VerifyView())
    await interaction.response.send_message("✅ ตั้งค่าระบบยืนยันตัวตนเรียบร้อยแล้ว", ephemeral=True)

@bot.tree.command(name="ล้างข้อมูลทั้งหมด", description="ลบข้อมูลการยืนยันตัวตนทุกคน")
@app_commands.default_permissions(administrator=True)
async def reset_db(interaction: discord.Interaction):
    conn = sqlite3.connect(DB_PATH); conn.execute("DELETE FROM users"); conn.commit(); conn.close()
    await interaction.response.send_message("⚠️ [Admin] ล้างฐานข้อมูลทั้งหมดเรียบร้อยแล้ว ทุกคนต้องยืนยันใหม่!", ephemeral=True)

# --- FASTAPI SETUP ---
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
    
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT discord_id FROM users WHERE LOWER(TRIM(pending_roblox_username)) = ?", (search_name,)).fetchone()
    conn.close()
    
    if not row:
        return {"ok": False, "message": f"ไม่พบชื่อ '{rname}' ในรายการรอ (กรุณากดปุ่มยืนยันใน Discord ก่อน)"}
    
    rank, d_name, r_name = await update_member_status(row["discord_id"], rid, rname)
    if rank is not None:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE users SET roblox_id = ?, roblox_username = ?, verified = 1, pending_roblox_username = NULL WHERE discord_id = ?", (str(rid), rname, row["discord_id"]))
        conn.commit(); conn.close()
        return {"ok": True, "discord_username": d_name, "current_rank": r_name}
    
    return {"ok": False, "message": "บอทไม่มีสิทธิ์เปลี่ยนยศ"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
