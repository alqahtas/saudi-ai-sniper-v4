Saudi AI Sniper V4 — Telegram upgrade

1. Replace streamlit_app.py and requirements.txt in GitHub, and add sniper_core.py + telegram_scan.py.
2. Add .github/workflows/daily-telegram.yml in that exact path.
3. Streamlit Secrets: TELEGRAM_BOT_TOKEN = "your_new_token" and TELEGRAM_SETUP_PIN = "choose_a_private_pin"
4. Open Streamlit, find Chat ID and test message.
5. In GitHub repo Settings > Secrets and variables > Actions > New repository secret, create TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.
6. Actions > Saudi AI Sniper - Daily Telegram Report > Run workflow to verify.
7. Scheduled report 15:40 KSA Sunday-Thursday; historical daily candles, not real-time.
Never upload token as file, source code, or screenshot.
