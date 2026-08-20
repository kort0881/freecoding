#!/usr/bin/env python3
"""
Vibe Coding Scout v4.0 — адаптировано под оригинальный Groq API
Используется модель openai/gpt-oss-120b (через Groq SDK).
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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("VibeScout")

# ========================= КОНФИГ =========================
def get_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        logger.error(f"❌ Missing env: {name}")
        raise SystemExit(1)
    return val

GROQ_API_KEY       = get_env("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = get_env("TELEGRAM_BOT_TOKEN")
CHANNEL_ID         = get_env("CHANNEL_ID")
GITHUB_TOKEN       = os.getenv("GITHUB_TOKEN", "")
PRODUCT_HUNT_TOKEN = os.getenv("PRODUCT_HUNT_TOKEN", "")

POST_MODE = os.getenv("POST_MODE", "auto")

CACHE_DIR  = os.getenv("CACHE_DIR", "cache_vibe")
os.makedirs(CACHE_DIR, exist_ok=True)

STATE_FILE          = os.path.join(CACHE_DIR, "state_vibe.json")
ARTICLES_STATE_FILE = os.path.join(CACHE_DIR, "articles_state.json")
MODE_FILE           = os.path.join(CACHE_DIR, "last_mode.json")

# ========================= СОЗДАЁМ КЛИЕНТ GROQ =========================
groq_client = Groq(api_key=GROQ_API_KEY)

# ========================= ЧЕРЕДОВАНИЕ =========================
def get_current_mode() -> str:
    if POST_MODE != "auto":
        return POST_MODE
    last = "articles"
    if os.path.exists(MODE_FILE):
        try:
            with open(MODE_FILE) as f:
                last = json.load(f).get("last_mode", "articles")
        except Exception:
            pass
    current = "products" if last == "articles" else "articles"
    with open(MODE_FILE, "w") as f:
        json.dump({"last_mode": current, "ts": int(time.time())}, f)
    logger.info(f"🔄 Чередование: {last} → {current}")
    return current

# ========================= RATE LIMITER =========================
class GroqRateLimiter:
    def __init__(self, max_rpm: int = 18):
        self.max_rpm = max_rpm
        self.requests: list[float] = []

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

# ========================= ИСТОЧНИКИ — СТАТЬИ =========================
HABR_RSS = [
    "https://habr.com/ru/rss/hub/ai/?limit=20",
    "https://habr.com/ru/rss/hub/programming/?limit=20",
    "https://habr.com/ru/rss/hub/nocode/?limit=20",
    "https://habr.com/ru/rss/hub/open_source/?limit=20",
    "https://habr.com/ru/rss/best/weekly/?limit=15",
]

HN_RSS = [
    "https://hnrss.org/newest?q=vibe+coding&points=10",
    "https://hnrss.org/newest?q=ai+coding&points=10",
    "https://hnrss.org/newest?q=bolt.new&points=5",
    "https://hnrss.org/newest?q=lovable&points=5",
    "https://hnrss.org/newest?q=cursor+ai&points=5",
    "https://hnrss.org/newest?q=llm+code&points=10",
    "https://hnrss.org/newest?q=no-code+ai&points=5",
    "https://hnrss.org/newest?q=claude+code&points=5",
]

EXTRA_ARTICLE_RSS = [
    ("https://simonwillison.net/atom/everything/",   "Simon Willison", True),
    ("https://venturebeat.com/category/ai/feed/",    "VentureBeat AI", True),
    ("https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "The Verge AI", True),
    ("https://medium.com/feed/tag/vibe-coding",      "Medium",         True),
    ("https://medium.com/feed/tag/ai-coding",        "Medium",         True),
    ("https://medium.com/feed/tag/llm",              "Medium",         True),
    ("https://openai.com/news/rss.xml",              "OpenAI",         True),
    ("https://github.blog/feed/",                    "GitHub Blog",    True),
    ("https://blog.replit.com/rss.xml",              "Replit Blog",    True),
    ("https://raw.githubusercontent.com/Olshansk/rss-feeds/refs/heads/main/feeds/feed_windsurf_blog.xml",
     "Windsurf Blog", True),
    ("https://raw.githubusercontent.com/Olshansk/rss-feeds/refs/heads/main/feeds/feed_windsurf_changelog.xml",
     "Windsurf Changelog", True),
    ("https://raw.githubusercontent.com/Olshansk/rss-feeds/refs/heads/main/feeds/feed_the_batch.xml",
     "The Batch", True),
]

DEVTO_RSS    = "https://dev.to/feed/tag/ai"
LOBSTERS_RSS = "https://lobste.rs/t/ai.rss"

# ========================= ИСТОЧНИКИ — ПРОДУКТЫ =========================
GITHUB_QUERIES = [
    "vibe coding",
    "ai code generator",
    "bolt.new alternative",
    "lovable alternative",
    "cursor ai",
    "windsurf editor",
    "ai website builder",
    "claude code",
    "no-code ai",
    "llm coding assistant",
]

SERVICE_PAGES = [
    {"name": "Bolt.new",        "url": "https://bolt.new"},
    {"name": "Lovable",         "url": "https://lovable.dev"},
    {"name": "v0 by Vercel",    "url": "https://v0.dev"},
    {"name": "Replit AI",       "url": "https://replit.com"},
    {"name": "Google AI Studio","url": "https://aistudio.google.com"},
]

# ========================= КАНОНИЧЕСКИЙ ID =========================
def canonical_id(tool: dict) -> str:
    url  = tool.get("url", "")
    name = tool.get("name", "").lower()
    gh_match = re.search(r"github\.com/([^/?#]+/[^/?#]+)", url)
    if gh_match:
        return f"github_{gh_match.group(1).lower()}"
    if "/" in name and "." not in name and len(name.split("/")) == 2:
        return f"github_{name.lower()}"
    domain_match = re.search(r"(?:https?://)?([^/?#]+)", url)
    if domain_match:
        domain = domain_match.group(1).replace("www.", "")
        return f"{domain}_{name[:50]}"
    return tool.get("uid", name)

# ========================= STATE =========================
class State:
    def __init__(self, filepath):
        self.filepath = filepath
        self.data: dict = {"posted_ids": {}, "recent_titles": []}
        self._load()

    def _load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    self.data.update(json.load(f))
            except Exception:
                pass

    def save(self):
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def is_posted(self, uid: str) -> bool:
        return uid in self.data["posted_ids"]

    def mark_posted(self, uid: str, title: str, approved: bool = True):
        self.data["posted_ids"][uid] = {"ts": int(time.time()), "approved": approved}
        if approved:
            self.data["recent_titles"].append(title)
            if len(self.data["recent_titles"]) > 200:
                self.data["recent_titles"] = self.data["recent_titles"][-200:]
        self.save()

product_state = State(STATE_FILE)
article_state = State(ARTICLES_STATE_FILE)

# ========================= СБОР СТАТЕЙ =========================
ARTICLE_KEYWORDS = [
    "cursor", "bolt", "lovable", "vibe", "ai coding", "no-code", "no code",
    "llm", "gpt", "claude", "copilot", "code generation", "ai-assisted",
    "vibe-coding", "neural", "generative", "code assistant", "windsurf",
    "replit", "ai tool", "ai editor", "ai agent", "coding agent",
]

async def _parse_rss(session: aiohttp.ClientSession, url: str, source: str,
                     keyword_filter: bool = False) -> list[dict]:
    articles: list[dict] = []
    headers = {"User-Agent": "VibeScout/4.0"}
    try:
        async with session.get(url, headers=headers, timeout=15) as r:
            if r.status != 200:
                logger.debug(f"RSS {source} → HTTP {r.status}")
                return []
            feed = feedparser.parse(await r.text())
            for e in feed.entries[:15]:
                link = e.get("link")
                if not link:
                    continue
                uid = hashlib.md5(link.encode()).hexdigest()[:16]
                if article_state.is_posted(uid):
                    continue
                title   = e.get("title", "")
                summary = re.sub(r"<[^>]+>", "", e.get("summary", ""))[:1200]
                combined = (title + " " + summary).lower()
                if keyword_filter and not any(k in combined for k in ARTICLE_KEYWORDS):
                    continue
                articles.append({
                    "uid":     uid,
                    "title":   title,
                    "summary": summary,
                    "link":    link,
                    "source":  source,
                })
    except Exception as e:
        logger.warning(f"RSS [{source}]: {e}")
    return articles

async def fetch_all_articles(session: aiohttp.ClientSession) -> list[dict]:
    tasks = []
    for url in HABR_RSS:
        tasks.append(_parse_rss(session, url, "Habr", keyword_filter=False))
    for url in HN_RSS:
        tasks.append(_parse_rss(session, url, "HN", keyword_filter=True))
    tasks.append(_parse_rss(session, DEVTO_RSS,    "dev.to",   keyword_filter=True))
    tasks.append(_parse_rss(session, LOBSTERS_RSS, "Lobsters", keyword_filter=True))
    for url, src, kw_filter in EXTRA_ARTICLE_RSS:
        tasks.append(_parse_rss(session, url, src, keyword_filter=kw_filter))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    seen: set[str] = set()
    all_articles: list[dict] = []
    for batch in results:
        if isinstance(batch, Exception):
            continue
        for a in batch:
            if a["uid"] not in seen:
                seen.add(a["uid"])
                all_articles.append(a)

    non_habr = [a for a in all_articles if a["source"] != "Habr"]
    habr     = [a for a in all_articles if a["source"] == "Habr"]

    mixed: list[dict] = []
    i, j, nh_per_h = 0, 0, 2
    while i < len(non_habr) or j < len(habr):
        for _ in range(nh_per_h):
            if i < len(non_habr):
                mixed.append(non_habr[i]); i += 1
        if j < len(habr):
            mixed.append(habr[j]); j += 1

    src_counts: dict[str, int] = {}
    for a in mixed:
        src_counts[a["source"]] = src_counts.get(a["source"], 0) + 1
    logger.info(f"📰 Статей всего: {len(mixed)} | {src_counts}")
    return mixed

# ========================= СБОР ПРОДУКТОВ — GITHUB =========================
async def fetch_github_repos(session: aiohttp.ClientSession) -> list[dict]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    repos: list[dict] = []
    for query in GITHUB_QUERIES:
        date = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 25 * 86400))
        url  = (
            f"https://api.github.com/search/repositories"
            f"?q={query}+is:public+created:>={date}&sort=stars&order=desc&per_page=5"
        )
        try:
            async with session.get(url, headers=headers, timeout=15) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
                for item in data.get("items", []):
                    uid  = f"gh_{item['id']}"
                    if product_state.is_posted(uid):
                        continue
                    desc   = (item.get("description") or "").lower()
                    lang   = (item.get("language") or "").lower()
                    topics = [t.lower() for t in item.get("topics", [])]
                    if lang in ["rust","c++","c","swift","kotlin","dart","objective-c","scala"]:
                        if not any(t in ["ai","llm","machine-learning","deep-learning","gpt","openai"] for t in topics):
                            continue
                    if not any(kw in desc for kw in [
                        "ai","llm","gpt","openai","copilot","claude",
                        "no-code","low-code","prompt","coding","code generation",
                    ]):
                        continue
                    try:
                        async with session.get(
                            f"https://api.github.com/repos/{item['full_name']}/readme",
                            headers=headers, timeout=10
                        ) as rr:
                            if rr.status == 200:
                                if (await rr.json()).get("size", 0) < 300:
                                    continue
                    except Exception:
                        pass
                    repos.append({
                        "uid": uid, "name": item["full_name"],
                        "description": item.get("description", ""),
                        "stars": item["stargazers_count"], "url": item["html_url"],
                    })
        except Exception as e:
            logger.warning(f"GitHub [{query}]: {e}")
        await asyncio.sleep(1.2)
    return repos

async def fetch_github_trending(session: aiohttp.ClientSession) -> list[dict]:
    url     = "https://github.com/trending?since=weekly&spoken_language_code=en"
    headers = {"User-Agent": "VibeScout/4.0", "Accept-Language": "en-US"}
    results: list[dict] = []
    AI_KEYWORDS = ["ai","llm","gpt","openai","claude","copilot","no-code","code","cursor","vibe","bolt"]
    try:
        async with session.get(url, headers=headers, timeout=20) as resp:
            if resp.status != 200:
                logger.warning(f"GitHub Trending → HTTP {resp.status}")
                return []
            soup = BeautifulSoup(await resp.text(), "html.parser")
            for article in soup.select("article.Box-row")[:20]:
                h2 = article.select_one("h2 a")
                if not h2:
                    continue
                repo_path = h2["href"].strip("/")
                repo_url  = f"https://github.com/{repo_path}"
                uid       = f"trend_{hashlib.md5(repo_path.encode()).hexdigest()[:12]}"
                if product_state.is_posted(uid):
                    continue
                p = article.select_one("p")
                desc = p.get_text(strip=True).lower() if p else ""
                if not any(k in desc or k in repo_path.lower() for k in AI_KEYWORDS):
                    continue
                stars_el = article.select_one("a[href$='/stargazers']")
                stars = 0
                if stars_el:
                    stars_text = stars_el.get_text(strip=True).replace(",", "")
                    try:
                        stars = int(float(stars_text.replace("k", "")) * (1000 if "k" in stars_text else 1))
                    except Exception:
                        pass
                results.append({
                    "uid":         uid,
                    "name":        repo_path,
                    "description": p.get_text(strip=True) if p else "",
                    "stars":       stars,
                    "url":         repo_url,
                })
    except Exception as e:
        logger.warning(f"GitHub Trending: {e}")
    return results

async def fetch_theresanai(session: aiohttp.ClientSession) -> list[dict]:
    url     = "https://theresanaiforthat.com/rss/new-ais/"
    headers = {"User-Agent": "VibeScout/4.0"}
    CODING_TAGS = ["coding","code","developer","programming","no-code","low-code","ai editor","vibe"]
    results: list[dict] = []
    try:
        async with session.get(url, headers=headers, timeout=15) as r:
            if r.status != 200:
                return []
            feed = feedparser.parse(await r.text())
            for e in feed.entries[:20]:
                link = e.get("link")
                if not link:
                    continue
                uid = f"taat_{hashlib.md5(link.encode()).hexdigest()[:12]}"
                if product_state.is_posted(uid):
                    continue
                title   = e.get("title", "")
                summary = re.sub(r"<[^>]+>", "", e.get("summary", "")).lower()
                if not any(k in summary or k in title.lower() for k in CODING_TAGS):
                    continue
                results.append({
                    "uid":         uid,
                    "name":        title,
                    "description": re.sub(r"<[^>]+>", "", e.get("summary", ""))[:400],
                    "stars":       0,
                    "url":         link,
                })
    except Exception as e:
        logger.warning(f"There's An AI For That: {e}")
    return results

async def fetch_betalist(session: aiohttp.ClientSession) -> list[dict]:
    url     = "https://betalist.com/feed"
    headers = {"User-Agent": "VibeScout/4.0"}
    AI_TAGS = ["ai","llm","gpt","code","developer","no-code","vibe","automation","chatbot","openai"]
    results: list[dict] = []
    try:
        async with session.get(url, headers=headers, timeout=15) as r:
            if r.status != 200:
                return []
            feed = feedparser.parse(await r.text())
            for e in feed.entries[:20]:
                link = e.get("link")
                if not link:
                    continue
                uid = f"beta_{hashlib.md5(link.encode()).hexdigest()[:12]}"
                if product_state.is_posted(uid):
                    continue
                title   = e.get("title", "")
                summary = re.sub(r"<[^>]+>", "", e.get("summary", "")).lower()
                if not any(k in summary or k in title.lower() for k in AI_TAGS):
                    continue
                results.append({
                    "uid":         uid,
                    "name":        title,
                    "description": re.sub(r"<[^>]+>", "", e.get("summary", ""))[:400],
                    "stars":       0,
                    "url":         link,
                })
    except Exception as e:
        logger.warning(f"BetaList: {e}")
    return results

async def fetch_futurepedia(session: aiohttp.ClientSession) -> list[dict]:
    url     = "https://www.futurepedia.io/ai-tools/coding?sort=new&pricing=Free"
    headers = {"User-Agent": "VibeScout/4.0"}
    results: list[dict] = []
    try:
        async with session.get(url, headers=headers, timeout=20) as resp:
            if resp.status != 200:
                logger.warning(f"Futurepedia → HTTP {resp.status}")
                return []
            soup = BeautifulSoup(await resp.text(), "html.parser")
            for a_tag in soup.select("a[href*='/tool/']")[:20]:
                href = a_tag.get("href", "")
                if not href.startswith("http"):
                    href = f"https://www.futurepedia.io{href}"
                uid = f"fp_{hashlib.md5(href.encode()).hexdigest()[:12]}"
                if product_state.is_posted(uid):
                    continue
                name_el = a_tag.select_one("h3, h2, [class*='title'], [class*='name']")
                name    = name_el.get_text(strip=True) if name_el else a_tag.get_text(strip=True)[:60]
                desc_el = a_tag.select_one("p, [class*='desc'], [class*='tagline']")
                desc    = desc_el.get_text(strip=True)[:400] if desc_el else ""
                if not name or len(name) < 2:
                    continue
                results.append({
                    "uid":         uid,
                    "name":        name,
                    "description": desc,
                    "stars":       0,
                    "url":         href,
                })
    except Exception as e:
        logger.warning(f"Futurepedia: {e}")
    return results

async def fetch_product_hunt(session: aiohttp.ClientSession) -> list[dict]:
    if not PRODUCT_HUNT_TOKEN:
        return []
    query = """
    query($after: DateTime!) {
      posts(order: VOTES, first: 20, postedAfter: $after, topic: "ai-coding") {
        edges { node { id name tagline url votesCount topics { name } isFree } }
      }
    }
    """
    headers = {"Authorization": f"Bearer {PRODUCT_HUNT_TOKEN}", "Content-Type": "application/json"}
    after   = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7 * 86400))
    tools: list[dict] = []
    try:
        async with session.post(
            "https://api.producthunt.com/v2/api/graphql",
            json={"query": query, "variables": {"after": after}},
            headers=headers, timeout=20
        ) as resp:
            if resp.status != 200:
                return []
            data  = await resp.json()
            posts = data.get("data", {}).get("posts", {}).get("edges", [])
            for edge in posts:
                node = edge["node"]
                if not node.get("isFree", False):
                    continue
                uid    = f"ph_{node['id']}"
                if product_state.is_posted(uid):
                    continue
                topics = [t["name"].lower() for t in node.get("topics", [])]
                if any(t in topics for t in ["vibe-coding","ai-coding","no-code"]):
                    tools.append({
                        "uid":         uid,
                        "name":        node["name"],
                        "description": node["tagline"],
                        "stars":       node["votesCount"],
                        "url":         node["url"],
                    })
    except Exception as e:
        logger.warning(f"Product Hunt: {e}")
    return tools

async def fetch_service_free_info(session: aiohttp.ClientSession) -> list[dict]:
    results: list[dict] = []
    headers = {"User-Agent": "VibeScout/4.0"}
    for s in SERVICE_PAGES:
        uid = f"sv_{hashlib.md5(s['url'].encode()).hexdigest()[:12]}"
        if product_state.is_posted(uid):
            continue
        try:
            async with session.get(s["url"], headers=headers, timeout=12) as resp:
                if resp.status == 200:
                    text = (await resp.text()).lower()
                    if any(x in text for x in ["free tier","start for free","free plan","no credit card"]):
                        results.append({
                            "uid": uid, "name": s["name"],
                            "description": "Бесплатный тариф подтверждён",
                            "url": s["url"], "stars": 0,
                        })
        except Exception:
            pass
    return results

# ========================= АНАЛИЗ (через Groq) =========================
async def analyze_product(tool: dict) -> Optional[dict]:
    await rate_limiter.wait_if_needed()
    system = (
        "Ты — строгий куратор канала о БЕСПЛАТНЫХ AI-инструментах для вайб-кодинга.\n"
        "Отклоняй если: не AI, не генерирует/помогает писать код, платный, без документации.\n"
        "Верни ТОЛЬКО JSON: usefulness, innovation, community, free_confidence (1-10), "
        "summary (до 320 символов на русском).\n"
        "Не подходит — {\"reject\": true}."
    )
    user = f"Инструмент: {tool['name']}\nОписание: {tool.get('description','')}\nURL: {tool['url']}"
    try:
        resp = await asyncio.to_thread(
            lambda: groq_client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[{"role":"system","content":system},{"role":"user","content":user}],
                response_format={"type":"json_object"},
                temperature=0.4, max_tokens=500,
            )
        )
        data = json.loads(resp.choices[0].message.content)
        if data.get("reject"):
            return None
        data.update({"name": tool["name"], "url": tool["url"], "stars": tool.get("stars",0)})
        return data
    except Exception as e:
        logger.warning(f"analyze_product: {e}")
        return None

# ========================= ИСПРАВЛЕННАЯ analyze_article =========================
async def analyze_article(article: dict) -> Optional[dict]:
    await rate_limiter.wait_if_needed()
    system = (
        "Ты — редактор Telegram-канала о вайб-кодинге.\n"
        "Сделай краткий анонс (250–500 символов) на русском: суть, 1–2 ключевых факта, практический вывод.\n"
        "Без воды, без рекламы, без призывов подписываться. Ссылку добавлять не нужно — она будет в конце.\n"
        "Если тема не про AI-разработку/вайб-кодинг — {\"reject\": true}.\n"
        "Ответ строго JSON: {\"reject\": bool, \"post_text\": string}."
    )
    user = (
        f"[{article['source']}] {article['title']}\n"
        f"{article['summary'][:1100]}\n"
        f"Ссылка: {article['link']}"
    )
    try:
        resp = await asyncio.to_thread(
            lambda: groq_client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[{"role":"system","content":system},{"role":"user","content":user}],
                response_format={"type":"json_object"},
                temperature=0.75,
                max_tokens=700,   # уменьшено с 2300
            )
        )
        data = json.loads(resp.choices[0].message.content)
        if data.get("reject"):
            return None
        text = data.get("post_text")
        if not text:
            return None
        text += f"\n\n<a href='{article['link']}'>Источник → {article['source']}</a>"
        return {"post_text": text}
    except Exception as e:
        logger.warning(f"analyze_article: {e}")
        return None

# ========================= ФОРМАТИРОВАНИЕ =========================
def format_product_post(tools: list[dict]) -> str:
    if not tools:
        return ""
    tools = sorted(tools,
                   key=lambda x: x.get("usefulness",0) + x.get("innovation",0),
                   reverse=True)
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
    if not text:
        logger.warning("⚠️ Текст пуст, отправка пропущена")
        return

    MAX_LEN = 4090
    if len(text) > MAX_LEN:
        logger.warning(f"⚠️ Пост слишком длинный ({len(text)} символов), обрезаем до {MAX_LEN}")
        text = text[:MAX_LEN] + "…"

    bot = Bot(token=TELEGRAM_BOT_TOKEN,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await bot.send_message(CHANNEL_ID, text, disable_web_page_preview=False)
        logger.info("✅ Пост отправлен")
    except Exception as e:
        logger.error(f"Telegram error: {e}")
    finally:
        await bot.session.close()

# ========================= MAIN =========================
async def main():
    mode = get_current_mode()
    logger.info(f"🚀 Vibe Coding Scout v4.0 (Groq SDK) | режим: {mode}")

    async with aiohttp.ClientSession() as session:

        # ─────────── PRODUCTS ───────────
        if mode == "products":
            github   = await fetch_github_repos(session)
            trending = await fetch_github_trending(session)
            services = await fetch_service_free_info(session)
            ph       = await fetch_product_hunt(session)
            taat     = await fetch_theresanai(session)
            beta     = await fetch_betalist(session)
            futura   = await fetch_futurepedia(session)

            all_tools = github + trending + services + ph + taat + beta + futura

            seen_cid: set[str] = set()
            unique_tools: list[dict] = []
            for t in all_tools:
                cid = canonical_id(t)
                if cid not in seen_cid:
                    seen_cid.add(cid)
                    unique_tools.append(t)

            src_label = {
                "gh_": "GitHub Search", "trend_": "Trending",
                "sv_": "Services", "ph_": "Product Hunt",
                "taat_": "TAAT", "beta_": "BetaList", "fp_": "Futurepedia",
            }
            counts: dict[str, int] = {}
            for t in unique_tools:
                for pref, label in src_label.items():
                    if t["uid"].startswith(pref):
                        counts[label] = counts.get(label, 0) + 1
                        break
            logger.info(f"Кандидатов: {len(unique_tools)} | {counts}")

            approved: list[dict] = []
            for tool in unique_tools:
                analysis = await analyze_product(tool)
                if analysis:
                    approved.append(analysis)
                    product_state.mark_posted(tool["uid"], tool["name"], approved=True)
                else:
                    product_state.mark_posted(tool["uid"], tool["name"], approved=False)
                await asyncio.sleep(0.6)

            if approved:
                post = format_product_post(approved)
                if post:
                    await send_telegram(post)
            else:
                logger.info("Нет достойных инструментов.")

        # ─────────── ARTICLES ───────────
        elif mode == "articles":
            articles = await fetch_all_articles(session)

            published = False
            for a in articles:
                if published:
                    break
                result = await analyze_article(a)
                if result:
                    await send_telegram(result["post_text"])
                    article_state.mark_posted(a["uid"], a["title"], approved=True)
                    published = True
                else:
                    article_state.mark_posted(a["uid"], a["title"], approved=False)

            if not published:
                logger.info("Подходящих статей не найдено.")

        else:
            logger.error(f"Неизвестный режим: {mode}")

if __name__ == "__main__":
    asyncio.run(main())
