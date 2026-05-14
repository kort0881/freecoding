#!/usr/bin/env python3
"""
Vibe Coding Scout v2.1 – 1 пост за запуск, только качественные инструменты и статьи.
"""

import os, json, asyncio, time, hashlib, html, logging, re
from typing import Optional
import aiohttp
from bs4 import BeautifulSoup
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from groq import Groq
import feedparser

# ========================= ЛОГИРОВАНИЕ =========================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger("VibeScout")

# ========================= КОНФИГ =========================
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
PRODUCT_HUNT_TOKEN = os.getenv("PRODUCT_HUNT_TOKEN", "")
POST_MODE = os.getenv("POST_MODE", "products")   # products или articles

CACHE_DIR = os.getenv("CACHE_DIR", "cache_vibe")
os.makedirs(CACHE_DIR, exist_ok=True)

STATE_FILE = os.path.join(CACHE_DIR, "state_vibe.json")
ARTICLES_STATE_FILE = os.path.join(CACHE_DIR, "articles_state.json")

groq_client = Groq(api_key=GROQ_API_KEY)

# ========================= RATE LIMITER =========================
class GroqRateLimiter:
    def __init__(self, max_rpm: int = 18):
        self.max_rpm = max_rpm
        self.requests = []

    async def wait_if_needed(self):
        now = time.time()
        self.requests = [t for t in self.requests if now - t < 60]
        if len(self.requests) >= self.max_rpm:
            wait = 60 - (now - self.requests[0]) + 1
            logger.info(f"⏳ Rate limit → ждём {wait:.1f}с")
            await asyncio.sleep(wait)
            self.requests = [t for t in self.requests if now - t < 60]
        self.requests.append(now)
        await asyncio.sleep(0.5)

rate_limiter = GroqRateLimiter()

# ========================= ИСТОЧНИКИ =========================
GITHUB_QUERIES = [
    "vibe coding", "ai code generator", "bolt.new alternative", 
    "lovable alternative", "cursor ai", "windsurf editor", 
    "ai website builder", "claude code", "no-code ai"
]

SERVICE_PAGES = [
    {"name": "Bolt.new", "url": "https://bolt.new"},
    {"name": "Lovable", "url": "https://lovable.dev"},
    {"name": "v0 by Vercel", "url": "https://v0.dev"},
    {"name": "Replit AI", "url": "https://replit.com"},
    {"name": "Google AI Studio", "url": "https://aistudio.google.com"},
]

RSS_FEEDS = [
    "https://habr.com/ru/rss/hub/ai/?limit=20",
    "https://habr.com/ru/rss/hub/programming/?limit=20",
    "https://habr.com/ru/rss/hub/nocode/?limit=20",
]

FORUM_RSS = [
    "https://www.reddit.com/r/cursor/new/.rss",
    "https://www.reddit.com/r/vibecoding/new/.rss",
    "https://www.reddit.com/r/nocode/new/.rss",
    "https://www.reddit.com/r/LocalLLaMA/new/.rss",
    "https://www.reddit.com/r/ChatGPTCoding/new/.rss",
    "https://hnrss.org/newest?q=bolt.new",
    "https://hnrss.org/newest?q=lovable",
    "https://hnrss.org/newest?q=cursor+ai",
]

DEVTO_RSS = "https://dev.to/feed/tag/ai"

# ========================= STATE =========================
class State:
    def __init__(self, filepath):
        self.filepath = filepath
        self.data = {"posted_ids": {}, "recent_titles": []}
        self._load()

    def _load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    self.data.update(json.load(f))
            except: pass

    def save(self):
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def is_posted(self, uid: str) -> bool:
        return uid in self.data["posted_ids"]

    def mark_posted(self, uid: str, title: str):
        self.data["posted_ids"][uid] = int(time.time())
        self.data["recent_titles"].append(title)
        if len(self.data["recent_titles"]) > 150:
            self.data["recent_titles"] = self.data["recent_titles"][-150:]
        self.save()

product_state = State(STATE_FILE)
article_state = State(ARTICLES_STATE_FILE)

# ========================= СБОР ПРОДУКТОВ =========================
async def fetch_github_repos(session):
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    repos = []
    for query in GITHUB_QUERIES:
        date = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 25*86400))
        url = f"https://api.github.com/search/repositories?q={query}+is:public+created:>={date}&sort=stars&order=desc&per_page=5"
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200: continue
                data = await resp.json()
                for item in data.get("items", []):
                    uid = f"gh_{item['id']}"
                    if product_state.is_posted(uid): continue

                    # Фильтры от мусора
                    desc = (item.get("description") or "").lower()
                    lang = (item.get("language") or "").lower()
                    topics = [t.lower() for t in item.get("topics", [])]

                    # Языки не для веба/ИИ без явного AI-топика
                    if lang in ["rust","c++","c","swift","kotlin","dart","objective-c","scala"]:
                        if not any(t in ["ai","llm","machine-learning","deep-learning","gpt","openai"] for t in topics):
                            continue

                    # Ключевые слова AI/вайб
                    if not any(kw in desc for kw in ["ai","llm","gpt","openai","copilot","claude","no-code","low-code","prompt","coding"]):
                        continue

                    # Размер README > 300 байт
                    try:
                        async with session.get(f"https://api.github.com/repos/{item['full_name']}/readme", headers=headers) as rr:
                            if rr.status == 200:
                                readme = await rr.json()
                                if readme.get("size",0) < 300: continue
                    except: pass

                    repos.append({
                        "uid": uid,
                        "name": item["full_name"],
                        "description": item.get("description", ""),
                        "stars": item["stargazers_count"],
                        "url": item["html_url"],
                    })
        except Exception as e:
            logger.warning(f"GitHub {query}: {e}")
        await asyncio.sleep(1.2)
    return repos

