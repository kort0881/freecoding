# freecoding – AI-powered Vibe Coding Tool Scout

Automated scouting tool for discovering and monitoring vibe coding tools.

## Setup

1. Clone the repo
2. Copy `.env.example` to `.env` and fill in your keys
3. Run `chmod +x setup.sh && ./setup.sh`
4. Run `python scripts/vibe_scout.py`

## Workflow

The GitHub Actions workflow runs every 3/9/15/21 UTC and also supports manual dispatch.

## Secrets

- XAI_API_KEY – xAI API key
- TELEGRAM_BOT_TOKEN – Telegram bot token
- CHANNEL_ID – Telegram channel ID
- GITHUB_TOKEN – GitHub personal access token (optional)
