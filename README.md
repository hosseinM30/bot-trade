# Telegram Paper Trader

This is a serverless-ish deployment of the uploaded Crypto Paper Trader:
- 5 coins: BTC, ETH, SOL, BNB, XRP
- $22 virtual cash per coin
- SMA 10/30 crossover
- trend filter
- RSI 45-68
- volume confirmation
- ATR stop/target
- trailing stop
- closed candles only
- fee 0.10%
- slippage 0.05%

Telegram controls:
- ▶️ شروع
- ⏹ توقف
- 💰 موجودی
- 📊 وضعیت
- 📜 معاملات اخیر

GitHub Actions runs the worker every 5 minutes. It does NOT place real Binance orders.

## Setup
1. Create a bot with @BotFather using /newbot.
2. Create a GitHub repository and upload all files in this folder.
3. In GitHub: Settings -> Secrets and variables -> Actions -> New repository secret:
   - TELEGRAM_BOT_TOKEN = token from BotFather
   - OWNER_CHAT_ID = your Telegram chat ID
4. Enable Actions if needed.
5. Run the workflow once manually from Actions -> Paper Trader Bot -> Run workflow.
6. Open your bot in Telegram and press Start.
