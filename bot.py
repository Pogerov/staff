import discord
from discord.ext import commands
from discord import app_commands
import json
import os
from datetime import datetime
from aiohttp import web
import asyncio

# ================== НАСТРОЙКИ ==================
TOKEN = os.getenv("TOKEN")  # токен из Environment Variables на Render

# ⚠️ ЗАМЕНИ ЭТИ ID НА СВОИ ⚠️
APPLY_CHANNEL_ID = 1564200703940100106   # канал с кнопкой "Заявка"
LOG_CHANNEL_ID = 1564528314650075137     # канал для логов (только стафф)
MAIN_ROLE_ID = 1564523461559189617       # роль Main (выдаётся при принятии)
FAMILY_ROLE_ID = 1564523599744733214     # роль Family (выдаётся при принятии)
GUILD_ID = 1563144842656350360           # ID твоего сервера

# 🛡️ СТАФФ-РОЛИ — можно указать сколько угодно
# Вариант 1 (по умолчанию): задай список прямо тут
STAFF_ROLES = [
    1564529213510389800,   # роль стаффа №1 — замени
    1564528457386430576,   # роль стаффа №2 — замени (если не нужна — удали строку)
]

# Вариант 2 (гибкий): если задана env-переменная STAFF_ROLES — используется она
_env_staff = os.getenv("STAFF_ROLES")
if _env_staff:
    STAFF_ROLES = [int(x.strip()) for x in _env_staff.split(",") if x.strip()]
# ===============================================

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

DATA_FILE = "applications.json"


# ---------- хранилище заявок ----------
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------- проверка: является ли юзер стаффом ----------
def is_staff(member: discord.Member) -> bool:
    user_role_ids = {r.id for r in member.roles}
    return bool(user_role_ids & set(STAFF_ROLES))


# ================== ФОРМА ЗАЯВКИ (3 ВОПРОСА) ==================
class ApplicationModal(discord.ui.Modal):
    def __init__(self, team: str):
        super().__init__(title=f"Заявка в {team}")
        self.team = team

        self.q1 = discord.ui.TextInput(
            label="Зачем ты подал заявку сюда?",
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=True
        )
        self.q2 = discord.ui.TextInput(
            label="Что ты будешь давать для сервера?",
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=True
        )
        self.q3 = discord.ui.TextInput(
            label="От тебя нужен актив, ты готов?",
            style=discord.TextStyle.paragraph,
            max_length=300,
            required=True
        )
        self.add_item(self.q1)
        self.add_item(self.q2)
        self.add_item(self.q3)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if log_channel is None:
            await interaction.followup.send("❌ Канал логов не найден.", ephemeral=True)
            return

        app_id = f"{interaction.user.id}_{int(datetime.now().timestamp())}"

        embed = discord.Embed(
            title=f"📩 Новая заявка — {self.team}",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="👤 Кандидат", value=f"{interaction.user.mention}\n`{interaction.user}`", inline=False)
        embed.add_field(name="🎯 Отряд", value=self.team, inline=True)
        embed.add_field(name="🆔 ID заявки", value=f"`{app_id}`", inline=True)
        embed.add_field(name="❓ 1. Зачем ты подал заявку сюда?", value=self.q1.value, inline=False)
        embed.add_field(name="❓ 2. Что ты будешь давать для сервера?", value=self.q2.value, inline=False)
        embed.add_field(name="❓ 3. Готов быть активным?", value=self.q3.value, inline=False)
        embed.set_footer(text="Заявка ожидает решения стаффа")

        view = ReviewView(app_id=app_id, applicant_id=interaction.user.id, team=self.team)

        # пингуем ВСЕ стафф-роли
        staff_pings = " ".join(f"<@&{rid}>" for rid in STAFF_ROLES)
        msg = await log_channel.send(content=staff_pings or None, embed=embed, view=view)

        data = load_data()
        data[app_id] = {
            "applicant_id": interaction.user.id,
            "applicant_name": str(interaction.user),
            "team": self.team,
            "answers": {"q1": self.q1.value, "q2": self.q2.value, "q3": self.q3.value},
            "status": "pending",
            "message_id": msg.id,
            "created_at": datetime.now().isoformat()
        }
        save_data(data)

        await interaction.followup.send(
            f"✅ Твоя заявка в **{self.team}** отправлена на рассмотрение стаффу!",
            ephemeral=True
        )


