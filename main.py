# เเครดิต: By.ivzex, By.patxez, DEV.manpop79, DEV.Fugus1234
import os, asyncio, json, re, sqlite3, requests, discord, uvicorn, io, datetime
from discord.ext import commands
from discord import app_commands
from fastapi import FastAPI, Request
from contextlib import asynccontextmanager

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
PORT = int(os.getenv("PORT", 8888))
DB_PATH, SETTINGS_PATH = "database.db", "settings.json"

DEFAULT_SETTINGS = {
    "roblox_group_id": 226834839, "roblox_group_url": "https://www.roblox.com/groups/226834839",
    "roblox_map_url": "https://www.roblox.com/th/games/78189317414125/By",
    "verified_role_id": 1479443343367995579, "developer_role_id": 1479469155399766129,
    "ticket_role_id": 1508479215908028544, "transcript_channel_id": None,
    "role_ids": {"or": 1479699133001629797, "of_low": 1479699314078122094, "of_high": 1479699471603470432, "guest": None},
    "rank_prefixes": {"or-1": "OR-1, PC", "or-2": "OR-2, PEC", "or-3": "OR-3, CPL", "or-4": "OR-4, SGT", "or-5": "OR-5, SSG", "or-6": "OR-6/OR-7, SFC", "or-7": "OR-6/OR-7, SFC", "or-8": "OR-8/OR-9, MSG", "or-9": "OR-8/OR-9, MSG", "of-1a": "OF-1A, LTP", "of-1b": "OF-1B, 1LT", "of-2": "OF-2, CPT", "of-3": "OF-3, MAJ", "of-4": "OF-4, LTC", "of-5": "OF-5, COL", "of-6": "OF-6, SRCOL", "of-7": "OF-7, PMG", "of-8": "OF-8, MG", "of-9": "OF-9, GEN"}
}

def load_settings():
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    try:
        if os.path.exists(SETTINGS_PATH):
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    for k, v in saved.items():
                        if k in ["role_ids", "rank_prefixes"] and isinstance(v, dict): s[k].update(v)
                        else: s[k] = v
    except: pass
    return s

def save_settings(s):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f: json.dump(s, f, ensure_ascii=False, indent=2)

