# 🚀 freecoding — AI-powered Vibe Coding Scout

Автоматический Telegram‑бот, который находит **бесплатные AI‑инструменты** для вайб‑кодинга,
собирает лучшие статьи с Habr, Reddit, Hacker News и dev.to,
а затем публикует один качественный пост в канал **4 раза в день**.

> 🔗 Канал проекта: [@EMC1_4](https://t.me/EMC1_4) — «FREE CODING»

---

## ✨ Особенности

- 🤖 **Два режима работы**
  - `products` — ежедневная подборка свежих бесплатных AI‑инструментов (GitHub, Product Hunt, сервисы)
  - `articles` — трижды в день глубокий обзор одной интересной статьи или обсуждения
- 🧠 **AI‑модерация через Groq (LLaMA 3.3 70B)**
  - строгая фильтрация: только AI‑инструменты, которые действительно генерируют код / приложения
  - живые, информативные описания на русском языке
- 📚 **Множество источников**
  - GitHub Search API (по 9 ключевым темам, фильтр по звёздам и README)
  - Product Hunt (бесплатные AI‑релизы)
  - Прямой парсинг популярных сервисов (Bolt.new, Lovable, v0, Replit AI, Google AI Studio)
  - RSS‑ленты: Habr (AI, программирование, no‑code), Reddit (r/cursor, r/vibecoding, r/ChatGPTCoding, r/LocalLLaMA …), Hacker News, dev.to
- 📈 **Защита от повторов** — отдельные state‑файлы для продуктов и статей
- ⚡ **Полностью автоматический запуск** через GitHub Actions (cron + workflow_dispatch)
- 🛡️ **Стабильность** — встроенный rate‑limiter для Groq, обработка ошибок API, graceful‑деградация
- 📝 **Один пост за запуск** — никакого спама, только лучшее

---

## 🏗️ Как это работает

### Архитектура
[GitHub Actions] → [vibe_scout.py]
│ │
▼ ▼
POST_MODE Сбор кандидатов
(products / (GitHub, PH, сервисы,
articles) RSS-ленты, форумы)
│ │
▼ ▼
Раз в сутки Фильтрация
(3:00 UTC) локальная + Groq
│ │
▼ ▼
Трижды в день Генерация поста
(9, 15, 21 UTC) и отправка в Telegram

text

- **Режим `products`** — запускается **1 раз в день** (workflow `vibe_products.yml`).  
  Собирает инструменты, проверяет через Groq, публикует топ‑5 в одном посте.
- **Режим `articles`** — запускается **3 раза в день** (workflow `vibe_articles.yml`).  
  Ищет свежие материалы, Groq пишет развёрнутый обзор, публикуется **строго 1 пост**.

---

## 📋 Требования

- Python 3.11+
- GitHub аккаунт (для Actions)
- Telegram‑бот и канал (бот должен быть администратором)
- API‑ключи:
  - **Groq Cloud** ([console.groq.com](https://console.groq.com)) – бесплатный тир
  - **Product Hunt** (опционально, [producthunt.com/v2/oauth/applications](https://www.producthunt.com/v2/oauth/applications))
  - **GitHub Personal Access Token** (опционально, повышает лимит API)

---

## 🚀 Быстрый старт (локальный запуск)

```bash
# 1. Клонируй репозиторий
git clone https://github.com/твой-юзер/freecoding.git
cd freecoding

# 2. Создай .env файл (пример в .env.example)
cp .env.example .env
# Заполни реальными значениями (см. ниже)

# 3. Установи зависимости
pip install -r requirements.txt

# 4. Запусти вручную
POST_MODE=products python scripts/vibe_scout.py    # подборка инструментов
POST_MODE=articles python scripts/vibe_scout.py    # обзор статьи
⚙️ Настройка GitHub Actions
Проект уже содержит два workflow‑файла, которые запускаются автоматически:

.github/workflows/vibe_products.yml — каждый день в 03:00 UTC

.github/workflows/vibe_articles.yml — каждый день в 09:00, 15:00, 21:00 UTC

1. Добавь секреты в репозиторий
Перейди в Settings → Secrets and variables → Actions → New repository secret и добавь:

Секрет	Описание
GROQ_API_KEY	API‑ключ Groq Cloud
TELEGRAM_BOT_TOKEN	Токен Telegram‑бота
CHANNEL_ID	ID или @username канала (@EMC1_4)
GITHUB_TOKEN	(необязательно) Personal Access Token GitHub
PRODUCT_HUNT_TOKEN	(необязательно) Developer Token Product Hunt
2. Запусти вручную (для проверки)
Зайди в Actions, выбери любой workflow и нажми «Run workflow».

🧪 Переменные окружения (.env)
ini
GROQ_API_KEY=gsk_...
TELEGRAM_BOT_TOKEN=8880955927:AA...
CHANNEL_ID=@EMC1_4
GITHUB_TOKEN=ghp_...
PRODUCT_HUNT_TOKEN=ph_...
CACHE_DIR=cache_vibe
CACHE_DIR — папка для хранения state‑файлов (по умолчанию cache_vibe).

GITHUB_TOKEN и PRODUCT_HUNT_TOKEN можно не указывать — скрипт продолжит работать, просто пропустит соответствующие источники.

📁 Структура репозитория
text
freecoding/
├── .github/
│   └── workflows/
│       ├── vibe_products.yml      # Ежедневный запуск продуктов
│       └── vibe_articles.yml      # Трижды в день запуск статей
├── scripts/
│   └── vibe_scout.py              # Основной скрипт
├── .env.example                   # Шаблон переменных окружения
├── requirements.txt               # Python‑зависимости
├── setup.sh                       # Быстрая установка виртуального окружения
├── .gitignore
└── README.md
👤 Автор и контакты
Канал: FREE CODING
По всем вопросам: пиши в Telegram @EMC1_4

📄 Лицензия
MIT © [kort0881]

P.S. Если проект оказался полезным — поставь звёздочку ⭐ на GitHub и подпишись на канал, там регулярно выходят свежие подборки и разборы инструментов для вайб‑кодинга.
