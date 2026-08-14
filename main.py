# เครดิต
# By.ivzex
# By.patxez
# DEV.manpop79
# DEV.Fugus1234
# ฝากติดตามRoblox พวกผมด้วยนะค้าบ
# นำไปขายต่อได้ ให้เครดิตพวกผมด้วยนะค้าบ❤️
import os
import asyncio
import json
import re
import sqlite3
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
SETTINGS_PATH = os.getenv("SETTINGS_PATH", "settings.json")

DEFAULT_SETTINGS = {
    "roblox_group_id": 226834839,
    "roblox_group_url": "https://www.roblox.com/groups/226834839",
    "roblox_map_url": "https://www.roblox.com/th/games/78189317414125/By",
    "verified_role_id": 1479443343367995579,
    "developer_role_id": 1479469155399766129,
    "role_ids": {
        "or": 1479699133001629797,
        "of_low": 1479699314078122094,
        "of_high": 1479699471603470432,
        "guest": None,
    },
    # ใช้เมื่อ rank_name จาก Roblox ตรงกับชื่อยศ หรือมีชื่อยศนี้อยู่ใน rank_name
    "rank_prefixes": {
        "or-1": "OR-1, PC",
        "or-2": "OR-2, PEC",
        "or-3": "OR-3, CPL",
        "or-4": "OR-4, SGT",
        "or-5": "OR-5, SSG",
        "or-6": "OR-6/OR-7, SFC",
        "or-7": "OR-6/OR-7, SFC",
        "or-8": "OR-8/OR-9, MSG",
        "or-9": "OR-8/OR-9, MSG",
        "of-1a": "OF-1A, LTP",
        "of-1b": "OF-1B, 1LT",
        "of-2": "OF-2, CPT",
        "of-3": "OF-3, MAJ",
        "of-4": "OF-4, LTC",
        "of-5": "OF-5, COL",
        "of-6": "OF-6, SRCOL",
        "of-7": "OF-7, PMG",
        "of-8": "OF-8, MG",
        "of-9": "OF-9, GEN",
    },
}

# ใส่ Roblox ID ของ Developer ที่นี่
DEVELOPER_IDS = [5711452462]
VERIFIED_EMOJI = "✅"


def _deep_copy_default_settings():
    return json.loads(json.dumps(DEFAULT_SETTINGS))


def load_settings():
    settings = _deep_copy_default_settings()
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as file:
            saved = json.load(file)
        if isinstance(saved, dict):
            for key, value in saved.items():
                if key == "role_ids" and isinstance(value, dict):
                    settings["role_ids"].update(value)
                elif key == "rank_prefixes" and isinstance(value, dict):
                    settings["rank_prefixes"].update(value)
                else:
                    settings[key] = value
    except FileNotFoundError:
        save_settings(settings)
    except (json.JSONDecodeError, OSError) as error:
        print(f"Settings load error: {error}")
    return settings


def save_settings(settings):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as file:
            json.dump(settings, file, ensure_ascii=False, indent=2)
    except OSError as error:
        print(f"Settings save error: {error}")


def parse_id(value):
    """รองรับทั้งตัวเลข ID และรูปแบบ mention เช่น <@&123456789>"""
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group()) if match else None


def get_role_id(settings, role_type):
    if role_type in {"verified", "developer"}:
        return settings.get(f"{role_type}_role_id")
    return settings.get("role_ids", {}).get(role_type)


# =========================
# DATABASE
# =========================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            discord_id TEXT PRIMARY KEY,
            roblox_id TEXT,
            roblox_username TEXT,
            verified INTEGER DEFAULT 0,
            pending_roblox_username TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def get_user(discord_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM users WHERE discord_id = ?", (str(discord_id),)
    ).fetchone()
    conn.close()
    return row