def parse_id(v):
    if v is None: return None
    m = re.search(r"\d+", str(v))
    return int(m.group()) if m else None

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS users (discord_id TEXT PRIMARY KEY, roblox_id TEXT, roblox_username TEXT, verified INTEGER DEFAULT 0, pending_roblox_username TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS tickets (channel_id TEXT PRIMARY KEY, user_id TEXT, category TEXT, status TEXT DEFAULT 'open')")
    conn.commit(); conn.close()

def get_user(did):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM users WHERE discord_id = ?", (str(did),)).fetchone()
    conn.close(); return row

def update_pending(did, u):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO users (discord_id, pending_roblox_username, verified) VALUES (?, ?, 0) ON CONFLICT(discord_id) DO UPDATE SET pending_roblox_username = excluded.pending_roblox_username, verified = 0", (str(did), str(u).strip().lower()))
    conn.commit(); conn.close()

async def generate_transcript(ch, u, cb, cat):
    msgs = []
    async for m in ch.history(limit=None, oldest_first=True): msgs.append(m)
    h = f"<html><head><meta charset='utf-8'><style>body{{background:#36393f;color:#dcddde;font-family:sans-serif;padding:20px;}}.info{{background:#2f3136;padding:15px;border-radius:8px;margin-bottom:20px;border-left:5px solid #7289da;}}.msg{{display:flex;margin-bottom:15px;}}.av{{width:40px;height:40px;border-radius:50%;margin-right:15px;}}.auth{{font-weight:bold;color:#fff;}}.time{{font-size:0.75rem;color:#72767d;margin-left:10px;}}.txt{{margin-top:5px;white-space:pre-wrap;}}.att{{margin-top:10px;max-width:400px;border-radius:4px;}}</style></head><body><div class='info'><h2>Ticket Transcript</h2><p>Category: {cat}</p><p>Opened by: {u}</p><p>Closed by: {cb}</p><p>Date: {datetime.datetime.now()}</p></div>"
    for m in msgs:
        if m.author.bot and not m.embeds: continue
        h += f"<div class='msg'><img class='av' src='{m.author.display_avatar.url}'><div><span class='auth'>{m.author.display_name}</span><span class='time'>{m.created_at.strftime('%Y-%m-%d %H:%M')}</span><div class='txt'>{m.clean_content}</div>"
        for a in m.attachments:
            if any(a.filename.lower().endswith(e) for e in ['.png','.jpg','.jpeg','.gif','.webp']): h += f"<img class='att' src='{a.url}'>"
            else: h += f"<div class='txt'><a href='{a.url}' style='color:#00aff4;'>File: {a.filename}</a></div>"
        h += "</div></div>"
    return h + "</body></html>"

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default(); intents.members = True; intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
    async def setup_hook(self):
        self.add_view(VerifyView()); self.add_view(ReVerifyView()); self.add_view(TicketPanelView())
        await self.tree.sync()
bot = MyBot()

def get_roblox_id_by_name(u):
    try:
        r = requests.post("https://users.roblox.com/v1/usernames/users", json={"usernames":[u],"excludeBannedUsers":True}, timeout=15)
        d = r.json()
        if d.get("data"): return d["data"][0]["id"]
    except: return None

def check_group_membership(rid):
    s = load_settings()
    try:
        r = requests.get(f"https://groups.roblox.com/v1/users/{rid}/groups/roles", timeout=15)
        d = r.json()
        for g in d.get("data", []):
            if g["group"]["id"] == int(s["roblox_group_id"]): return True, g["role"]["rank"], g["role"]["name"]
    except: pass
    return False, 0, None

def get_prefix_for_rank(rv, rn, s):
    px = s.get("rank_prefixes", {})
    n = str(rn or "").strip().lower()
    for k, p in px.items():
        if str(k).strip().lower() in n: return str(p).strip()
    f = {1:"OR-1, PC",2:"OR-2, PEC",3:"OR-3, CPL",4:"OR-4, SGT",5:"OR-5, SSG",6:"OR-6/OR-7, SFC",7:"OR-6/OR-7, SFC",8:"OF-1A, LTP",9:"OF-1B, 1LT",10:"OF-2, CPT",11:"OF-2, CPT",12:"OF-3, MAJ",13:"OF-4, LTC",14:"OF-5, COL",15:"OF-6, SRCOL",16:"OF-7, PMG",17:"OF-8, MG",18:"OF-9, GEN"}
    return f.get(int(rv or 0), "")

async def update_member_status(did, rid, rn, gid=None):
    s = load_settings(); g = bot.get_guild(int(gid)) if gid else (bot.guilds[0] if bot.guilds else None)
    if not g: return None, None, None
    try:
        m = await g.fetch_member(int(did)); in_g, rv, rname = check_group_membership(rid); is_d = int(rid) in [5711452462]
        manage = {parse_id(s.get("verified_role_id")), parse_id(s.get("developer_role_id")), parse_id(s.get("ticket_role_id")), *[parse_id(x) for x in s["role_ids"].values()]}
        manage.discard(None)
        to_a = [r for r in m.roles if r.id not in manage and r != g.default_role]
        vr = g.get_role(parse_id(s.get("verified_role_id")))
        if vr: to_a.append(vr)
        if is_d:
            dr = g.get_role(parse_id(s.get("developer_role_id")))
            if dr: to_a.append(dr)
            nk, dp = f"Dev | {rn}", "Developer"
        elif in_g:
            if 1 <= rv <= 7: rr = g.get_role(parse_id(s["role_ids"].get("or")))
            elif 8 <= rv <= 11: rr = g.get_role(parse_id(s["role_ids"].get("of_low")))
            elif 12 <= rv <= 18: rr = g.get_role(parse_id(s["role_ids"].get("of_high")))
            else: rr = None
            if rr: to_a.append(rr)
            px = get_prefix_for_rank(rv, rname, s)
            nk, dp = (f"{px} | {rn}" if px else rn), (rname or "Unknown")
        else:
            gr = g.get_role(parse_id(s["role_ids"].get("guest")))
            if gr: to_a.append(gr)
            nk, dp = f"Guest | {rn}", "Guest"
        await m.edit(roles=list(set(to_a)), nick=nk[:32])
        return rv if not is_d else 999, m.display_name, dp
    except: return None, None, None

class VerifyModal(discord.ui.Modal, title="Roblox Verification"):
    u = discord.ui.TextInput(label="Enter Roblox Username", min_length=3, max_length=20)
    async def on_submit(self, it: discord.Interaction):
        n = self.u.value; rid = get_roblox_id_by_name(n); s = load_settings()
        if not rid: return await it.response.send_message(f"❌ Username not found: {n}", ephemeral=True)
        in_g, _, _ = check_group_membership(rid); is_d = int(rid) in [5711452462]
        if not in_g and not is_d: return await it.response.send_message(f"❌ Please join our group first: {s['roblox_group_url']}", ephemeral=True)
        update_pending(it.user.id, n); em = discord.Embed(title="Verification", description=f"Username: **{n}**\n[Click here to join Map for auto-verify]({s['roblox_map_url']})", color=0x00FF00)
        await it.response.send_message(embed=em, ephemeral=True)

class ReVerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Update Rank", style=discord.ButtonStyle.success, custom_id="up_rank")
    async def up(self, it: discord.Interaction, b: discord.ui.Button):
        u = get_user(it.user.id)
        if not u or not u["verified"]: return await it.response.send_message("❌ Please verify first", ephemeral=True)
        await it.response.defer(ephemeral=True); r, _, rn = await update_member_status(it.user.id, u["roblox_id"], u["roblox_username"], it.guild_id)
        await it.followup.send(f"✅ Rank updated: **{rn}**" if r else "❌ Failed", ephemeral=True)
    @discord.ui.button(label="Verify", style=discord.ButtonStyle.primary, custom_id="st_v")
    async def st(self, it: discord.Interaction, b: discord.ui.Button): await it.response.send_modal(VerifyModal())

class VerifyView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, custom_id="v_main")
    async def v(self, it: discord.Interaction, b: discord.ui.Button): await it.response.send_modal(VerifyModal())