# ================== КНОПКИ ПРИНЯТЬ / ОТКЛОНИТЬ ==================
class ReviewView(discord.ui.View):
    def __init__(self, app_id: str, applicant_id: int, team: str):
        super().__init__(timeout=None)
        self.app_id = app_id
        self.applicant_id = applicant_id
        self.team = team

    async def _check_staff(self, interaction: discord.Interaction) -> bool:
        if not is_staff(interaction.user):
            await interaction.response.send_message("❌ Только стафф может это делать.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Принять", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_staff(interaction):
            return

        data = load_data()
        if self.app_id not in data or data[self.app_id]["status"] != "pending":
            await interaction.response.send_message("⚠️ Заявка уже обработана или не найдена.", ephemeral=True)
            return

        guild = interaction.guild
        member = guild.get_member(self.applicant_id)
        role_id = MAIN_ROLE_ID if "Main" in self.team else FAMILY_ROLE_ID
        role = guild.get_role(role_id)

        if member and role:
            try:
                await member.add_roles(role)
            except discord.Forbidden:
                pass

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.set_footer(text=f"✅ Принято стаффом: {interaction.user}")
        embed.add_field(
            name="📊 Решение",
            value=f"**Принято** ✅\nСтафф: {interaction.user.mention}",
            inline=False
        )

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)

        data[self.app_id]["status"] = "accepted"
        data[self.app_id]["reviewed_by"] = str(interaction.user)
        save_data(data)

        if member:
            try:
                await member.send(
                    f"🎉 Твоя заявка в **{self.team}** на сервере **{guild.name}** была **принята**!\n"
                    f"Тебе выдана роль: **{role.name if role else '—'}**"
                )
            except discord.Forbidden:
                pass

    @discord.ui.button(label="❌ Отклонить", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_staff(interaction):
            return

        data = load_data()
        if self.app_id not in data or data[self.app_id]["status"] != "pending":
            await interaction.response.send_message("⚠️ Заявка уже обработана или не найдена.", ephemeral=True)
            return

        guild = interaction.guild
        member = guild.get_member(self.applicant_id)

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.set_footer(text=f"❌ Отклонено стаффом: {interaction.user}")
        embed.add_field(
            name="📊 Решение",
            value=f"**Отклонено** ❌\nСтафф: {interaction.user.mention}",
            inline=False
        )

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(embed=embed, view=self)

        data[self.app_id]["status"] = "declined"
        data[self.app_id]["reviewed_by"] = str(interaction.user)
        save_data(data)

        if member:
            try:
                await member.send(
                    f"😔 К сожалению, твоя заявка в **{self.team}** на сервере **{guild.name}** была **отклонена**."
                )
            except discord.Forbidden:
                pass


# ================== ВЫБОР ОТРЯДА ==================
class TeamSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="👁️ Main", style=discord.ButtonStyle.primary)
    async def main_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ApplicationModal(team="Main"))

    @discord.ui.button(label="✅ Family", style=discord.ButtonStyle.success)
    async def family_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ApplicationModal(team="Family"))


# ================== ГЛАВНАЯ КНОПКА "ЗАЯВКА" ==================
class ApplyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📨 Заявка", style=discord.ButtonStyle.primary, custom_id="apply_button")
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "🎯 Выбери отряд, в который хочешь подать заявку:",
            view=TeamSelectView(),
            ephemeral=True
        )


# ================== СОБЫТИЯ ==================
@bot.event
async def on_ready():
    print(f"✅ Бот запущен как {bot.user}")
    print(f"🛡️ Стафф-роли: {STAFF_ROLES}")
    try:
        bot.add_view(ApplyView())
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print(f"Синхронизировано {len(synced)} команд")
    except Exception as e:
        print(f"Ошибка синхронизации: {e}")


# ================== КОМАНДЫ ==================
@bot.tree.command(name="setup_apply", description="Отправить сообщение с набором в клан", guild=discord.Object(id=GUILD_ID))
@app_commands.checks.has_permissions(administrator=True)
async def setup_apply(interaction: discord.Interaction):
    channel = bot.get_channel(APPLY_CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message("❌ Канал не найден. Проверь APPLY_CHANNEL_ID.", ephemeral=True)
        return

    embed = discord.Embed(
        title="⚔️ Набор в клан",
        description=(
            "**Вступайте в наш клан в отряды Main или Family.**\n"
            "**Каждому будем рады!** 🎉\n\n"
            "Нажми на кнопку ниже, чтобы подать заявку 👇"
        ),
        color=discord.Color.gold()
    )

    await channel.send(embed=embed, view=ApplyView())
    await interaction.response.send_message("✅ Сообщение с набором отправлено!", ephemeral=True)


@bot.tree.command(name="applications_stats", description="Статистика по заявкам", guild=discord.Object(id=GUILD_ID))
@app_commands.checks.has_permissions(administrator=True)
async def apps_stats(interaction: discord.Interaction):
    data = load_data()
    pending = sum(1 for v in data.values() if v["status"] == "pending")
    accepted = sum(1 for v in data.values() if v["status"] == "accepted")
    declined = sum(1 for v in data.values() if v["status"] == "declined")
    embed = discord.Embed(title="📊 Статистика заявок", color=discord.Color.purple())
    embed.add_field(name="⏳ Ожидают", value=str(pending))
    embed.add_field(name="✅ Принято", value=str(accepted))
    embed.add_field(name="❌ Отклонено", value=str(declined))
    embed.add_field(name="📁 Всего", value=str(len(data)))
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("❌ Нет прав.", ephemeral=True)


# ================== ВЕБ-СЕРВЕР ДЛЯ RENDER ==================
async def healthcheck(request):
    return web.Response(text="Bot is alive!")


async def start_web():
    app = web.Application()
    app.router.add_get("/", healthcheck)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Веб-сервер запущен на порту {port}")


async def main():
    asyncio.create_task(start_web())
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
