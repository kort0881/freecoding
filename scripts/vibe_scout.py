import os, json, asyncio, hashlib, time, logging
from datetime import datetime, timezone

import aiohttp
from bs4 import BeautifulSoup
from openai import AsyncOpenAI
from aiogram import Bot, Dispatcher, types, Router
from aiogram.filters import Command
from aiogram.types import Message

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
XAI_API_KEY = os.getenv("XAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@your_channel")
GH_TOKEN = os.getenv("GH_TOKEN")
CACHE_DIR = os.getenv("CACHE_DIR", "cache_vibe")

os.makedirs(CACHE_DIR, exist_ok=True)

# OpenAI client
client = AsyncOpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")

# Telegram
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dispatcher = Dispatcher()
router = Router()


def hash_url(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def load_cache(url: str) -> dict:
    path = f"{CACHE_DIR}/{hash_url(url)}.json"
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_cache(url: str, data: dict):
    path = f"{CACHE_DIR}/{hash_url(url)}.json"
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def fetch_page(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            return await resp.text()


async def scrape_tool_page(url: str) -> dict:
    html = await fetch_page(url)
    soup = BeautifulSoup(html, "html.parser")

    title = soup.find("h1")
    title_text = title.get_text(strip=True) if title else "No title"

    description = soup.find("meta", attrs={"name": "description"})
    desc_text = description.get("content", "") if description else ""

    h2s = soup.find_all("h2")
    sections = []
    for h2 in h2s[:5]:
        text = h2.get_text(strip=True)
        next_siblings = []
        sibling = h2.find_next_sibling()
        while sibling and sibling.name not in ("h2", "h3"):
            next_siblings.append(sibling.get_text(strip=True))
            sibling = sibling.find_next_sibling()
        sections.append(f"## {text}\n" + "\n".join(next_siblings[:3]))

    return {
        "url": url,
        "title": title_text,
        "description": desc_text,
        "sections": sections,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


async def check_github_updates():
    if not GH_TOKEN:
        logger.info("GH_TOKEN not set, skipping GitHub updates")
        return

    headers = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    async with aiohttp.ClientSession() as session:
        # Check for new issues
        async with session.get("https://api.github.com/repos/user/repo/issues?state=open&per_page=5", headers=headers) as resp:
            if resp.status == 200:
                issues = await resp.json()
                for issue in issues:
                    logger.info(f"New issue: {issue['title']}")


async def post_to_telegram(message: str):
    try:
        await bot.send_message(chat_id=CHANNEL_ID, text=message)
        logger.info(f"Posted to Telegram: {CHANNEL_ID}")
    except Exception as e:
        logger.error(f"Telegram error: {e}")


@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Hello! I scout vibe coding tools.")


def main():
    # Main scouting loop
    tools = [
        "https://example-tool-1.dev",
        "https://example-tool-2.dev",
    ]

    for url in tools:
        cache = load_cache(url)
        if cache and (datetime.now(timezone.utc) - datetime.fromisoformat(cache["scraped_at"])).seconds < 3600:
            logger.info(f"Skipping {url} (cached)")
            continue

        try:
            data = asyncio.run(scrape_tool_page(url))
            save_cache(url, data)
            logger.info(f"Scraped: {data['title']}")
        except Exception as e:
            logger.error(f"Error scraping {url}: {e}")

    # Post summary
    summary = "Scouting complete."
    asyncio.run(post_to_telegram(summary))

    # Check GitHub
    asyncio.run(check_github_updates())


if __name__ == "__main__":
    main()
