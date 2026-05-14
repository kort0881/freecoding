#!/usr/bin/env python3
"""
Vibe Coding Scout – сбор бесплатных инструментов для вайб-кодинга.
Источники: GitHub Search API, прямые страницы сервисов, Product Hunt API.
Анализ: Groq (LLaMA 3.3 70B).
Публикация: Telegram.
"""

import os
import json
import asyncio
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
from groq import Groq

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

GROQ_API_KEY = get_env("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = get_env("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = get_env("CHANNEL_ID")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
PRODUCT_HUNT_TOKEN = os.getenv("PRODUCT_HUNT_TOKEN", "")  # опционально

CACHE_DIR = os.getenv("CACHE_DIR", "cache_vibe")
os.makedirs(CACHE_DIR, exist_ok=True)
STATE_FILE = os.path.join(CACHE_DIR, "state_vibe.json")

# ============ КЛИЕНТ Groq ============
groq_client = Groq(api_key=GROQ_API_KEY)

# ============ RATE LIMITER для Groq ============
class GroqRateLimiter:
    def __init__(self, max_rpm: int = 25):
        self.max_rpm = max_rpm
        self.requests = []

    async def wait_if_needed(self):
        now = time.time()
        self.requests = [t for t in self.requests if now - t < 60.0]
        if len(self.requests) >= self.max_rpm:
            wait = 60.0 - (now - self.requests[0]) + 1.0
            logger.info(f"⏳ Groq rate limit: waiting {wait:.1f}s")
            await asyncio.sleep(wait)
            self.requests = []
        self.requests.append(now)

rate_limiter = GroqRateLimiter(max_rpm=25)

# ============ ИСТОЧНИКИ ============
GITHUB_QUERIES = [
    "vibe coding", "ai code generator", "no-code ai platform",
    "bolt.new alternative", "lovable alternative", "cursor ai free",
    "windsurf editor", "github copilot free tier", "replit ai",
    "google ai studio", "ai website builder open source"
]

SERVICE_PAGES = [
    {"name": "Bolt.new", "url": "https://bolt.new"},
    {"name": "Lovable", "url": "https://lovable.dev"},
    {"name": "Replit", "url": "https://replit.com"},
    {"name": "v0 by Vercel", "url": "https://v0.dev"},
    {"name": "Google AI Studio", "url": "https://aistudio.google.com"},
]

# ============ СОСТОЯНИЕ ============
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
        date_filter = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 30 * 86400))
        url = (
            f"https://api.github.com/search/repositories"
            f"?q={query}+is:public+created:>={date_filter}"
            f"&sort=stars&order=desc&per_page=5"
        )
        for attempt in range(3):
            try:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 403 or resp.status == 429:
                        remaining = resp.headers.get("X-RateLimit-Remaining", "0")
                        reset_at = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
                        wait = max(reset_at - time.time() + 1, 10)
                        logger.warning(f"Rate limit hit for '{query}'. Waiting {wait:.0f}s")
                        await asyncio.sleep(wait)
                        continue
                    if resp.status != 200:
                        logger.warning(f"GitHub API status {resp.status} for query '{query}'")
                        break
                    data = await resp.json()
                    for item in data.get("items", []):
                        uid = f"gh_{item['id']}"
                        if state.is_posted(uid):
                            continue

                        # --- ФИЛЬТРЫ ---
                        language = (item.get("language") or "").lower()
                        topics = [t.lower() for t in item.get("topics", [])]
                        description = (item.get("description") or "").lower()

                        # 1. Отсеиваем не‑айтишные языки без AI‑топиков
                        non_ai_langs = ["rust", "c++", "c", "swift", "kotlin", "dart", "objective-c", "scala"]
                        if language in non_ai_langs:
                            ai_topics = ["ai", "llm", "machine-learning", "deep-learning", "artificial-intelligence", "gpt", "openai", "copilot"]
                            if not any(t in ai_topics for t in topics):
                                continue

                        # 2. Ключевые слова в описании
                        required_kw = ["ai", "llm", "gpt", "openai", "copilot", "codex", "claude", "gemini", "no-code", "low-code", "prompt", "coding"]
                        if not any(kw in description for kw in required_kw):
                            continue

                        # 3. Минимальный размер README (хотя бы 300 байт)
                        try:
                            readme_url = f"https://api.github.com/repos/{item['full_name']}/readme"
                            async with session.get(readme_url, headers=headers) as r:
                                if r.status == 200:
                                    readme = await r.json()
                                    if readme.get("size", 0) < 300:
                                        continue
                        except:
                            pass

                        # Если прошёл — добавляем
                        repos.append({
                            "uid": uid,
                            "type": "github",
                            "name": item["full_name"],
                            "description": item["description"],
                            "stars": item["stargazers_count"],
                            "url": item["html_url"],
                            "updated_at": item["updated_at"],
                            "topics": topics,
                            "language": language,
                        })
                    break
            except Exception as e:
                logger.warning(f"GitHub search error for '{query}': {e}")
                break
        await asyncio.sleep(1.5)
    return repos

async def fetch_service_free_info(session: aiohttp.ClientSession) -> list[dict]:
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
                # Более точные проверки бесплатности
                free_keywords = ["start for free", "free tier", "free plan", "no credit card", "get started free", "free forever"]
                if any(kw in page_text for kw in free_keywords):
                    # Ищем ссылку на цены
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
                        "description": f"Бесплатный тариф подтверждён: {service['url']}",
                        "url": pricing_link or service["url"],
                        "stars": 0,
                        "updated_at": "live"
                    })
        except Exception as e:
            logger.warning(f"Error scraping {service['name']}: {e}")
    return results