class CustomizeAllModal(discord.ui.Modal, title="Customize System"):
    gid = discord.ui.TextInput(label="Roblox Group ID"); vrole = discord.ui.TextInput(label="Verified Role ID")
    gurl = discord.ui.TextInput(label="Group URL"); murl = discord.ui.TextInput(label="Map URL")
    pxs = discord.ui.TextInput(label="Rank Prefixes (OF-3=MAJ; OF-4=LTC)", style=discord.TextStyle.paragraph, required=False)
    async def on_submit(self, it: discord.Interaction):
        s = load_settings()
        try:
            s["roblox_group_id"] = int(self.gid.value); s["verified_role_id"] = int(self.vrole.value)
            s["roblox_group_url"] = self.gurl.value; s["roblox_map_url"] = self.murl.value
            if self.pxs.value.strip():
                for i in self.pxs.value.split(";"):
                    if "=" in i: c, t = i.split("=", 1); s["rank_prefixes"][c.strip().lower()] = f"{c.strip()}, {t.strip()}"
            save_settings(s); await it.response.send_message("✅ Settings saved", ephemeral=True)
        except: await it.response.send_message("❌ ID must be numeric", ephemeral=True)

class TicketSelect(discord.ui.Select):
    def __init__(self):
        opts = [
            discord.SelectOption(label="Report Cheater", emoji="❗️", value="Report Cheater"),
            discord.SelectOption(label="Claim Reward", emoji="🪄", value="Claim Reward"),
            discord.SelectOption(label="General Contact", emoji="💭", value="General Contact")
        ]
        super().__init__(placeholder="Select a category to contact staff", options=opts, custom_id="t_sel")
    async def callback(self, it: discord.Interaction):
        await it.response.defer(ephemeral=True)
        s = load_settings(); tid = parse_id(s.get("ticket_role_id", 1508479215908028544))
        name = f"ticket-{self.values[0]}-{it.user.name}".lower(); g = it.guild
        ov = {g.default_role: discord.PermissionOverwrite(view_channel=False), it.user: discord.PermissionOverwrite(view_channel=True, send_messages=True), g.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)}
        if tid:
            r = g.get_role(tid)
            if r: ov[r] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        ch = await g.create_text_channel(name=name, overwrites=ov)
        conn = sqlite3.connect(DB_PATH); conn.execute("INSERT INTO tickets (channel_id, user_id, category) VALUES (?, ?, ?)", (str(ch.id), str(it.user.id), self.values[0])); conn.commit(); conn.close()
        tag = f"<@&{tid}>" if tid else "@here"
        em = discord.Embed(title=f"🎫 Ticket: {self.values[0]}", description=f"Hello {it.user.mention}, please provide details to the staff.\nType `/ปิดช่อง` to close and save transcript.", color=0x3498DB)
        await ch.send(content=f"{tag} {it.user.mention}", embed=em)
        await it.followup.send(f"✅ Ticket opened at {ch.mention}", ephemeral=True)

class TicketPanelView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None); self.add_item(TicketSelect())

@bot.tree.command(name="ยืนยันตัวตน")
@app_commands.default_permissions(administrator=True)
async def setup_v(it: discord.Interaction):
    await it.channel.send(embed=discord.Embed(title="Roblox Verification", description="Click the button below to verify", color=0x2B2D31), view=VerifyView())
    await it.response.send_message("✅ Setup completed", ephemeral=True)

@bot.tree.command(name="ตั้งค่าทิกเก็ต")
@app_commands.default_permissions(administrator=True)
async def setup_t(it: discord.Interaction):
    await it.response.send_message("Sending Ticket panel...", ephemeral=True)
    await it.channel.send(embed=discord.Embed(title="📬 Staff Contact System", description="Select a category from the dropdown menu below", color=0x2B2D31), view=TicketPanelView())
    await it.edit_original_response(content="✅ Ticket panel sent")

