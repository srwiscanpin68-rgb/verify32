import os
import asyncio
import sqlite3
import requests
import discord
from discord.ext import commands
from fastapi import FastAPI, Request
import uvicorn
from contextlib import asynccontextmanager

# --- ตั้งค่าตรงนี้ ---
TOKEN = os.getenv("DISCORD_TOKEN", "ใส่_TOKEN_ของคุณที่นี่")
GROUP_ID = 35646818
ROLE_VERIFIED = 1532801945981423847
ROLE_OR = 1532804608777523200
ROLE_OF_LOW = 1532804655375978646
ROLE_OF_HIGH = 1532806100611629076

RANK_NAMES = {
    1: "PC", 2: "PEC", 3: "CPL", 4: "SGT", 5: "SSG", 6: "SFC", 7: "MSG",
    8: "CADET", 9: "LTP", 10: "1LT", 11: "CPT", 12: "MAJ", 13: "LTC",
    14: "COL", 15: "SRCOL", 16: "PMG", 17: "MG", 18: "GEN"
}

# --- ระบบฐานข้อมูล ---
def init_db():
    with sqlite3.connect("data.db") as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (discord_id TEXT PRIMARY KEY, roblox_id TEXT, roblox_username TEXT, verified INTEGER, pending TEXT)")

def get_user(did):
    with sqlite3.connect("data.db") as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM users WHERE discord_id = ?", (str(did),)).fetchone()

# --- บอท Discord ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    bot.add_view(VerifyView())
    print(f"บอท {bot.user} ออนไลน์แล้ว!")

class VerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="ยืนยันตัวตน", style=discord.ButtonStyle.success, custom_id="v_btn")
    async def v_btn(self, it, btn):
        u = get_user(it.user.id)
        if u and u["verified"]:
            await it.response.send_message(f"# พบข้อมูลอยู่แล้ว: {u['roblox_username']}", ephemeral=True)
        else:
            class M(discord.ui.Modal, title="ใส่ชื่อ Roblox"):
                name = discord.ui.TextInput(label="ชื่อ Roblox")
                async def on_submit(self, it2):
                    with sqlite3.connect("data.db") as conn:
                        conn.execute("INSERT OR REPLACE INTO users (discord_id, pending, verified) VALUES (?, ?, 0)", (str(it2.user.id), self.name.value))
                    await it2.response.send_message(f"กรุณาเข้าแมพและพิมพ์ `ยืนยันตัวตน`", ephemeral=True)
            await it.response.send_modal(M())

@bot.command()
async def setup_verify(ctx):
    await ctx.send("กดปุ่มเพื่อยืนยันตัวตน", view=VerifyView())

# --- API สำหรับ Roblox ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    asyncio.create_task(bot.start(TOKEN))
    yield

app = FastAPI(lifespan=lifespan)

@app.post("/verify")
async def verify(req: Request):
    data = await req.json()
    rid, rname = data.get("robloxId"), data.get("robloxUsername")
    with sqlite3.connect("data.db") as conn:
        conn.row_factory = sqlite3.Row
        user = conn.execute("SELECT discord_id FROM users WHERE pending = ?", (rname,)).fetchone()
        if user:
            did = user["discord_id"]
            conn.execute("UPDATE users SET roblox_id=?, roblox_username=?, verified=1, pending=NULL WHERE discord_id=?", (rid, rname, did))
            # อัปเดทยศ (แบบย่อ)
            asyncio.create_task(update_roles(did, rid, rname))
            return {"success": True}
    return {"success": False}

async def update_roles(did, rid, rname):
    try:
        guild = bot.guilds[0]
        mem = await guild.fetch_member(int(did))
        res = requests.get(f"https://groups.roblox.com/v1/users/{rid}/groups/roles" ).json()
        g = next((x for x in res["data"] if x["group"]["id"] == GROUP_ID), None)
        r_ids = [ROLE_VERIFIED]
        nick = f"Guest | {rname}"
        if g:
            rv = g["role"]["rank"]
            nick = f"{RANK_NAMES.get(rv, 'Guest')} | {rname}"
            if 1 <= rv <= 7: r_ids.append(ROLE_OR)
            elif 8 <= rv <= 11: r_ids.append(ROLE_OF_LOW)
            elif 12 <= rv <= 18: r_ids.append(ROLE_OF_HIGH)
        await mem.edit(roles=[guild.get_role(x) for x in r_ids if guild.get_role(x)], nick=nick[:32])
    except: pass

@app.get("/")
async def home(): return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