def update_pending(discord_id, username):
    conn = sqlite3.connect(DB_PATH)
    clean_name = str(username).strip().lower()
    conn.execute(
        """
        INSERT INTO users (discord_id, pending_roblox_username, verified)
        VALUES (?, ?, 0)
        ON CONFLICT(discord_id) DO UPDATE SET
            pending_roblox_username = excluded.pending_roblox_username,
            verified = 0
        """,
        (str(discord_id), clean_name),
    )
    conn.commit()
    conn.close()


# =========================
# BOT SETUP
# =========================
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # ทำให้ปุ่มที่สร้างไว้ก่อนบอทรีสตาร์ตยังใช้งานได้
        self.add_view(VerifyView())
        self.add_view(ReVerifyView())
        await self.tree.sync()
        print(f"Dev System v6 slash commands synced for {self.user}")


bot = MyBot()


def get_roblox_id_by_name(username):
    try:
        response = requests.post(
            "https://users.roblox.com/v1/usernames/users",
            json={"usernames": [username], "excludeBannedUsers": True},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("data"):
            return data["data"][0]["id"]
    except (requests.RequestException, ValueError) as error:
        print(f"Error fetching Roblox ID: {error}")
    return None


def check_group_membership(roblox_id):
    settings = load_settings()
    try:
        response = requests.get(
            f"https://groups.roblox.com/v1/users/{roblox_id}/groups/roles",
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        for group in data.get("data", []):
            if group["group"]["id"] == int(settings["roblox_group_id"]):
                return True, group["role"]["rank"], group["role"]["name"]
    except (requests.RequestException, ValueError, KeyError, TypeError) as error:
        print(f"Error checking group membership: {error}")
    return False, 0, None


def get_prefix_for_rank(rank_val, rank_name, settings):
    """คืนคำนำหน้าที่ตรงกับชื่อยศที่ตั้งไว้ ถ้าไม่พบให้ใช้ค่าเริ่มต้นตาม Rank ID"""
    prefixes = settings.get("rank_prefixes", {})
    normalized_name = str(rank_name or "").strip().lower()

    # ค่าที่เพิ่มผ่าน /ใส่คำนำหน้า จะถูกเลือกก่อนค่า fallback
    for rank_key, prefix in prefixes.items():
        if str(rank_key).strip().lower() in normalized_name:
            return str(prefix).strip()

    # Fallback สำหรับเซิร์ฟเวอร์ที่ใช้ชื่อยศไม่ตรงกับรหัส เช่น ตั้งชื่อเป็นภาษาไทย
    numeric_fallback = {
        1: "OR-1, PC", 2: "OR-2, PEC", 3: "OR-3, CPL", 4: "OR-4, SGT",
        5: "OR-5, SSG", 6: "OR-6/OR-7, SFC", 7: "OR-6/OR-7, SFC",
        8: "OF-1A, LTP", 9: "OF-1B, 1LT", 10: "OF-2, CPT", 11: "OF-2, CPT",
        12: "OF-3, MAJ", 13: "OF-4, LTC", 14: "OF-5, COL", 15: "OF-6, SRCOL",
        16: "OF-7, PMG", 17: "OF-8, MG", 18: "OF-9, GEN",
    }
    return numeric_fallback.get(int(rank_val or 0), "")


async def update_member_status(discord_id, roblox_id, roblox_username, guild_id=None):
    settings = load_settings()
    guild = bot.get_guild(int(guild_id)) if guild_id else None
    if guild is None and bot.guilds:
        guild = bot.guilds[0]
    if guild is None:
        return None, None, None

    try:
        member = await guild.fetch_member(int(discord_id))
        is_in_group, rank_val, rank_name = check_group_membership(roblox_id)
        is_dev = int(roblox_id) in DEVELOPER_IDS

        role_ids_to_manage = {
            parse_id(settings.get("verified_role_id")),
            parse_id(settings.get("developer_role_id")),
            *{
                parse_id(role_id)
                for role_id in settings.get("role_ids", {}).values()
            },
        }
        role_ids_to_manage.discard(None)

        # เก็บโรลอื่นของสมาชิกไว้ ไม่ลบทิ้งทั้งหมดเหมือนโค้ดเดิม
        roles_to_add = [
            role for role in member.roles
            if role != guild.default_role and role.id not in role_ids_to_manage
        ]
        verified_role = guild.get_role(parse_id(settings.get("verified_role_id")))
        if verified_role:
            roles_to_add.append(verified_role)

        if is_dev:
            developer_role = guild.get_role(parse_id(settings.get("developer_role_id")))
            if developer_role:
                roles_to_add.append(developer_role)
            nickname = f"Dev | {roblox_username}"
            display_rank_name = "Developer"
        elif is_in_group:
            if 1 <= rank_val <= 7:
                rank_role = guild.get_role(parse_id(settings["role_ids"].get("or")))
            elif 8 <= rank_val <= 11:
                rank_role = guild.get_role(parse_id(settings["role_ids"].get("of_low")))
            elif 12 <= rank_val <= 18:
                rank_role = guild.get_role(parse_id(settings["role_ids"].get("of_high")))
            else:
                rank_role = None

            if rank_role:
                roles_to_add.append(rank_role)
            prefix = get_prefix_for_rank(rank_val, rank_name, settings)
            nickname = f"{prefix} | {roblox_username}" if prefix else roblox_username
            display_rank_name = rank_name or "ไม่ทราบชื่อยศ"
        else:
            guest_role = guild.get_role(parse_id(settings["role_ids"].get("guest")))
            if guest_role:
                roles_to_add.append(guest_role)
            nickname = f"Guest | {roblox_username}"
            display_rank_name = "Guest"

        # กันโรลซ้ำจากกรณีตั้งค่าบทบาทเดียวกันหลายช่อง
        unique_roles = list({role.id: role for role in roles_to_add}.values())
        await member.edit(roles=unique_roles, nick=nickname[:32])
        return rank_val if not is_dev else 999, member.display_name, display_rank_name
    except (discord.HTTPException, ValueError, TypeError) as error:
        print(f"Update Error: {error}")
        return None, None, None


# =========================
# UI COMPONENTS
# =========================
class VerifyModal(discord.ui.Modal, title="ยืนยันตัวตน Roblox"):
    username = discord.ui.TextInput(
        label="ใส่ชื่อใน Roblox",
        placeholder="พิมพ์ชื่อของคุณที่นี่...",
        min_length=3,
        max_length=20,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        input_name = self.username.value
        roblox_id = get_roblox_id_by_name(input_name)
        if not roblox_id:
            await interaction.response.send_message(
                f"❌ ไม่พบชื่อ Roblox: **{input_name}** กรุณาตรวจสอบการสะกดชื่ออีกครั้ง",
                ephemeral=True,
            )
            return

        is_dev = int(roblox_id) in DEVELOPER_IDS
        is_in_group, _, _ = check_group_membership(roblox_id)
        settings = load_settings()
        if not is_in_group and not is_dev:
            embed_error = discord.Embed(
                title="❌ กรุณาเข้ากลุ่ม Roblox",
                description=(
                    "คุณยังไม่ได้เข้ากลุ่มของเรา! บอทได้ส่งลิงก์กลุ่มไปให้คุณทาง DM แล้วครับ\n\n"
                    f"**ลิงก์กลุ่ม:** [คลิกที่นี่เพื่อเข้ากลุ่ม]({settings['roblox_group_url']})"
                ),
                color=0xFF0000,
            )
            await interaction.response.send_message(embed=embed_error, ephemeral=True)
            try:
                await interaction.user.send(
                    "สวัสดีครับ! กรุณาเข้ากลุ่ม Roblox ของเราก่อนยืนยันตัวตนนะครับ: "
                    f"{settings['roblox_group_url']}"
                )
            except discord.HTTPException:
                pass
            return

        update_pending(interaction.user.id, input_name)
        embed_success = discord.Embed(
            title="กรุณาเข้าแมพเพื่อยืนยันตัวตน", color=0x00FF00
        )
        embed_success.add_field(name="Username", value=f"**{input_name}**", inline=False)
        embed_success.add_field(
            name="Map", value=f"[คลิกที่นี่เพื่อเข้าเกม]({settings['roblox_map_url']})", inline=False
        )
        embed_success.set_footer(text="กรุณาเข้าเกมเพื่อให้ระบบยืนยันอัตโนมัติ")
        await interaction.response.send_message(embed=embed_success, ephemeral=True)


class ReVerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="อัพเดทยศ", style=discord.ButtonStyle.success, custom_id="update_rank")
    async def update_rank(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("กำลังอัพเดทยศ รอสักครู่...", ephemeral=True)
        user = get_user(interaction.user.id)
        if user and user["roblox_id"]:
            rank_val, display_name, rank_name = await update_member_status(
                interaction.user.id,
                user["roblox_id"],
                user["roblox_username"],
                interaction.guild.id if interaction.guild else None,
            )
            if rank_val is not None:
                embed = discord.Embed(title=f"{VERIFIED_EMOJI} อัพเดทยศสำเร็จ", color=0x00FF00)
                embed.description = (
                    f"ข้อมูลของคุณเป็นปัจจุบันแล้ว\n\n**Roblox:** {user['roblox_username']}\n"
                    f"**ยศปัจจุบัน:** {rank_name}"
                )
                await interaction.edit_original_response(content=None, embed=embed)
            else:
                await interaction.edit_original_response(content="❌ เกิดข้อผิดพลาดในการอัพเดทยศ")
        else:
            await interaction.edit_original_response(content="❌ ไม่พบข้อมูลการยืนยันของคุณ")

    @discord.ui.button(label="เปลี่ยน Account", style=discord.ButtonStyle.primary, custom_id="change_acc")
    async def change_acc(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerifyModal())


class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="ยืนยันตัวตน",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="persistent_verify",
    )
    async def start_v(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = get_user(interaction.user.id)
        if user and user["verified"]:
            embed = discord.Embed(title="❗พบข้อมูล Roblox Account อยู่แล้ว❗", color=0x3498DB)
            embed.add_field(
                name="ข้อมูลปัจจุบัน:",
                value=(
                    f"**Roblox:** {user['roblox_username']}\n"
                    f"**Roblox ID:** {user['roblox_id']}\n"
                    f"**สถานะ:** ยืนยันแล้ว {VERIFIED_EMOJI}"
                ),
                inline=False,
            )
            embed.description = "ต้องการเปลี่ยน Account หรืออัพเดทยศ? กดปุ่มด้านล่าง"
            await interaction.response.send_message(embed=embed, view=ReVerifyView(), ephemeral=True)
        else:
            await interaction.response.send_modal(VerifyModal())


class CustomizeAllModal(discord.ui.Modal, title="ปรับแต่งระบบทั้งหมด"):
    group_id = discord.ui.TextInput(
        label="Roblox Group ID",
        required=False,
        placeholder="ใส่ ID กลุ่ม (ตัวเลขเท่านั้น) เช่น 226834839",
    )
    group_url = discord.ui.TextInput(
        label="ลิงก์กลุ่ม Roblox",
        required=False,
        placeholder="https://www.roblox.com/groups/...",
    )
    map_url = discord.ui.TextInput(
        label="ลิงก์แมพ Roblox",
        required=False,
        placeholder="https://www.roblox.com/games/...",
    )
    prefixes = discord.ui.TextInput(
        label="คำนำหน้า (แยกด้วย ;) เช่น OF-3=MAJ; OF-4=LTC",
        required=False,
        style=discord.TextStyle.paragraph,
        placeholder="or-1=PC; of-3=MAJ",
    )
    role_ids = discord.ui.TextInput(
        label="Role IDs (แยกด้วย ;) เช่น or=123; guest=456",
        required=False,
        style=discord.TextStyle.paragraph,
        placeholder="verified=ID; or=ID; of_low=ID; of_high=ID; guest=ID",
    )

    async def on_submit(self, interaction: discord.Interaction):
        settings = load_settings()
        
        # อัพเดท Group ID
        if self.group_id.value.strip():
            gid = parse_id(self.group_id.value.strip())
            if gid: settings["roblox_group_id"] = gid
            
        if self.group_url.value.strip():
            settings["roblox_group_url"] = self.group_url.value.strip()
        if self.map_url.value.strip():
            settings["roblox_map_url"] = self.map_url.value.strip()

        # อัพเดท Prefixes แบบกลุ่ม
        if self.prefixes.value.strip():
            for item in self.prefixes.value.split(";"):
                if "=" not in item: continue
                k, v = item.split("=", 1)
                k, v = k.strip().lower(), v.strip()
                if k and v:
                    # ถ้าใส่มาแค่ชื่อยศ เช่น "MAJ" จะแปลงเป็น "OF-3, MAJ" ให้ตาม format
                    if "," not in v and "-" in k:
                        settings["rank_prefixes"][k] = f"{k.upper()}, {v}"
                    else:
                        settings["rank_prefixes"][k] = v

        # อัพเดท Role IDs แบบกลุ่ม
        if self.role_ids.value.strip():
            for item in self.role_ids.value.split(";"):
                if "=" not in item: continue
                rtype, rid_raw = item.split("=", 1)
                rtype = rtype.strip().lower()
                rid = parse_id(rid_raw)
                if not rid: continue
                
                if rtype in {"verified", "developer"}:
                    settings[f"{rtype}_role_id"] = rid
                elif rtype in {"or", "of_low", "of_high", "guest"}:
                    settings["role_ids"][rtype] = rid

        save_settings(settings)
        await interaction.response.send_message(
            "✅ ปรับแต่งระบบทั้งหมดและบันทึกค่าเรียบร้อยแล้ว\n"
            "การตั้งค่าจะมีผลกับสมาชิกที่กดยืนยันใหม่หรือกดอัพเดทยศครั้งถัดไป",
            ephemeral=True,
        )


# =========================
# SLASH COMMANDS
# =========================
@bot.tree.command(name="ยืนยันตัวตน", description="ตั้งค่าระบบยืนยันตัวตน (Administrator Only)")
@app_commands.default_permissions(administrator=True)
async def setup_verify(interaction: discord.Interaction):
    settings = load_settings()
    embed = discord.Embed(
        title="ระบบยืนยันตัวตนทหารไทย",
        description="กรุณากดปุ่มด้านล่างเพื่อเริ่มการยืนยันตัวตนกับ Roblox",
        color=0x2B2D31,
    )
    await interaction.channel.send(embed=embed, view=VerifyView())
    await interaction.response.send_message("✅ ตั้งค่าระบบยืนยันตัวตนเรียบร้อยแล้ว", ephemeral=True)


async def clear_verification_data(interaction: discord.Interaction):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM users")
    conn.commit()
    conn.close()
    await interaction.response.send_message(
        "⚠️ [Admin] ล้างข้อมูลการยืนยันตัวตนทั้งหมดเรียบร้อยแล้ว ทุกคนต้องยืนยันใหม่!",
        ephemeral=True,
    )


@bot.tree.command(name="ล้างข้อมูล", description="ลบข้อมูลการยืนยันตัวตนทุกคน")
@app_commands.default_permissions(administrator=True)
async def reset_db_short(interaction: discord.Interaction):
    await clear_verification_data(interaction)


# คงคำสั่งเดิมไว้เพื่อไม่ให้เซิร์ฟเวอร์ที่เคยใช้คำสั่งนี้เสียการทำงาน
@bot.tree.command(name="ล้างข้อมูลทั้งหมด", description="ลบข้อมูลการยืนยันตัวตนทุกคน (คำสั่งเดิม)")
@app_commands.default_permissions(administrator=True)
async def reset_db_legacy(interaction: discord.Interaction):
    await clear_verification_data(interaction)


@bot.tree.command(name="ใส่โรล", description="ตั้งค่า Role ให้กับประเภทที่เลือก")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    ประเภท="verified, developer, or, of_low, of_high หรือ guest",
    โรล="เลือก Role ที่ต้องการให้ระบบใช้",
)
@app_commands.choices(
    ประเภท=[
        app_commands.Choice(name="ยืนยันตัวตน", value="verified"),
        app_commands.Choice(name="Developer", value="developer"),
        app_commands.Choice(name="OR", value="or"),
        app_commands.Choice(name="OF Low", value="of_low"),
        app_commands.Choice(name="OF High", value="of_high"),
        app_commands.Choice(name="Guest", value="guest"),
    ]
)
async def set_role(interaction: discord.Interaction, ประเภท: app_commands.Choice[str], โรล: discord.Role):
    settings = load_settings()
    role_type = ประเภท.value
    if role_type in {"verified", "developer"}:
        settings[f"{role_type}_role_id"] = โรล.id
    else:
        settings["role_ids"][role_type] = โรล.id
    save_settings(settings)
    await interaction.response.send_message(
        f"✅ ตั้งค่าโรล **{โรล.name}** ให้กับประเภท **{ประเภท.name}** เรียบร้อยแล้ว",
        ephemeral=True,
    )


@bot.tree.command(name="ใส่คำนำหน้า", description="เพิ่มหรือแก้คำนำหน้าตามชื่อยศ Roblox")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    ยศ="รหัสยศ เช่น OF-3 หรือ OR-1 ต้องตรงหรือเป็นส่วนหนึ่งของชื่อยศ Roblox",
    คำนำหน้า="ชื่อคำนำหน้า เช่น MAJ หรือ PC",
)
async def set_prefix(interaction: discord.Interaction, ยศ: str, คำนำหน้า: str):
    rank_code = ยศ.strip()
    title = คำนำหน้า.strip()
    if not rank_code or not title:
        await interaction.response.send_message("❌ กรุณาระบุยศและคำนำหน้าให้ครบ", ephemeral=True)
        return

    settings = load_settings()
    settings["rank_prefixes"][rank_code.lower()] = f"{rank_code}, {title}"
    save_settings(settings)
    await interaction.response.send_message(
        f"✅ เพิ่มคำนำหน้า **{rank_code}, {title}** แล้ว\n"
        "สมาชิกจะเห็นผลเมื่อกดยืนยันใหม่หรือกดปุ่มอัพเดทยศ",
        ephemeral=True,
    )


@bot.tree.command(name="ปรับแต่งทั้งหมด", description="เปิดหน้าต่างปรับแต่งระบบกลุ่ม โรล และคำนำหน้า")
@app_commands.default_permissions(administrator=True)
async def customize_all(interaction: discord.Interaction):
    await interaction.response.send_modal(CustomizeAllModal())


@bot.tree.command(name="ดูการตั้งค่า", description="ดูค่าการตั้งค่าระบบปัจจุบัน (Administrator Only)")
@app_commands.default_permissions(administrator=True)
async def show_settings(interaction: discord.Interaction):
    settings = load_settings()
    role_ids = settings.get("role_ids", {})
    embed = discord.Embed(title="การตั้งค่าระบบปัจจุบัน", color=0x3498DB)
    embed.add_field(name="Group ID", value=str(settings.get("roblox_group_id")), inline=False)
    embed.add_field(name="Verified Role ID", value=str(settings.get("verified_role_id")), inline=False)
    embed.add_field(
        name="Role IDs",
        value=(
            f"OR: `{role_ids.get('or')}`\n"
            f"OF Low: `{role_ids.get('of_low')}`\n"
            f"OF High: `{role_ids.get('of_high')}`\n"
            f"Guest: `{role_ids.get('guest')}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="คำนำหน้าที่ตั้งไว้",
        value="\n".join(
            f"`{key}` → {value}" for key, value in settings.get("rank_prefixes", {}).items()
        )[:1024]
        or "ยังไม่มี",
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# =========================
# FASTAPI WEBHOOK
# =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    load_settings()
    asyncio.create_task(bot.start(DISCORD_TOKEN))
    yield
    await bot.close()


app = FastAPI(lifespan=lifespan)


@app.post("/verify")
async def verify_endpoint(request: Request):
    data = await request.json()
    roblox_id = data.get("robloxId")
    roblox_username = str(data.get("robloxUsername", "")).strip()
    guild_id = data.get("guildId")
    search_name = roblox_username.lower()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT discord_id FROM users
        WHERE LOWER(TRIM(pending_roblox_username)) = ?
        ORDER BY rowid DESC LIMIT 1
        """,
        (search_name,),
    ).fetchone()
    conn.close()

    if not row:
        return {
            "ok": False,
            "message": (
                f"ไม่พบชื่อ '{roblox_username}' ในรายการรอ "
                "(กรุณากดปุ่มยืนยันใน Discord ก่อน)"
            ),
        }

    rank, display_name, rank_name = await update_member_status(
        row["discord_id"], roblox_id, roblox_username, guild_id
    )
    if rank is not None:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """
            UPDATE users
            SET roblox_id = ?, roblox_username = ?, verified = 1,
                pending_roblox_username = NULL
            WHERE discord_id = ?
            """,
            (str(roblox_id), roblox_username, row["discord_id"]),
        )
        conn.commit()
        conn.close()
        return {
            "ok": True,
            "discord_username": display_name,
            "current_rank": rank_name,
        }

    return {"ok": False, "message": "บอทไม่มีสิทธิ์เปลี่ยนยศหรือไม่พบเซิร์ฟเวอร์ Discord"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)

# วิธีใช้คำสั่งใหม่
# /ใส่โรล ประเภท: OR โรล: @ชื่อโรล
# /ใส่คำนำหน้า ยศ: OF-3 คำนำหน้า: MAJ
# /ปรับแต่งทั้งหมด แล้วกรอก prefixes เป็น OF-3=MAJ; OF-4=LTC
# /ดูการตั้งค่า
# /ล้างข้อมูล หรือ /ล้างข้อมูลทั้งหมด
# หมายเหตุ: ต้องเปิด Server Members Intent และให้บอทมี Manage Roles / Manage Nicknames
# รวมถึงลาก Role ของบอทให้อยู่สูงกว่า Role ที่บอทต้องจัดการ

# อ้างอิงไฟล์เดิมเก็บไว้ที่ pasted_content.backup.txt
# สร้าง settings.json อัตโนมัติเมื่อรันครั้งแรก
# ใน webhook สามารถส่ง guildId เพิ่มได้ เช่น {"guildId": "123456789"}
# หากไม่ส่ง guildId ระบบจะใช้เซิร์ฟเวอร์แรกที่บอทเข้าร่วมเหมือนพฤติกรรมเดิม

# สิ้นสุดไฟล์

# หมายเหตุ: บรรทัดคอมเมนต์ด้านล่างนี้ใช้เพื่อย้ำรูปแบบคำสั่งภาษาไทยเท่านั้น
# /ยืนยันตัวตน ยังคงเป็นคำสั่งตั้งค่าข้อความปุ่มยืนยันตัวตน
# /ล้างข้อมูล และ /ล้างข้อมูลทั้งหมด ยังคงล้างเฉพาะข้อมูลในตาราง users

# ขอบคุณที่ซื้อนะค้าบ❤️❤️
