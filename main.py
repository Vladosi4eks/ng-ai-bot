import os
import sys
import subprocess
import io
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp

# --- АВТОМАТИЧЕСКАЯ УСТАНОВКА CHROMIUM ПРИ ОТСУТСТВИИ ---
browsers_path = os.path.abspath("./.browsers")
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browsers_path

if not os.path.exists(browsers_path) or not os.listdir(browsers_path):
    print("⏳ Chromium не найден. Загружаю браузер (это может занять до 1 минуты)...")
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        print("✅ Chromium успешно установлен!")
    except Exception as e:
        print(f"⚠️ Ошибка установки браузера: {e}")

from playwright.async_api import async_playwright

# ==================== ТВОЇ ДАНІ ЗАПОВНЕНІ ====================
DISCORD_TOKEN = "MTU2Mjg4MTc3NzE5Njg1OTQ5Mw.GrTck9.wXBt0iTpwuc4NsfIL5StCv6dx_EjT8u_eqVAbo"
CHANNEL_ID = 1562885221051801740
PROJECT_URL = "https://flow.google.com/u/4/project/4f535ba4-8389-4a4d-8a10-8cc8f13e1071"
USER_DATA_DIR = "./"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

playwright_instance = None
browser_context = None
flow_page = None
page_lock = asyncio.Lock()


async def init_browser():
    """Запуск фонового Chromium із підключенням сесії."""
    global playwright_instance, browser_context, flow_page
    playwright_instance = await async_playwright().start()

    browser_context = await playwright_instance.chromium.launch_persistent_context(
        user_data_dir=USER_DATA_DIR,
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
        ],
        ignore_default_args=["--enable-automation"],
    )

    flow_page = browser_context.pages[0] if browser_context.pages else await browser_context.new_page()
    print("⏳ Відкриваю проєкт Google Flow у фоні...")
    await flow_page.goto(PROJECT_URL, wait_until="networkidle")
    print("✅ Сторінка Flow готова до генерацій!")


async def run_flow_generation(prompt: str) -> str | None:
    """Введення запиту у веб-інтерфейс і перехоплення CDN-посилання."""
    global flow_page

    async with page_lock:
        captured_url = None
        response_future = asyncio.get_event_loop().create_future()

        def on_response(response):
            nonlocal captured_url
            url = response.url
            if "flow-content.google" in url and ("image" in url or "video" in url):
                if not response_future.done():
                    captured_url = url
                    response_future.set_result(url)

        flow_page.on("response", on_response)

        try:
            input_selector = "textarea, div[contenteditable='true'], input[type='text']"
            await flow_page.wait_for_selector(input_selector, timeout=15000)
            
            await flow_page.click(input_selector)
            await flow_page.fill(input_selector, prompt)
            await flow_page.keyboard.press("Enter")

            await asyncio.wait_for(response_future, timeout=120.0)
            return captured_url

        except asyncio.TimeoutError:
            print("[Flow] Перевищено час очікування відповіді від CDN.")
            return None
        except Exception as e:
            print(f"[Flow Error]: {e}")
            return None
        finally:
            flow_page.remove_listener("response", on_response)