@bot.tree.command(name="ปิดช่อง")
@app_commands.default_permissions(administrator=True)
async def close_t(it: discord.Interaction):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row; row = conn.execute("SELECT * FROM tickets WHERE channel_id = ?", (str(it.channel.id),)).fetchone()
    if not row: return await it.response.send_message("❌ This is not a ticket channel", ephemeral=True)
    await it.response.send_message("🔒 Saving transcript and deleting channel...")
    s = load_settings(); u = it.guild.get_member(int(row["user_id"]))
    html = await generate_transcript(it.channel, u or row["user_id"], it.user, row["category"])
    file = discord.File(io.BytesIO(html.encode()), filename=f"transcript-{it.channel.name}.html")
    ts_id = parse_id(s.get("transcript_channel_id"))
    if ts_id:
        ts_ch = it.guild.get_channel(ts_id)
        if ts_ch:
            em = discord.Embed(title="📄 Ticket Transcript", color=0x2B2D31, timestamp=datetime.datetime.now())
            em.add_field(name="Category", value=row["category"]); em.add_field(name="Opened by", value=f"<@{row['user_id']}>"); em.add_field(name="Closed by", value=it.user.mention)
            await ts_ch.send(embed=em, file=file)
    conn.execute("UPDATE tickets SET status = 'closed' WHERE channel_id = ?", (str(it.channel.id),)); conn.commit(); conn.close()
    await asyncio.sleep(3)
    await it.channel.delete()

@bot.tree.command(name="ตั้งช่องประวัติ")
@app_commands.default_permissions(administrator=True)
async def set_ts(it: discord.Interaction, ช่อง: discord.TextChannel):
    s = load_settings(); s["transcript_channel_id"] = ช่อง.id; save_settings(s)
    await it.response.send_message(f"✅ Transcript channel set to {ช่อง.mention}", ephemeral=True)

@bot.tree.command(name="ล้างข้อมูล")
@app_commands.default_permissions(administrator=True)
async def reset_db(it: discord.Interaction):
    conn = sqlite3.connect(DB_PATH); conn.execute("DELETE FROM users"); conn.commit(); conn.close()
    await it.response.send_message("⚠️ Database cleared", ephemeral=True)

@bot.tree.command(name="ใส่โรล")
@app_commands.default_permissions(administrator=True)
@app_commands.choices(ประเภท=[app_commands.Choice(name="Verified", value="verified"), app_commands.Choice(name="Developer", value="developer"), app_commands.Choice(name="Ticket", value="ticket"), app_commands.Choice(name="OR", value="or"), app_commands.Choice(name="OF Low", value="of_low"), app_commands.Choice(name="OF High", value="of_high"), app_commands.Choice(name="Guest", value="guest")])
async def set_r(it: discord.Interaction, ประเภท: app_commands.Choice[str], โรล: discord.Role):
    s = load_settings(); t = ประเภท.value
    if t in ["verified", "developer", "ticket"]: s[f"{t}_role_id"] = โรล.id
    else: s["role_ids"][t] = โรล.id
    save_settings(s); await it.response.send_message(f"✅ Role {โรล.name} set for {ประเภท.name}", ephemeral=True)

@bot.tree.command(name="ดูการตั้งค่า")
@app_commands.default_permissions(administrator=True)
async def show_s(it: discord.Interaction):
    s = load_settings(); r = s.get("role_ids", {})
    em = discord.Embed(title="Current Settings", color=0x3498DB)
    em.add_field(name="Group ID", value=s.get("roblox_group_id")); em.add_field(name="Ticket Role", value=s.get("ticket_role_id"))
    em.add_field(name="Roles", value=f"OR: {r.get('or')}\nOF Low: {r.get('of_low')}\nOF High: {r.get('of_high')}", inline=False)
    await it.response.send_message(embed=em, ephemeral=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(); load_settings(); asyncio.create_task(bot.start(DISCORD_TOKEN))
    yield
    await bot.close()

app = FastAPI(lifespan=lifespan)

@app.post("/verify")
async def v_api(req: Request):
    d = await req.json(); rid, rname, gid = d.get("robloxId"), d.get("robloxUsername"), d.get("guildId")
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row; row = conn.execute("SELECT discord_id FROM users WHERE LOWER(pending_roblox_username) = ? ORDER BY rowid DESC LIMIT 1", (str(rname).lower(),)).fetchone(); conn.close()
    if not row: return {"ok": False}
    r, dname, rn = await update_member_status(row["discord_id"], rid, rname, gid)
    if r:
        conn = sqlite3.connect(DB_PATH); conn.execute("UPDATE users SET roblox_id = ?, roblox_username = ?, verified = 1, pending_roblox_username = NULL WHERE discord_id = ?", (str(rid), rname, row["discord_id"])); conn.commit(); conn.close()
        return {"ok": True, "discord_username": dname, "current_rank": rn}
    return {"ok": False}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