async def fetch_product_hunt(session: aiohttp.ClientSession) -> list[dict]:
    """Забирает посты за последние 7 дней из топиков 'ai-coding', 'vibe-coding'."""
    if not PRODUCT_HUNT_TOKEN:
        return []

    query = """
    query($postedAfter: DateTime!) {
      posts(order: VOTES, first: 20, postedAfter: $postedAfter, topic: "ai-coding") {
        edges {
          node {
            id
            name
            tagline
            url
            votesCount
            topics { name }
            isFree
          }
        }
      }
    }
    """
    headers = {
        "Authorization": f"Bearer {PRODUCT_HUNT_TOKEN}",
        "Content-Type": "application/json",
    }
    seven_days_ago = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7 * 86400))
    variables = {"postedAfter": seven_days_ago}
    payload = {"query": query, "variables": variables}

    results = []
    try:
        async with session.post("https://api.producthunt.com/v2/api/graphql",
                                headers=headers, json=payload,
                                timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            posts = data.get("data", {}).get("posts", {}).get("edges", [])
            for edge in posts:
                node = edge["node"]
                if not node.get("isFree", False):
                    continue
                uid = f"ph_{node['id']}"
                if state.is_posted(uid):
                    continue
                topics = [t["name"].lower() for t in node.get("topics", [])]
                if "vibe-coding" in topics or "ai-coding" in topics:
                    results.append({
                        "uid": uid,
                        "type": "producthunt",
                        "name": node["name"],
                        "description": node["tagline"],
                        "stars": node["votesCount"],
                        "url": node["url"],
                        "updated_at": "today",
                        "topics": topics,
                    })
    except Exception as e:
        logger.warning(f"Product Hunt error: {e}")
    return results

# ============ АНАЛИЗ GROQ ============
async def analyze_with_llm(tool: dict) -> Optional[dict]:
    await rate_limiter.wait_if_needed()

    system_prompt = (
        "Ты — строгий фильтр для Telegram-канала о БЕСПЛАТНЫХ AI-инструментах для вайб-кодинга.\n"
        "Инструмент должен ПОЗВОЛЯТЬ генерировать код, приложения, веб-сайты с помощью ИИ без ручного написания кода.\n\n"
        "СРАЗУ ОТКЛОНЯЙ (reject: true), если инструмент НЕ соответствует ХОТЯ БЫ ОДНОМУ из критериев:\n"
        "- Не относится к разработке ПО / созданию приложений\n"
        "- Не использует AI/LLM/ML для генерации кода или интерфейса\n"
        "- Явно платный (нет бесплатного тарифа, требует кредитную карту для запуска)\n"
        "- Не имеет документации (README с примерами) или последний коммит был более 6 месяцев назад\n"
        "- Это просто «Hello World» или учебный проект без реального функционала\n"
        "- Язык описания не содержит слов: AI, LLM, генерация, prompt, no‑code, low‑code, chat, assistant\n\n"
        "Если подходит — оцени ПО СТРОГОСТИ:\n"
        "- usefulness (1-10): насколько инструмент ускоряет разработку\n"
        "- innovation (1-10): оригинальность подхода\n"
        "- community (1-10): звёзды, форки, активность\n"
        "- free_confidence (1-10): насколько уверен, что есть полноценный бесплатный доступ\n"
        "Добавь summary до 200 символов на русском.\n"
        "Верни ТОЛЬКО JSON."
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
            lambda: groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
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
        result["name"] = tool["name"]
        result["url"] = tool["url"]
        result["stars"] = tool.get("stars", 0)
        return result
    except Exception as e:
        logger.warning(f"LLM analysis error for {tool['name']}: {e}")
        return None

# ============ ФОРМАТ ПОСТА ============
def format_telegram_post(tools: list[dict]) -> str:
    if not tools:
        return ""
    tools.sort(key=lambda x: x.get("usefulness", 0) + x.get("innovation", 0), reverse=True)
    lines = ["🛠️ <b>Топ бесплатных инструментов для вайб-кодинга</b>\n"]
    for t in tools[:3]:
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
            github_tools = await fetch_github_repos(session)
            logger.info(f"🔍 GitHub candidates: {len(github_tools)}")
            service_tools = await fetch_service_free_info(session)
            logger.info(f"🌐 Service candidates: {len(service_tools)}")
            ph_tools = await fetch_product_hunt(session)
            logger.info(f"🏆 Product Hunt candidates: {len(ph_tools)}")

            all_candidates = github_tools + service_tools + ph_tools
            if not all_candidates:
                logger.info("Нет новых инструментов.")
                return

            approved = []
            for tool in all_candidates:
                analysis = await analyze_with_llm(tool)
                if analysis:
                    approved.append(analysis)
                    state.mark_posted(tool["uid"], tool["name"], tool.get("description", ""))
                # rate_limiter уже управляет паузами, дополнительная задержка не нужна

            logger.info(f"✅ После Groq одобрено: {len(approved)}")

            if approved:
                post = format_telegram_post(approved)
                if post:
                    await send_telegram(post)
            else:
                logger.info("Ничего достойного публикации.")
        except Exception as e:
            logger.exception("Критическая ошибка в main")

if __name__ == "__main__":
    asyncio.run(main())