async def fetch_service_free_info(session):
    results = []
    headers = {"User-Agent": "VibeScout/2.1"}
    for s in SERVICE_PAGES:
        uid = f"sv_{hashlib.md5(s['url'].encode()).hexdigest()[:12]}"
        if product_state.is_posted(uid): continue
        try:
            async with session.get(s["url"], headers=headers, timeout=12) as resp:
                if resp.status == 200:
                    text = (await resp.text()).lower()
                    if any(x in text for x in ["free tier","start for free","free plan","no credit card"]):
                        results.append({
                            "uid": uid,
                            "name": s["name"],
                            "description": "Бесплатный тариф подтверждён",
                            "url": s["url"],
                            "stars": 0,
                        })
        except: pass
    return results

async def fetch_product_hunt(session):
    if not PRODUCT_HUNT_TOKEN: return []
    query = """
    query($after: DateTime!) {
      posts(order: VOTES, first: 20, postedAfter: $after, topic: "ai-coding") {
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
    headers = {"Authorization": f"Bearer {PRODUCT_HUNT_TOKEN}", "Content-Type": "application/json"}
    after = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7*86400))
    payload = {"query": query, "variables": {"after": after}}
    tools = []
    try:
        async with session.post("https://api.producthunt.com/v2/api/graphql", json=payload, headers=headers, timeout=20) as resp:
            if resp.status != 200: return []
            data = await resp.json()
            posts = data.get("data",{}).get("posts",{}).get("edges",[])
            for edge in posts:
                node = edge["node"]
                if not node.get("isFree", False): continue
                uid = f"ph_{node['id']}"
                if product_state.is_posted(uid): continue
                topics = [t["name"].lower() for t in node.get("topics", [])]
                if any(t in topics for t in ["vibe-coding","ai-coding","no-code"]):
                    tools.append({
                        "uid": uid,
                        "name": node["name"],
                        "description": node["tagline"],
                        "stars": node["votesCount"],
                        "url": node["url"],
                    })
    except Exception as e:
        logger.warning(f"Product Hunt: {e}")
    return tools

# ========================= СБОР СТАТЕЙ =========================
async def fetch_all_articles(session):
    articles = []

    # Habr
    for url in RSS_FEEDS:
        try:
            async with session.get(url, timeout=15) as r:
                if r.status == 200:
                    feed = feedparser.parse(await r.text())
                    for e in feed.entries:
                        link = e.get("link")
                        if not link: continue
                        uid = hashlib.md5(link.encode()).hexdigest()[:16]
                        if article_state.is_posted(uid): continue
                        articles.append({
                            "uid": uid,
                            "title": e.get("title",""),
                            "summary": re.sub(r'<[^>]+>', '', e.get("summary","")),
                            "link": link,
                            "source": "Habr"
                        })
        except: pass

    # Форумы
    for url in FORUM_RSS:
        try:
            async with session.get(url, timeout=15) as r:
                if r.status == 200:
                    feed = feedparser.parse(await r.text())
                    for e in feed.entries[:10]:
                        link = e.get("link")
                        if not link: continue
                        uid = hashlib.md5(link.encode()).hexdigest()[:16]
                        if article_state.is_posted(uid): continue
                        summary = re.sub(r'<[^>]+>', '', e.get("summary",""))
                        if any(k in (e.title + summary).lower() for k in ["cursor","bolt","lovable","vibe","ai coding","no-code"]):
                            articles.append({
                                "uid": uid,
                                "title": e.title,
                                "summary": summary[:950],
                                "link": link,
                                "source": "Reddit" if "reddit" in link else "HN"
                            })
        except: pass

    # dev.to
    try:
        async with session.get(DEVTO_RSS, timeout=15) as r:
            if r.status == 200:
                feed = feedparser.parse(await r.text())
                for e in feed.entries[:8]:
                    link = e.get("link")
                    if not link: continue
                    uid = hashlib.md5(link.encode()).hexdigest()[:16]
                    if article_state.is_posted(uid): continue
                    articles.append({
                        "uid": uid,
                        "title": e.title,
                        "summary": re.sub(r'<[^>]+>', '', e.get("summary","")),
                        "link": link,
                        "source": "dev.to"
                    })
    except: pass

    return articles

# ========================= АНАЛИЗ =========================
async def analyze_product(tool: dict) -> Optional[dict]:
    await rate_limiter.wait_if_needed()
    system = (
        "Ты — строгий куратор канала о БЕСПЛАТНЫХ AI-инструментах для вайб-кодинга.\n"
        "Отклоняй если: не AI, не генерирует код, платный, без документации.\n"
        "Верни ТОЛЬКО JSON с полями: usefulness, innovation, community, free_confidence (1-10), summary (до 320 символов на русском).\n"
        "Если не подходит — reject: true."
    )
    user = f"Инструмент: {tool['name']}\nОписание: {tool.get('description','')}\nURL: {tool['url']}"
    try:
        resp = await asyncio.to_thread(
            lambda: groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role":"system","content":system},{"role":"user","content":user}],
                response_format={"type":"json_object"},
                temperature=0.4,
                max_tokens=500,
            )
        )
        data = json.loads(resp.choices[0].message.content)
        if data.get("reject"): return None
        data["name"] = tool["name"]
        data["url"] = tool["url"]
        data["stars"] = tool.get("stars",0)
        return data
    except Exception as e:
        logger.warning(f"analyze_product error: {e}")
        return None

async def analyze_article(article: dict) -> Optional[dict]:
    await rate_limiter.wait_if_needed()
    system = (
        "Ты — редактор Telegram-канала о вайб-кодинге (создание ПО через AI).\n"
        "Преврати материал в живой, полезный пост на русском (1700-3200 символов).\n"
        "Для обсуждений выдели лучшие советы и сравнения инструментов.\n"
        "Если тема не про вайб-кодинг — верни reject: true.\n"
        "Ответ строго JSON: reject (bool), post_text (string)."
    )
    user = f"[{article['source']}] {article['title']}\n{article['summary'][:1100]}\nСсылка: {article['link']}"
    try:
        resp = await asyncio.to_thread(
            lambda: groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role":"system","content":system},{"role":"user","content":user}],
                response_format={"type":"json_object"},
                temperature=0.75,
                max_tokens=2300,
            )
        )
        data = json.loads(resp.choices[0].message.content)
        if data.get("reject"): return None
        text = data.get("post_text")
        if not text: return None
        text += f"\n\n<a href='{article['link']}'>Источник → {article['source']}</a>"
        return {"post_text": text}
    except Exception as e:
        logger.warning(f"analyze_article error: {e}")
        return None

def format_product_post(tools):
    if not tools: return ""
    tools = sorted(tools, key=lambda x: x.get("usefulness",0) + x.get("innovation",0), reverse=True)
    lines = ["🛠️ <b>Свежие бесплатные AI-инструменты для вайб-кодинга</b>\n"]
    for t in tools[:5]:
        lines.append(
            f"▸ <a href='{t['url']}'>{html.escape(t['name'])}</a> ⭐{t.get('stars',0)}\n"
            f"{t.get('summary','')}\n"
            f"👍 {t.get('usefulness','?')}/10   💸 {t.get('free_confidence','?')}/10\n"
        )
    lines.append("\n#vibecoding #ai #бесплатно")
    return "\n".join(lines)

# ========================= ОТПРАВКА =========================
async def send_telegram(text: str):
    bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await bot.send_message(CHANNEL_ID, text, disable_web_page_preview=False)
        logger.info("✅ Пост отправлен в канал")
    except Exception as e:
        logger.error(f"Telegram error: {e}")
    finally:
        await bot.session.close()

# ========================= MAIN =========================
async def main():
    logger.info(f"🚀 Vibe Coding Scout запущен в режиме: {POST_MODE}")

    async with aiohttp.ClientSession() as session:
        if POST_MODE == "products":
            github = await fetch_github_repos(session)
            services = await fetch_service_free_info(session)
            ph = await fetch_product_hunt(session)
            all_tools = github + services + ph

            # дедупликация по uid
            seen = set()
            unique_tools = []
            for t in all_tools:
                if t["uid"] not in seen:
                    seen.add(t["uid"])
                    unique_tools.append(t)

            logger.info(f"Кандидатов в инструменты: {len(unique_tools)}")

            approved = []
            for tool in unique_tools:
                analysis = await analyze_product(tool)
                if analysis:
                    approved.append(analysis)
                    product_state.mark_posted(tool["uid"], tool["name"])
                await asyncio.sleep(0.6)  # дополнительный щадящий интервал

            if approved:
                post = format_product_post(approved)
                if post:
                    await send_telegram(post)
            else:
                logger.info("Нет достойных инструментов.")

        elif POST_MODE == "articles":
            articles = await fetch_all_articles(session)
            seen = set()
            unique = [a for a in articles if not (a["uid"] in seen or seen.add(a["uid"]))]

            logger.info(f"📰 Найдено материалов: {len(unique)}")

            # Публикуем ТОЛЬКО ОДИН пост
            published = False
            for a in unique:
                if published: break
                result = await analyze_article(a)
                if result:
                    await send_telegram(result["post_text"])
                    article_state.mark_posted(a["uid"], a["title"])
                    published = True
                else:
                    article_state.mark_posted(a["uid"], a["title"])   # чтобы не перепроверять

            if not published:
                logger.info("Подходящих статей не найдено.")
        else:
            logger.error(f"Неизвестный режим: {POST_MODE}")

if __name__ == "__main__":
    asyncio.run(main())
