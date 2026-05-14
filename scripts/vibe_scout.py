#!/usr/bin/env python3
"""
Vibe Coding Scout – сбор бесплатных инструментов для вайб-кодинга.
Источники: GitHub Search API, прямые страницы сервисов.
Анализ: xAI Grok.
Публикация: Telegram.
"""

import os
import json
import asyncio
import re
import time
import hashlib
import html
import logging
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from openai import OpenAI

# ============ ЛОГИРОВАНИЕ ============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("VibeScout")

# ============ КОНФИГУРАЦИЯ ============
def get_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        logger.error(f"❌ Missing env: {name}")
        raise SystemExit(1)
    return val

XAI_API_KEY = get_env("XAI_API_KEY")
TELEGRAM_BOT_TOKEN = get_env("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = get_env("CHANNEL_ID")  # @channel или -100...
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")  # опционально, увеличивает лимиты API

CACHE_DIR = os.getenv("CACHE_DIR", "cache_vibe")
os.makedirs(CACHE_DIR, exist_ok=True)
STATE_FILE = os.path.join(CACHE_DIR, "state_vibe.json")

# ============ КЛИЕНТ Grok ============
xai_client = OpenAI(
    api_key=XAI_API_KEY,
    base_url="https://api.x.ai/v1",
)

# ============ ИСТОЧНИКИ ============
GITHUB_QUERIES = [
    "vibe coding", "ai code generator", "no-code ai platform",
    "bolt.new alternative", "lovable alternative", "cursor ai free",
    "windsurf editor", "github copilot free tier", "replit ai",
    "google ai studio", "ai website builder open source"
]

# Реальные сервисы для прямого парсинга (проверка бесплатности)
SERVICE_PAGES = [
    {"name": "Bolt.new", "url": "https://bolt.new"},
    {"name": "Lovable", "url": "https://lovable.dev"},
    {"name": "Replit", "url": "https://replit.com"},
    {"name": "v0 by Vercel", "url": "https://v0.dev"},
    {"name": "Google AI Studio", "url": "https://aistudio.google.com"},
]

# ============ СОСТОЯНИЕ И ДУБЛИКАТЫ ============
class State:
    def __init__(self):
        self.data = {"posted_ids": {}, "recent_titles": [], "recent_posts": []}
        self._load()

    def _load(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    self.data.update(json.load(f))
            except:
                pass

    def save(self):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def is_posted(self, uid: str) -> bool:
        return uid in self.data["posted_ids"]

    def mark_posted(self, uid: str, title: str, summary: str = ""):
        now = int(time.time())
        self.data["posted_ids"][uid] = now
        self.data["recent_titles"].append(title)
        if len(self.data["recent_titles"]) > 50:
            self.data["recent_titles"] = self.data["recent_titles"][-50:]
        self.data["recent_posts"].append({
            "title": title,
            "summary": summary[:500],
            "time": now
        })
        if len(self.data["recent_posts"]) > 30:
            self.data["recent_posts"] = self.data["recent_posts"][-30:]
        self.save()

state = State()

# ============ СБОР ДАННЫХ ============
async def fetch_github_repos(session: aiohttp.ClientSession) -> list[dict]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    repos = []
    for query in GITHUB_QUERIES:
        # Ищем только публичные репозитории, созданные за последние 30 дней
        date_filter = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 30 * 86400))
        url = (
            f"https://api.github.com/search/repositories"
            f"?q={query}+is:public+created:>={date_filter}"
            f"&sort=stars&order=desc&per_page=5"
        )
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    logger.warning(f"GitHub API status {resp.status} for query '{query}'")
                    continue
                data = await resp.json()
                for item in data.get("items", []):
                    uid = f"gh_{item['id']}"
                    if state.is_posted(uid):
                        continue
                    # Простейший фильтр: минимум 1 звезда и описание
                    if item.get("stargazers_count", 0) < 1:
                        continue
                    if not item.get("description"):
                        continue
                    repos.append({
                        "uid": uid,
                        "type": "github",
                        "name": item["full_name"],
                        "description": item["description"],
                        "stars": item["stargazers_count"],
                        "url": item["html_url"],
                        "updated_at": item["updated_at"],
                        "topics": item.get("topics", []),
                        "language": item.get("language"),
                    })
            await asyncio.sleep(1.5)  # rate limit без токена 10 запросов/мин, с токеном 30
        except Exception as e:
            logger.warning(f"GitHub search error for '{query}': {e}")
    return repos