# ==================== МОДАЛЬНЕ ВІКНО ====================
class FlowSubmitModal(discord.ui.Modal):
    def __init__(self, settings: dict):
        super().__init__(title="Створення у Google Flow")
        self.settings = settings

        self.prompt = discord.ui.TextInput(
            label="Опишіть, що створити:",
            style=discord.TextStyle.paragraph,
            placeholder="Введіть промпт для генерації...",
            required=True,
            max_length=2000,
        )
        self.add_item(self.prompt)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        user_prompt = self.prompt.value

        file_url = await run_flow_generation(user_prompt)

        if not file_url:
            await interaction.followup.send("⚠️ Не вдалося згенерувати результат. Перевір консоль сервера.")
            return

        async with aiohttp.ClientSession() as session:
            async with session.get(file_url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    ext = "mp4" if self.settings["type"] == "video" else "png"
                    file = discord.File(io.BytesIO(data), filename=f"flow_output.{ext}")
                    await interaction.followup.send(
                        content=f"✨ **Готово!** ({self.settings['model']})\n**Запит:** {user_prompt}",
                        file=file,
                    )
                else:
                    await interaction.followup.send(f"Ось посилання: {file_url}")


# ==================== ПАНЕЛЬ КЕРУВАННЯ ====================
class FlowControlPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.state = {
            "type": "image",
            "aspect": "9:16",
            "model": "Nano Banana 2.1",
        }
        self.rebuild_components()

    def get_embed(self) -> discord.Embed:
        is_video = self.state["type"] == "video"
        title = "🎬 Google Flow: Режим Відео" if is_video else "🖼️ Google Flow: Режим Зображення"
        embed = discord.Embed(title=title, color=0x9B59B6 if is_video else 0x3498DB)
        embed.add_field(name="Формат", value=f"`{self.state['aspect']}`", inline=True)
        embed.add_field(name="Модель", value=f"`{self.state['model']}`", inline=True)
        embed.set_footer(text="Google Flow Pro (Auto Chromium)")
        return embed

    def rebuild_components(self):
        self.clear_items()
        is_video = self.state["type"] == "video"

        btn_img = discord.ui.Button(
            label="Изображение", emoji="🖼️",
            style=discord.ButtonStyle.success if not is_video else discord.ButtonStyle.secondary, row=0
        )
        btn_vid = discord.ui.Button(
            label="Видео", emoji="🎥",
            style=discord.ButtonStyle.success if is_video else discord.ButtonStyle.secondary, row=0
        )

        async def switch_img(it: discord.Interaction):
            self.state["type"] = "image"
            self.state["model"] = "Nano Banana 2.1"
            self.rebuild_components()
            await it.response.edit_message(embed=self.get_embed(), view=self)

        async def switch_vid(it: discord.Interaction):
            self.state["type"] = "video"
            self.state["model"] = "Omni 1.1 Flash"
            self.rebuild_components()
            await it.response.edit_message(embed=self.get_embed(), view=self)

        btn_img.callback = switch_img
        btn_vid.callback = switch_vid
        self.add_item(btn_img)
        self.add_item(btn_vid)

        if not is_video:
            model_select = discord.ui.Select(
                placeholder="Оберіть модель...",
                options=[
                    discord.SelectOption(label="Nano Banana Pro", emoji="🍌", default=self.state["model"] == "Nano Banana Pro"),
                    discord.SelectOption(label="Nano Banana 2.1", emoji="🍌", default=self.state["model"] == "Nano Banana 2.1"),
                ],
                row=1,
            )
        else:
            model_select = discord.ui.Select(
                placeholder="Оберіть модель...",
                options=[
                    discord.SelectOption(label="Omni 1.1 Flash", emoji="🔊", default=True),
                    discord.SelectOption(label="Veo 3.1 – Quality", emoji="🔊"),
                ],
                row=1,
            )

        async def model_cb(it: discord.Interaction):
            self.state["model"] = model_select.values[0]
            self.rebuild_components()
            await it.response.edit_message(embed=self.get_embed(), view=self)

        model_select.callback = model_cb
        self.add_item(model_select)

        create_btn = discord.ui.Button(label="Створити...", emoji="✨", style=discord.ButtonStyle.success, row=2)

        async def create_cb(it: discord.Interaction):
            await it.response.send_modal(FlowSubmitModal(settings=self.state))

        create_btn.callback = create_cb
        self.add_item(create_btn)


# ==================== КОМАНДИ ====================
@bot.tree.command(name="flow", description="Відкрити меню Google Flow")
async def slash_flow(interaction: discord.Interaction):
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message("Ця команда активна лише в призначеному каналі!", ephemeral=True)
        return
    panel = FlowControlPanel()
    await interaction.response.send_message(embed=panel.get_embed(), view=panel)


@bot.command(name="flow")
async def prefix_flow(ctx):
    if ctx.channel.id != CHANNEL_ID:
        return
    panel = FlowControlPanel()
    await ctx.send(embed=panel.get_embed(), view=panel)


@bot.event
async def on_ready():
    print(f"Запускаю браузер для бота {bot.user}...")
    await init_browser()
    await bot.tree.sync()
    print(f"🚀 Бот успішно онлайн! Використовуй !flow або /flow у каналі.")


bot.run(DISCORD_TOKEN)