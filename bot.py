import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import os

# --- การตั้งค่าบอท ---
# แนะนำให้ใส่ใน Railway Environment Variables เพื่อความปลอดภัย
TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [1284107691723067454] # รายชื่อ ID แอดมิน
LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID', '1532801908949909604'))
VERIFIED_ROLE_ID = int(os.getenv('VERIFIED_ROLE_ID', '1532801945981423847'))
IMAGE_URL = "https://cdn.discordapp.com/attachments/1529416449267859567/1530830006534537256/image.png?ex=6a67000b&is=6a65ae8b&hm=11ca199cc05a03507fac9ed75273ff81ae668a9b5d45b4c6ae22c4b701692963&"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- Modals ---

class QuestionnaireModal(discord.ui.Modal, title='ตอบไม่งั้นพ่อมึงตาย'):
    roblox_name = discord.ui.TextInput(label='ชื่อใน Roblox', placeholder='...')
    dept = discord.ui.TextInput(label='มึงมาจากกรมไร', placeholder='...')
    unit = discord.ui.TextInput(label='มึงอยู่หน่วยอะไรจากรมนั้น', placeholder='...')
    rebel = discord.ui.TextInput(label='กฏบพ่องตาย', placeholder='...', style=discord.TextStyle.long)

    def __init__(self, user):
        super().__init__()
        self.user = user

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("รอนะไอโง่", ephemeral=True)
        
        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(title="📝 มีการขอเข้ากลุ่มใหม่", color=discord.Color.blue())
            embed.add_field(name="ผู้สมัคร", value=f"{self.user.mention} ({self.user.id})", inline=False)
            embed.add_field(name="ชื่อ Roblox", value=self.roblox_name.value, inline=True)
            embed.add_field(name="กรม", value=self.dept.value, inline=True)
            embed.add_field(name="หน่วย", value=self.unit.value, inline=True)
            embed.add_field(name="สถานะกบฏ", value=self.rebel.value, inline=False)
            
            view = AdminReviewView(self.user, self.roblox_name.value)
            # แท็กแอดมินคนแรกเป็นตัวอย่าง หรือแท็กทุกคน
            admin_mentions = " ".join([f"<@{admin_id}>" for admin_id in ADMIN_IDS])
            await log_channel.send(content=f"{admin_mentions} รีบๆตอบดิไอโง่ปัญญาอ่อน", embed=embed, view=view)

# --- Views ---

class AdminReviewView(discord.ui.View):
    def __init__(self, target_user, roblox_name):
        super().__init__(timeout=None)
        self.target_user = target_user
        self.roblox_name = roblox_name

    @discord.ui.button(label="รับ (Accept)", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in ADMIN_IDS:
            return await interaction.response.send_message("คุณไม่มีสิทธิ์กดปุ่มนี้!", ephemeral=True)

        role = interaction.guild.get_role(VERIFIED_ROLE_ID)
        if role:
            try:
                await self.target_user.add_roles(role)
                await interaction.response.send_message(f"รับ {self.target_user.mention} เข้ากลุ่มแล้ว!", ephemeral=False)
                try:
                    await self.target_user.send(f"ยินดีด้วย! คุณได้รับการอนุมัติเข้ากลุ่มแล้วในเซิร์ฟเวอร์ {interaction.guild.name}")
                except:
                    pass
                for child in self.children:
                    child.disabled = True
                await interaction.message.edit(view=self)
            except Exception as e:
                await interaction.response.send_message(f"เกิดข้อผิดพลาด: {e}", ephemeral=True)
        else:
            await interaction.response.send_message("ไม่พบยศที่ตั้งค่าไว้!", ephemeral=True)

    @discord.ui.button(label="ไม่รับ (Reject)", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in ADMIN_IDS:
            return await interaction.response.send_message("คุณไม่มีสิทธิ์กดปุ่มนี้!", ephemeral=True)

        try:
            await self.target_user.kick(reason="ไม่ผ่านการยืนยันตัวตน")
            await interaction.response.send_message(f"เตะ {self.target_user.mention} ออกจากเซิร์ฟเวอร์แล้ว", ephemeral=False)
            for child in self.children:
                child.disabled = True
            await interaction.message.edit(view=self)
        except Exception as e:
            await interaction.response.send_message(f"เกิดข้อผิดพลาดในการเตะ: {e}", ephemeral=True)

class ReadyCheckView(discord.ui.View):
    def __init__(self, user):
        super().__init__(timeout=60)
        self.user = user

    @discord.ui.button(label="พร้อม", style=discord.ButtonStyle.primary)
    async def ready(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("ไม่ใช่คิวของคุณ!", ephemeral=True)
        await interaction.response.send_modal(QuestionnaireModal(self.user))

    @discord.ui.button(label="ไม่พร้อม", style=discord.ButtonStyle.secondary)
    async def not_ready(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("ไม่ใช่คิวของคุณ!", ephemeral=True)
        await interaction.response.send_message("เมื่อคุณพร้อม สามารถกดปุ่มยืนยันตัวตนใหม่ได้ทุกเมื่อ", ephemeral=True)

class InitialVerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
    @discord.ui.button(label="กดตรงนี้ครับไอปัญญาอ่อน", style=discord.ButtonStyle.success, emoji="✅")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ReadyCheckView(interaction.user)
        await interaction.response.send_message("พร้อมที่จะเข้าดิสกูไหม?", view=view, ephemeral=True)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_verify(ctx):
    embed = discord.Embed(
        title="ยืนยันตัวตนครับไอควาย",
        description="กดปุ่ด้านล่างครับไอหน้าหี",
        color=discord.Color.green()
    )
    if IMAGE_URL:
        embed.set_image(url=IMAGE_URL)
    view = InitialVerifyView()
    await ctx.send(embed=embed, view=view)
    await ctx.message.delete()

if __name__ == "__main__":
    bot.run(TOKEN)