async def fetch_service_free_info(session: aiohttp.ClientSession) -> list[dict]:
    """Проверяет наличие бесплатного тарифа на страницах сервисов."""
    results = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; VibeCodingScout/1.0)"}
    for service in SERVICE_PAGES:
        uid = f"sv_{hashlib.md5(service['url'].encode()).hexdigest()[:12]}"
        if state.is_posted(uid):
            continue
        try:
            async with session.get(service["url"], headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    continue
                text = await resp.text()
                soup = BeautifulSoup(text, "html.parser")
                page_text = soup.get_text().lower()
                # Ищем ключевые слова, указывающие на бесплатность
                free_keywords = ["free", "start for free", "free tier", "free plan", "no credit card"]
                if any(kw in page_text for kw in free_keywords):
                    # Пытаемся найти ссылку на цены
                    pricing_link = None
                    for a in soup.find_all("a", href=True):
                        if "pricing" in a["href"] or "price" in a["href"]:
                            pricing_link = a["href"]
                            if not pricing_link.startswith("http"):
                                pricing_link = service["url"].rstrip("/") + "/" + pricing_link.lstrip("/")
                            break
                    results.append({
                        "uid": uid,
                        "type": "service",
                        "name": service["name"],
                        "description": f"Бесплатный доступ подтверждён на странице {service['url']}",
                        "url": pricing_link or service["url"],
                        "stars": 0,
                        "updated_at": "live"
                    })
        except Exception as e:
            logger.warning(f"Error scraping {service['name']}: {e}")
    return results

# ============ АНАЛИЗ GROK ============
async def analyze_with_grok(tool: dict) -> Optional[dict]:
    system_prompt = (
        "Ты — эксперт по вайб-кодингу и AI-инструментам для быстрой разработки. "
        "Оцени инструмент по шкалам 1-10: usefulness, innovation, community, free_confidence. "
        "Добавь summary до 200 символов на русском. "
        "Если инструмент явно не бесплатный, не относится к теме или неработоспособен — "
        "установи 'reject: true'. Верни ТОЛЬКО JSON."
    )
    user_prompt = (
        f"Инструмент: {tool['name']}\n"
        f"Описание: {tool.get('description', '')}\n"
        f"Звёзды: {tool.get('stars', 0)}\n"
        f"Тип: {tool.get('type', '')}\n"
        f"URL: {tool['url']}"
    )
    try:
        response = await asyncio.to_thread(
            lambda: xai_client.chat.completions.create(
                model="grok-2-1212",  # актуальная модель
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
                max_tokens=300,
            )
        )
        result = json.loads(response.choices[0].message.content)
        if result.get("reject"):
            return None
        # Добавляем поля
        result["name"] = tool["name"]
        result["url"] = tool["url"]
        result["stars"] = tool.get("stars", 0)
        return result
    except Exception as e:
        logger.warning(f"Grok analysis error for {tool['name']}: {e}")
        return None

# ============ ФОРМАТ ПОСТА ============
def format_telegram_post(tools: list[dict]) -> str:
    if not tools:
        return ""
    # Сортируем по общей полезности
    tools.sort(key=lambda x: x.get("usefulness", 0) + x.get("innovation", 0), reverse=True)
    lines = ["🛠️ <b>Топ бесплатных инструментов для вайб-кодинга</b>\n"]
    for t in tools[:3]:  # не больше 3 в одном посте
        name = html.escape(t["name"])
        url = t["url"]
        summary = t.get("summary", "Быстрый AI-помощник для разработки")
        usefulness = t.get("usefulness", "?")
        free_conf = t.get("free_confidence", "?")
        stars = t.get("stars", 0)
        lines.append(
            f"• <a href='{url}'>{name}</a> (⭐ {stars})\n"
            f"  {summary}\n"
            f"  Полезность: {usefulness}/10 | Бесплатность: {free_conf}/10\n"
        )
    lines.append("\n#vibecoding #бесплатно #инструменты")
    return "\n".join(lines)

# ============ TELEGRAM ============
async def send_telegram(text: str):
    bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await bot.send_message(CHANNEL_ID, text, disable_web_page_preview=False)
        logger.info("✅ Отправлено в Telegram")
    except Exception as e:
        logger.error(f"Telegram error: {e}")
    finally:
        await bot.session.close()

# ============ ГЛАВНЫЙ ЦИКЛ ============
async def main():
    logger.info("🚀 Vibe Coding Scout started")
    async with aiohttp.ClientSession() as session:
        try:
            # Сбор источников
            github_tools = await fetch_github_repos(session)
            logger.info(f"🔍 GitHub candidates: {len(github_tools)}")
            service_tools = await fetch_service_free_info(session)
            logger.info(f"🌐 Service candidates: {len(service_tools)}")

            all_candidates = github_tools + service_tools
            if not all_candidates:
                logger.info("Нет новых инструментов.")
                return

            # Анализ Grok
            approved = []
            for tool in all_candidates:
                analysis = await analyze_with_grok(tool)
                if analysis:
                    approved.append(analysis)
                    state.mark_posted(tool["uid"], tool["name"], tool.get("description", ""))
                await asyncio.sleep(0.6)  # уважение к лимитам Grok

            logger.info(f"✅ После Grok одобрено: {len(approved)}")

            # Публикация
            if approved:
                post = format_telegram_post(approved)
                if post:
                    await send_telegram(post)
            else:
                logger.info("Ничего достойного публикации.")
        except Exception as e:
            logger.exception("Критическая ошибка в main")
        # session закроется автоматически при выходе из async with

if __name__ == "__main__":
    asyncio.run(main())
