import logging
import os
import requests
import hmac
import hashlib
import base64
import json
import time
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
BAYSE_PUBLIC_KEY = os.getenv("BAYSE_API_KEY", "")
BAYSE_SECRET_KEY = os.getenv("BAYSE_API_SECRET", "")
BAYSE_BASE_URL = os.getenv("BAYSE_BASE_URL", "https://relay.bayse.markets")
CRYPTO_ALLOC = 0.10
SPORTS_ALLOC = 0.20
SPORTS_MARGIN = 0.10
ALLOWED_USERS = [int(x) for x in os.getenv("ALLOWED_USERS", "0").split(",")]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

trade_log = []
last_crypto_trade = None
bot_active = True

def make_signature(method, path, body=""):
    timestamp = str(int(time.time()))
    body_hash = hashlib.sha256(body.encode()).hexdigest()
    payload = f"{timestamp}.{method}.{path}.{body_hash}"
    signature = base64.b64encode(
        hmac.new(BAYSE_SECRET_KEY.encode(), payload.encode(), hashlib.sha256).digest()
    ).decode()
    return timestamp, signature

def write_headers(method, path, body=""):
    timestamp, signature = make_signature(method, path, body)
    return {
        "X-Public-Key": BAYSE_PUBLIC_KEY,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
        "Content-Type": "application/json"
    }

def get_balance():
    try:
        path = "/v1/wallet/assets"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        data = r.json()
        assets = data.get("assets", [])
        for asset in assets:
            if asset.get("isLocalCurrencyAsset") == True:
                return float(asset.get("availableBalance", 0))
        if assets:
            return float(assets[0].get("availableBalance", 0))
        return 0
    except Exception as e:
        logger.error(f"Balance error: {e}")
        return 0

def get_portfolio():
    try:
        path = "/v1/pm/portfolio"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_orders():
    try:
        path = "/v1/pm/orders"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_activities():
    try:
        path = "/v1/pm/activities"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_events():
    try:
        path = "/v1/pm/events"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_trades():
    try:
        path = "/v1/pm/trades"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def place_order(event_id, market_id, side, amount):
    try:
        path = f"/v1/pm/events/{event_id}/markets/{market_id}/orders"
        body = json.dumps({"side": side, "amount": amount})
        r = requests.post(f"{BAYSE_BASE_URL}{path}", headers=write_headers("POST", path, body), data=body, timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_live_events():
    try:
        r = requests.get("https://api.sofascore.com/api/v1/sport/football/events/live", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        return r.json().get("events", [])
    except:
        return []

def get_sofascore_probability(event_id):
    try:
        r = requests.get(f"https://api.sofascore.com/api/v1/event/{event_id}/probabilities", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        return r.json().get("probabilities", {})
    except:
        return {}

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    return 100 - (100 / (1 + avg_gain / avg_loss))

def calculate_macd(prices):
    if len(prices) < 26:
        return 0, 0
    def ema(data, period):
        k = 2 / (period + 1)
        v = data[0]
        for p in data[1:]:
            v = p * k + v * (1 - k)
        return v
    return ema(prices[-26:], 12) - ema(prices[-26:], 26), ema(prices[-26:], 12)

def get_signal(prices):
    rsi = calculate_rsi(prices)
    macd, _ = calculate_macd(prices)
    if rsi < 35 and macd > 0:
        return "BUY", rsi, macd
    elif rsi > 65 and macd < 0:
        return "SELL", rsi, macd
    elif rsi < 40:
        return "WEAK_BUY", rsi, macd
    elif rsi > 60:
        return "WEAK_SELL", rsi, macd
    return "HOLD", rsi, macd

def is_allowed(user_id):
    return user_id in ALLOWED_USERS or ALLOWED_USERS == [0]

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    keyboard = [
        [InlineKeyboardButton("💰 Balance", callback_data="balance"),
         InlineKeyboardButton("📊 Portfolio", callback_data="portfolio")],
        [InlineKeyboardButton("📋 Orders", callback_data="orders"),
         InlineKeyboardButton("📜 Activities", callback_data="activities")],
        [InlineKeyboardButton("⚽ Sports Scan", callback_data="sports"),
         InlineKeyboardButton("📈 Crypto Scan", callback_data="crypto_scan")],
        [InlineKeyboardButton("📈 Events", callback_data="events"),
         InlineKeyboardButton("🔄 Trades", callback_data="trades")],
        [InlineKeyboardButton("📜 Trade Log", callback_data="log"),
         InlineKeyboardButton("▶️ Start Bot", callback_data="start_bot")],
        [InlineKeyboardButton("⏹ Stop Bot", callback_data="stop_bot")],
    ]
    await update.message.reply_text(
        "🤖 *Gowagr Bot* is online!\n\nDual-market autotrader:\n• 📈 Crypto (BTC 15m)\n• ⚽ Sports (SofaScore)\n\nChoose an action:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def balance_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Checking balance...")
    bal = get_balance()
    await update.message.reply_text(
        f"💰 *Wallet Balance*\n\n`₦{bal:,.2f}`\n\n⚽ Sports budget: `₦{bal*SPORTS_ALLOC:,.2f}`\n📈 Crypto budget: `₦{bal*CRYPTO_ALLOC:,.2f}`",
        parse_mode="Markdown"
    )

async def portfolio_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading portfolio...")
    data = get_portfolio()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    positions = data.get("positions", data.get("data", []))
    if not positions:
        await update.message.reply_text("📊 No open positions.")
        return
    msg = "📊 *Portfolio*\n\n" + "\n".join([f"• {p.get('market', p.get('name','N/A'))} | {p.get('shares', p.get('amount','N/A'))}" for p in positions[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def orders_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading orders...")
    data = get_orders()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    orders = data.get("orders", data.get("data", []))
    if not orders:
        await update.message.reply_text("📋 No open orders.")
        return
    msg = "📋 *Orders*\n\n" + "\n".join([f"• {o.get('market','N/A')} | {o.get('side','N/A')} | ₦{o.get('amount',0):,.2f}" for o in orders[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def activities_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading activities...")
    data = get_activities()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    acts = data.get("activities", data.get("data", []))
    if not acts:
        await update.message.reply_text("📜 No activities.")
        return
    msg = "📜 *Activities*\n\n" + "\n".join([f"• {a.get('type','N/A')} | {a.get('description', a.get('market','N/A'))}" for a in acts[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def events_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading events...")
    data = get_events()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    events = data.get("events", data.get("data", []))
    if not events:
        await update.message.reply_text("📈 No events.")
        return
    msg = "📈 *Events*\n\n" + "\n".join([f"• {e.get('title', e.get('name','N/A'))}" for e in events[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def trades_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading trades...")
    data = get_trades()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    trades = data.get("trades", data.get("data", []))
    if not trades:
        await update.message.reply_text("🔄 No trades.")
        return
    msg = "🔄 *Trades*\n\n" + "\n".join([f"• {t.get('market','N/A')} | {t.get('side','N/A')} | ₦{t.get('amount',0):,.2f}" for t in trades[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def log_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    if not trade_log:
        await update.message.reply_text("📜 No trades this session.")
        return
    await update.message.reply_text("📜 *Session Trades*\n\n" + "\n".join([f"• {t}" for t in trade_log[-10:]]), parse_mode="Markdown")

async def sports_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⚽ Scanning SofaScore...")
    await do_sports_scan(ctx.bot, update.effective_chat.id)

async def crypto_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("📈 Scanning crypto markets...")
    await do_crypto_scan(ctx.bot, update.effective_chat.id)

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    d = query.data
    global bot_active
    if d == "balance":
        bal = get_balance()
        await query.message.reply_text(
            f"💰 *Balance*\n\n`₦{bal:,.2f}`\n\n⚽ Sports: `₦{bal*SPORTS_ALLOC:,.2f}`\n📈 Crypto: `₦{bal*CRYPTO_ALLOC:,.2f}`",
            parse_mode="Markdown"
        )
    elif d == "portfolio":
        data = get_portfolio()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        positions = data.get("positions", data.get("data", []))
        if not positions:
            await query.message.reply_text("📊 No open positions.")
            return
        msg = "📊 *Portfolio*\n\n" + "\n".join([f"• {p.get('market', p.get('name','N/A'))} | {p.get('shares', p.get('amount','N/A'))}" for p in positions[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "orders":
        data = get_orders()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        orders = data.get("orders", data.get("data", []))
        if not orders:
            await query.message.reply_text("📋 No open orders.")
            return
        msg = "📋 *Orders*\n\n" + "\n".join([f"• {o.get('market','N/A')} | {o.get('side','N/A')} | ₦{o.get('amount',0):,.2f}" for o in orders[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "activities":
        data = get_activities()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        acts = data.get("activities", data.get("data", []))
        if not acts:
            await query.message.reply_text("📜 No activities.")
            return
        msg = "📜 *Activities*\n\n" + "\n".join([f"• {a.get('type','N/A')} | {a.get('description', a.get('market','N/A'))}" for a in acts[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "sports":
        await query.message.reply_text("⚽ Scanning...")
        await do_sports_scan(ctx.bot, chat_id)
    elif d == "crypto_scan":
        await query.message.reply_text("📈 Scanning crypto...")
        await do_crypto_scan(ctx.bot, chat_id)
    elif d == "events":
        data = get_events()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        events = data.get("events", data.get("data", []))
        if not events:
            await query.message.reply_text("📈 No events.")
            return
        msg = "📈 *Events*\n\n" + "\n".join([f"• {e.get('title', e.get('name','N/A'))}" for e in events[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "trades":
        data = get_trades()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        trades = data.get("trades", data.get("data", []))
        if not trades:
            await query.message.reply_text("🔄 No trades.")
            return
        msg = "🔄 *Trades*\n\n" + "\n".join([f"• {t.get('market','N/A')} | {t.get('side','N/A')} | ₦{t.get('amount',0):,.2f}" for t in trades[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "log":
        if not trade_log:
            await query.message.reply_text("📜 No trades this session.")
            return
        await query.message.reply_text("📜 *Session Trades*\n\n" + "\n".join([f"• {t}" for t in trade_log[-10:]]), parse_mode="Markdown")
    elif d == "start_bot":
        bot_active = True
        await query.message.reply_text("▶️ Bot *active!*", parse_mode="Markdown")
    elif d == "stop_bot":
        bot_active = False
        await query.message.reply_text("⏹ Bot *paused.*", parse_mode="Markdown")

async def do_sports_scan(bot, chat_id):
    if not bot_active:
        return
    try:
        bayse_events = get_events()
        bayse_list = bayse_events.get("events", bayse_events.get("data", []))
        sofa_events = get_live_events()
        if not bayse_list:
            await bot.send_message(chat_id=chat_id, text="⚽ No Bayse events available.")
            return
        bal = get_balance()
        amount = bal * SPORTS_ALLOC
        found = 0
        for b_event in bayse_list[:20]:
            b_title = b_event.get("title", b_event.get("name", "")).lower()
            b_markets = b_event.get("markets", [])
            if not b_markets:
                continue
            for sofa_event in sofa_events[:30]:
                s_home = sofa_event.get("homeTeam", {}).get("name", "").lower()
                s_away = sofa_event.get("awayTeam", {}).get("name", "").lower()
                if s_home not in b_title and s_away not in b_title:
                    continue
                sofa_probs = get_sofascore_probability(sofa_event.get("id"))
                if not sofa_probs:
                    continue
                sofa_home_prob = sofa_probs.get("homeWin", 0)
                for market in b_markets[:3]:
                    market_id = market.get("id")
                    b_event_id = b_event.get("id")
                    b_prob = float(market.get("probability", market.get("price", 0)))
                    margin = sofa_home_prob - b_prob
                    if margin >= SPORTS_MARGIN:
                        result = place_order(b_event_id, market_id, "buy", amount)
                        trade_log.append(f"{datetime.now().strftime('%H:%M')} | {s_home} vs {s_away} | +{margin:.0%} | ₦{amount:,.0f}")
                        found += 1
                        await bot.send_message(chat_id=chat_id,
                            text=f"⚽ *Sports Trade!*\n\nMatch: `{s_home} vs {s_away}`\nEdge: `+{margin:.1%}`\nAmount: `₦{amount:,.2f}`\nStatus: `{result.get('status', result.get('id','submitted'))}`",
                            parse_mode="Markdown")
        if found == 0:
            await bot.send_message(chat_id=chat_id, text="⚽ No qualifying edge found.")
    except Exception as e:
        logger.error(f"Sports error: {e}")
        await bot.send_message(chat_id=chat_id, text=f"⚽ Sports scan error: {str(e)}")

async def do_crypto_scan(bot, chat_id):
    global last_crypto_trade
    if not bot_active:
        return
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "15m", "limit": 30},
            timeout=10
        )
        candles = r.json()
        prices = [float(c[4]) for c in candles]
        signal, rsi, macd = get_signal(prices)
        now = datetime.now()
        time_since = (now - last_crypto_trade).seconds if last_crypto_trade else 999
        if signal in ("BUY", "SELL") or (time_since >= 600 and signal in ("WEAK_BUY", "WEAK_SELL")):
            data = get_events()
            events = data.get("events", data.get("data", []))
            crypto = [e for e in events if "btc" in e.get("title", e.get("name","")).lower()]
            if not crypto:
                await bot.send_message(chat_id=chat_id, text="📈 No BTC markets found on Bayse.")
                return
            event = crypto[0]
            markets = event.get("markets", [])
            if not markets:
                await bot.send_message(chat_id=chat_id, text="📈 No BTC markets available.")
                return
            bal = get_balance()
            amount = bal * CRYPTO_ALLOC
            side = "buy" if "BUY" in signal else "sell"
            result = place_order(event.get("id"), markets[0].get("id"), side, amount)
            last_crypto_trade = now
            trade_log.append(f"{now.strftime('%H:%M')} | BTC {side.upper()} | RSI:{rsi:.1f} | ₦{amount:,.0f}")
            await bot.send_message(chat_id=chat_id,
                text=f"📈 *Crypto Trade!*\n\nSide: `{side.upper()}`\nAmount: `₦{amount:,.2f}`\nRSI: `{rsi:.2f}` | MACD: `{macd:.4f}`\nSignal: `{signal}`\nStatus: `{result.get('status', result.get('id','submitted'))}`",
                parse_mode="Markdown")
        else:
            await bot.send_message(chat_id=chat_id,
                text=f"📈 *Crypto Scan*\n\nSignal: `{signal}`\nRSI: `{rsi:.2f}`\nMACD: `{macd:.4f}`\nNo trade triggered.",
                parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Crypto error: {e}")
        await bot.send_message(chat_id=chat_id, text=f"📈 Crypto scan error: {str(e)}")

async def post_init(app: Application):
    log
cat > bot.py << 'EOF'
import logging
import os
import requests
import hmac
import hashlib
import base64
import json
import time
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
BAYSE_PUBLIC_KEY = os.getenv("BAYSE_API_KEY", "")
BAYSE_SECRET_KEY = os.getenv("BAYSE_API_SECRET", "")
BAYSE_BASE_URL = os.getenv("BAYSE_BASE_URL", "https://relay.bayse.markets")
CRYPTO_ALLOC = 0.10
SPORTS_ALLOC = 0.20
SPORTS_MARGIN = 0.10
ALLOWED_USERS = [int(x) for x in os.getenv("ALLOWED_USERS", "0").split(",")]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

trade_log = []
last_crypto_trade = None
bot_active = True

def make_signature(method, path, body=""):
    timestamp = str(int(time.time()))
    body_hash = hashlib.sha256(body.encode()).hexdigest()
    payload = f"{timestamp}.{method}.{path}.{body_hash}"
    signature = base64.b64encode(
        hmac.new(BAYSE_SECRET_KEY.encode(), payload.encode(), hashlib.sha256).digest()
    ).decode()
    return timestamp, signature

def write_headers(method, path, body=""):
    timestamp, signature = make_signature(method, path, body)
    return {
        "X-Public-Key": BAYSE_PUBLIC_KEY,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
        "Content-Type": "application/json"
    }

def get_balance():
    try:
        path = "/v1/wallet/assets"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        data = r.json()
        assets = data.get("assets", [])
        for asset in assets:
            if asset.get("isLocalCurrencyAsset") == True:
                return float(asset.get("availableBalance", 0))
        if assets:
            return float(assets[0].get("availableBalance", 0))
        return 0
    except Exception as e:
        logger.error(f"Balance error: {e}")
        return 0

def get_portfolio():
    try:
        path = "/v1/pm/portfolio"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_orders():
    try:
        path = "/v1/pm/orders"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_activities():
    try:
        path = "/v1/pm/activities"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_events():
    try:
        path = "/v1/pm/events"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_trades():
    try:
        path = "/v1/pm/trades"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def place_order(event_id, market_id, side, amount):
    try:
        path = f"/v1/pm/events/{event_id}/markets/{market_id}/orders"
        body = json.dumps({"side": side, "amount": amount})
        r = requests.post(f"{BAYSE_BASE_URL}{path}", headers=write_headers("POST", path, body), data=body, timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_live_events():
    try:
        r = requests.get("https://api.sofascore.com/api/v1/sport/football/events/live", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        return r.json().get("events", [])
    except:
        return []

def get_sofascore_probability(event_id):
    try:
        r = requests.get(f"https://api.sofascore.com/api/v1/event/{event_id}/probabilities", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        return r.json().get("probabilities", {})
    except:
        return {}

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    return 100 - (100 / (1 + avg_gain / avg_loss))

def calculate_macd(prices):
    if len(prices) < 26:
        return 0, 0
    def ema(data, period):
        k = 2 / (period + 1)
        v = data[0]
        for p in data[1:]:
            v = p * k + v * (1 - k)
        return v
    return ema(prices[-26:], 12) - ema(prices[-26:], 26), ema(prices[-26:], 12)

def get_signal(prices):
    rsi = calculate_rsi(prices)
    macd, _ = calculate_macd(prices)
    if rsi < 35 and macd > 0:
        return "BUY", rsi, macd
    elif rsi > 65 and macd < 0:
        return "SELL", rsi, macd
    elif rsi < 40:
        return "WEAK_BUY", rsi, macd
    elif rsi > 60:
        return "WEAK_SELL", rsi, macd
    return "HOLD", rsi, macd

def is_allowed(user_id):
    return user_id in ALLOWED_USERS or ALLOWED_USERS == [0]

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    keyboard = [
        [InlineKeyboardButton("💰 Balance", callback_data="balance"),
         InlineKeyboardButton("📊 Portfolio", callback_data="portfolio")],
        [InlineKeyboardButton("📋 Orders", callback_data="orders"),
         InlineKeyboardButton("📜 Activities", callback_data="activities")],
        [InlineKeyboardButton("⚽ Sports Scan", callback_data="sports"),
         InlineKeyboardButton("📈 Crypto Scan", callback_data="crypto_scan")],
        [InlineKeyboardButton("📈 Events", callback_data="events"),
         InlineKeyboardButton("🔄 Trades", callback_data="trades")],
        [InlineKeyboardButton("📜 Trade Log", callback_data="log"),
         InlineKeyboardButton("▶️ Start Bot", callback_data="start_bot")],
        [InlineKeyboardButton("⏹ Stop Bot", callback_data="stop_bot")],
    ]
    await update.message.reply_text(
        "🤖 *Gowagr Bot* is online!\n\nDual-market autotrader:\n• 📈 Crypto (BTC 15m)\n• ⚽ Sports (SofaScore)\n\nChoose an action:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def balance_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Checking balance...")
    bal = get_balance()
    await update.message.reply_text(
        f"💰 *Wallet Balance*\n\n`₦{bal:,.2f}`\n\n⚽ Sports budget: `₦{bal*SPORTS_ALLOC:,.2f}`\n📈 Crypto budget: `₦{bal*CRYPTO_ALLOC:,.2f}`",
        parse_mode="Markdown"
    )

async def portfolio_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading portfolio...")
    data = get_portfolio()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    positions = data.get("positions", data.get("data", []))
    if not positions:
        await update.message.reply_text("📊 No open positions.")
        return
    msg = "📊 *Portfolio*\n\n" + "\n".join([f"• {p.get('market', p.get('name','N/A'))} | {p.get('shares', p.get('amount','N/A'))}" for p in positions[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def orders_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading orders...")
    data = get_orders()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    orders = data.get("orders", data.get("data", []))
    if not orders:
        await update.message.reply_text("📋 No open orders.")
        return
    msg = "📋 *Orders*\n\n" + "\n".join([f"• {o.get('market','N/A')} | {o.get('side','N/A')} | ₦{o.get('amount',0):,.2f}" for o in orders[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def activities_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading activities...")
    data = get_activities()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    acts = data.get("activities", data.get("data", []))
    if not acts:
        await update.message.reply_text("📜 No activities.")
        return
    msg = "📜 *Activities*\n\n" + "\n".join([f"• {a.get('type','N/A')} | {a.get('description', a.get('market','N/A'))}" for a in acts[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def events_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading events...")
    data = get_events()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    events = data.get("events", data.get("data", []))
    if not events:
        await update.message.reply_text("📈 No events.")
        return
    msg = "📈 *Events*\n\n" + "\n".join([f"• {e.get('title', e.get('name','N/A'))}" for e in events[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def trades_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading trades...")
    data = get_trades()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    trades = data.get("trades", data.get("data", []))
    if not trades:
        await update.message.reply_text("🔄 No trades.")
        return
    msg = "🔄 *Trades*\n\n" + "\n".join([f"• {t.get('market','N/A')} | {t.get('side','N/A')} | ₦{t.get('amount',0):,.2f}" for t in trades[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def log_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    if not trade_log:
        await update.message.reply_text("📜 No trades this session.")
        return
    await update.message.reply_text("📜 *Session Trades*\n\n" + "\n".join([f"• {t}" for t in trade_log[-10:]]), parse_mode="Markdown")

async def sports_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⚽ Scanning SofaScore...")
    await do_sports_scan(ctx.bot, update.effective_chat.id)

async def crypto_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("📈 Scanning crypto markets...")
    await do_crypto_scan(ctx.bot, update.effective_chat.id)

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    d = query.data
    global bot_active
    if d == "balance":
        bal = get_balance()
        await query.message.reply_text(
            f"💰 *Balance*\n\n`₦{bal:,.2f}`\n\n⚽ Sports: `₦{bal*SPORTS_ALLOC:,.2f}`\n📈 Crypto: `₦{bal*CRYPTO_ALLOC:,.2f}`",
            parse_mode="Markdown"
        )
    elif d == "portfolio":
        data = get_portfolio()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        positions = data.get("positions", data.get("data", []))
        if not positions:
            await query.message.reply_text("📊 No open positions.")
            return
        msg = "📊 *Portfolio*\n\n" + "\n".join([f"• {p.get('market', p.get('name','N/A'))} | {p.get('shares', p.get('amount','N/A'))}" for p in positions[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "orders":
        data = get_orders()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        orders = data.get("orders", data.get("data", []))
        if not orders:
            await query.message.reply_text("📋 No open orders.")
            return
        msg = "📋 *Orders*\n\n" + "\n".join([f"• {o.get('market','N/A')} | {o.get('side','N/A')} | ₦{o.get('amount',0):,.2f}" for o in orders[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "activities":
        data = get_activities()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        acts = data.get("activities", data.get("data", []))
        if not acts:
            await query.message.reply_text("📜 No activities.")
            return
        msg = "📜 *Activities*\n\n" + "\n".join([f"• {a.get('type','N/A')} | {a.get('description', a.get('market','N/A'))}" for a in acts[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "sports":
        await query.message.reply_text("⚽ Scanning...")
        await do_sports_scan(ctx.bot, chat_id)
    elif d == "crypto_scan":
        await query.message.reply_text("📈 Scanning crypto...")
        await do_crypto_scan(ctx.bot, chat_id)
    elif d == "events":
        data = get_events()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        events = data.get("events", data.get("data", []))
        if not events:
            await query.message.reply_text("📈 No events.")
            return
        msg = "📈 *Events*\n\n" + "\n".join([f"• {e.get('title', e.get('name','N/A'))}" for e in events[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "trades":
        data = get_trades()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        trades = data.get("trades", data.get("data", []))
        if not trades:
            await query.message.reply_text("🔄 No trades.")
            return
        msg = "🔄 *Trades*\n\n" + "\n".join([f"• {t.get('market','N/A')} | {t.get('side','N/A')} | ₦{t.get('amount',0):,.2f}" for t in trades[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "log":
        if not trade_log:
            await query.message.reply_text("📜 No trades this session.")
            return
        await query.message.reply_text("📜 *Session Trades*\n\n" + "\n".join([f"• {t}" for t in trade_log[-10:]]), parse_mode="Markdown")
    elif d == "start_bot":
        bot_active = True
        await query.message.reply_text("▶️ Bot *active!*", parse_mode="Markdown")
    elif d == "stop_bot":
        bot_active = False
        await query.message.reply_text("⏹ Bot *paused.*", parse_mode="Markdown")

async def do_sports_scan(bot, chat_id):
    if not bot_active:
        return
    try:
        bayse_events = get_events()
        bayse_list = bayse_events.get("events", bayse_events.get("data", []))
        sofa_events = get_live_events()
        if not bayse_list:
            await bot.send_message(chat_id=chat_id, text="⚽ No Bayse events available.")
            return
        bal = get_balance()
        amount = bal * SPORTS_ALLOC
        found = 0
        for b_event in bayse_list[:20]:
            b_title = b_event.get("title", b_event.get("name", "")).lower()
            b_markets = b_event.get("markets", [])
            if not b_markets:
                continue
            for sofa_event in sofa_events[:30]:
                s_home = sofa_event.get("homeTeam", {}).get("name", "").lower()
                s_away = sofa_event.get("awayTeam", {}).get("name", "").lower()
                if s_home not in b_title and s_away not in b_title:
                    continue
                sofa_probs = get_sofascore_probability(sofa_event.get("id"))
                if not sofa_probs:
                    continue
                sofa_home_prob = sofa_probs.get("homeWin", 0)
                for market in b_markets[:3]:
                    market_id = market.get("id")
                    b_event_id = b_event.get("id")
                    b_prob = float(market.get("probability", market.get("price", 0)))
                    margin = sofa_home_prob - b_prob
                    if margin >= SPORTS_MARGIN:
                        result = place_order(b_event_id, market_id, "buy", amount)
                        trade_log.append(f"{datetime.now().strftime('%H:%M')} | {s_home} vs {s_away} | +{margin:.0%} | ₦{amount:,.0f}")
                        found += 1
                        await bot.send_message(chat_id=chat_id,
                            text=f"⚽ *Sports Trade!*\n\nMatch: `{s_home} vs {s_away}`\nEdge: `+{margin:.1%}`\nAmount: `₦{amount:,.2f}`\nStatus: `{result.get('status', result.get('id','submitted'))}`",
                            parse_mode="Markdown")
        if found == 0:
            await bot.send_message(chat_id=chat_id, text="⚽ No qualifying edge found.")
    except Exception as e:
        logger.error(f"Sports error: {e}")
        await bot.send_message(chat_id=chat_id, text=f"⚽ Sports scan error: {str(e)}")

async def do_crypto_scan(bot, chat_id):
    global last_crypto_trade
    if not bot_active:
        return
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "15m", "limit": 30},
            timeout=10
        )
        candles = r.json()
        prices = [float(c[4]) for c in candles]
        signal, rsi, macd = get_signal(prices)
        now = datetime.now()
        time_since = (now - last_crypto_trade).seconds if last_crypto_trade else 999
        if signal in ("BUY", "SELL") or (time_since >= 600 and signal in ("WEAK_BUY", "WEAK_SELL")):
            data = get_events()
            events = data.get("events", data.get("data", []))
            crypto = [e for e in events if "btc" in e.get("title", e.get("name","")).lower()]
            if not crypto:
                await bot.send_message(chat_id=chat_id, text="📈 No BTC markets found on Bayse.")
                return
            event = crypto[0]
            markets = event.get("markets", [])
            if not markets:
                await bot.send_message(chat_id=chat_id, text="📈 No BTC markets available.")
                return
            bal = get_balance()
            amount = bal * CRYPTO_ALLOC
            side = "buy" if "BUY" in signal else "sell"
            result = place_order(event.get("id"), markets[0].get("id"), side, amount)
            last_crypto_trade = now
            trade_log.append(f"{now.strftime('%H:%M')} | BTC {side.upper()} | RSI:{rsi:.1f} | ₦{amount:,.0f}")
            await bot.send_message(chat_id=chat_id,
                text=f"📈 *Crypto Trade!*\n\nSide: `{side.upper()}`\nAmount: `₦{amount:,.2f}`\nRSI: `{rsi:.2f}` | MACD: `{macd:.4f}`\nSignal: `{signal}`\nStatus: `{result.get('status', result.get('id','submitted'))}`",
                parse_mode="Markdown")
        else:
            await bot.send_message(chat_id=chat_id,
                text=f"📈 *Crypto Scan*\n\nSignal: `{signal}`\nRSI: `{rsi:.2f}`\nMACD: `{macd:.4f}`\nNo trade triggered.",
                parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Crypto error: {e}")
        await bot.send_message(chat_id=chat_id, text=f"📈 Crypto scan error: {str(e)}")
cat > bot.py << 'EOF'
import logging
import os
import requests
import hmac
import hashlib
import base64
import json
import time
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
BAYSE_PUBLIC_KEY = os.getenv("BAYSE_API_KEY", "")
BAYSE_SECRET_KEY = os.getenv("BAYSE_API_SECRET", "")
BAYSE_BASE_URL = os.getenv("BAYSE_BASE_URL", "https://relay.bayse.markets")
CRYPTO_ALLOC = 0.10
SPORTS_ALLOC = 0.20
SPORTS_MARGIN = 0.10
ALLOWED_USERS = [int(x) for x in os.getenv("ALLOWED_USERS", "0").split(",")]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

trade_log = []
last_crypto_trade = None
bot_active = True

def make_signature(method, path, body=""):
    timestamp = str(int(time.time()))
    body_hash = hashlib.sha256(body.encode()).hexdigest()
    payload = f"{timestamp}.{method}.{path}.{body_hash}"
    signature = base64.b64encode(
        hmac.new(BAYSE_SECRET_KEY.encode(), payload.encode(), hashlib.sha256).digest()
    ).decode()
    return timestamp, signature

def write_headers(method, path, body=""):
    timestamp, signature = make_signature(method, path, body)
    return {
        "X-Public-Key": BAYSE_PUBLIC_KEY,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
        "Content-Type": "application/json"
    }

def get_balance():
    try:
        path = "/v1/wallet/assets"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        data = r.json()
        assets = data.get("assets", [])
        for asset in assets:
            if asset.get("isLocalCurrencyAsset") == True:
                return float(asset.get("availableBalance", 0))
        if assets:
            return float(assets[0].get("availableBalance", 0))
        return 0
    except Exception as e:
        logger.error(f"Balance error: {e}")
        return 0

def get_portfolio():
    try:
        path = "/v1/pm/portfolio"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_orders():
    try:
        path = "/v1/pm/orders"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_activities():
    try:
        path = "/v1/pm/activities"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_events():
    try:
        path = "/v1/pm/events"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_trades():
    try:
        path = "/v1/pm/trades"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def place_order(event_id, market_id, side, amount):
    try:
        path = f"/v1/pm/events/{event_id}/markets/{market_id}/orders"
        body = json.dumps({"side": side, "amount": amount})
        r = requests.post(f"{BAYSE_BASE_URL}{path}", headers=write_headers("POST", path, body), data=body, timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_live_events():
    try:
        r = requests.get("https://api.sofascore.com/api/v1/sport/football/events/live", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        return r.json().get("events", [])
    except:
        return []

def get_sofascore_probability(event_id):
    try:
        r = requests.get(f"https://api.sofascore.com/api/v1/event/{event_id}/probabilities", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        return r.json().get("probabilities", {})
    except:
        return {}

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    return 100 - (100 / (1 + avg_gain / avg_loss))

def calculate_macd(prices):
    if len(prices) < 26:
        return 0, 0
    def ema(data, period):
        k = 2 / (period + 1)
        v = data[0]
        for p in data[1:]:
            v = p * k + v * (1 - k)
        return v
    return ema(prices[-26:], 12) - ema(prices[-26:], 26), ema(prices[-26:], 12)

def get_signal(prices):
    rsi = calculate_rsi(prices)
    macd, _ = calculate_macd(prices)
    if rsi < 35 and macd > 0:
        return "BUY", rsi, macd
    elif rsi > 65 and macd < 0:
        return "SELL", rsi, macd
    elif rsi < 40:
        return "WEAK_BUY", rsi, macd
    elif rsi > 60:
        return "WEAK_SELL", rsi, macd
    return "HOLD", rsi, macd

def is_allowed(user_id):
    return user_id in ALLOWED_USERS or ALLOWED_USERS == [0]

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    keyboard = [
        [InlineKeyboardButton("💰 Balance", callback_data="balance"),
         InlineKeyboardButton("📊 Portfolio", callback_data="portfolio")],
        [InlineKeyboardButton("📋 Orders", callback_data="orders"),
         InlineKeyboardButton("📜 Activities", callback_data="activities")],
        [InlineKeyboardButton("⚽ Sports Scan", callback_data="sports"),
         InlineKeyboardButton("📈 Crypto Scan", callback_data="crypto_scan")],
        [InlineKeyboardButton("📈 Events", callback_data="events"),
         InlineKeyboardButton("🔄 Trades", callback_data="trades")],
        [InlineKeyboardButton("📜 Trade Log", callback_data="log"),
         InlineKeyboardButton("▶️ Start Bot", callback_data="start_bot")],
        [InlineKeyboardButton("⏹ Stop Bot", callback_data="stop_bot")],
    ]
    await update.message.reply_text(
        "🤖 *Gowagr Bot* is online!\n\nDual-market autotrader:\n• 📈 Crypto (BTC 15m)\n• ⚽ Sports (SofaScore)\n\nChoose an action:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def balance_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Checking balance...")
    bal = get_balance()
    await update.message.reply_text(
        f"💰 *Wallet Balance*\n\n`₦{bal:,.2f}`\n\n⚽ Sports budget: `₦{bal*SPORTS_ALLOC:,.2f}`\n📈 Crypto budget: `₦{bal*CRYPTO_ALLOC:,.2f}`",
        parse_mode="Markdown"
    )

async def portfolio_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading portfolio...")
    data = get_portfolio()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    positions = data.get("positions", data.get("data", []))
    if not positions:
        await update.message.reply_text("📊 No open positions.")
        return
    msg = "📊 *Portfolio*\n\n" + "\n".join([f"• {p.get('market', p.get('name','N/A'))} | {p.get('shares', p.get('amount','N/A'))}" for p in positions[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def orders_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading orders...")
    data = get_orders()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    orders = data.get("orders", data.get("data", []))
    if not orders:
        await update.message.reply_text("📋 No open orders.")
        return
    msg = "📋 *Orders*\n\n" + "\n".join([f"• {o.get('market','N/A')} | {o.get('side','N/A')} | ₦{o.get('amount',0):,.2f}" for o in orders[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def activities_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading activities...")
    data = get_activities()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    acts = data.get("activities", data.get("data", []))
    if not acts:
        await update.message.reply_text("📜 No activities.")
        return
    msg = "📜 *Activities*\n\n" + "\n".join([f"• {a.get('type','N/A')} | {a.get('description', a.get('market','N/A'))}" for a in acts[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def events_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading events...")
    data = get_events()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    events = data.get("events", data.get("data", []))
    if not events:
        await update.message.reply_text("📈 No events.")
        return
    msg = "📈 *Events*\n\n" + "\n".join([f"• {e.get('title', e.get('name','N/A'))}" for e in events[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def trades_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading trades...")
    data = get_trades()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    trades = data.get("trades", data.get("data", []))
    if not trades:
        await update.message.reply_text("🔄 No trades.")
        return
    msg = "🔄 *Trades*\n\n" + "\n".join([f"• {t.get('market','N/A')} | {t.get('side','N/A')} | ₦{t.get('amount',0):,.2f}" for t in trades[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def log_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    if not trade_log:
        await update.message.reply_text("📜 No trades this session.")
        return
    await update.message.reply_text("📜 *Session Trades*\n\n" + "\n".join([f"• {t}" for t in trade_log[-10:]]), parse_mode="Markdown")

async def sports_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⚽ Scanning SofaScore...")
    await do_sports_scan(ctx.bot, update.effective_chat.id)

async def crypto_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("📈 Scanning crypto markets...")
    await do_crypto_scan(ctx.bot, update.effective_chat.id)

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    d = query.data
    global bot_active
    if d == "balance":
        bal = get_balance()
        await query.message.reply_text(
            f"💰 *Balance*\n\n`₦{bal:,.2f}`\n\n⚽ Sports: `₦{bal*SPORTS_ALLOC:,.2f}`\n📈 Crypto: `₦{bal*CRYPTO_ALLOC:,.2f}`",
            parse_mode="Markdown"
        )
    elif d == "portfolio":
        data = get_portfolio()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        positions = data.get("positions", data.get("data", []))
        if not positions:
            await query.message.reply_text("📊 No open positions.")
            return
        msg = "📊 *Portfolio*\n\n" + "\n".join([f"• {p.get('market', p.get('name','N/A'))} | {p.get('shares', p.get('amount','N/A'))}" for p in positions[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "orders":
        data = get_orders()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        orders = data.get("orders", data.get("data", []))
        if not orders:
            await query.message.reply_text("📋 No open orders.")
            return
        msg = "📋 *Orders*\n\n" + "\n".join([f"• {o.get('market','N/A')} | {o.get('side','N/A')} | ₦{o.get('amount',0):,.2f}" for o in orders[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "activities":
        data = get_activities()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        acts = data.get("activities", data.get("data", []))
        if not acts:
            await query.message.reply_text("📜 No activities.")
            return
        msg = "📜 *Activities*\n\n" + "\n".join([f"• {a.get('type','N/A')} | {a.get('description', a.get('market','N/A'))}" for a in acts[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "sports":
        await query.message.reply_text("⚽ Scanning...")
        await do_sports_scan(ctx.bot, chat_id)
    elif d == "crypto_scan":
        await query.message.reply_text("📈 Scanning crypto...")
        await do_crypto_scan(ctx.bot, chat_id)
    elif d == "events":
        data = get_events()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        events = data.get("events", data.get("data", []))
        if not events:
            await query.message.reply_text("📈 No events.")
            return
        msg = "📈 *Events*\n\n" + "\n".join([f"• {e.get('title', e.get('name','N/A'))}" for e in events[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "trades":
        data = get_trades()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        trades = data.get("trades", data.get("data", []))
        if not trades:
            await query.message.reply_text("🔄 No trades.")
            return
        msg = "🔄 *Trades*\n\n" + "\n".join([f"• {t.get('market','N/A')} | {t.get('side','N/A')} | ₦{t.get('amount',0):,.2f}" for t in trades[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "log":
        if not trade_log:
            await query.message.reply_text("📜 No trades this session.")
            return
        await query.message.reply_text("📜 *Session Trades*\n\n" + "\n".join([f"• {t}" for t in trade_log[-10:]]), parse_mode="Markdown")
    elif d == "start_bot":
        bot_active = True
        await query.message.reply_text("▶️ Bot *active!*", parse_mode="Markdown")
    elif d == "stop_bot":
        bot_active = False
        await query.message.reply_text("⏹ Bot *paused.*", parse_mode="Markdown")

async def do_sports_scan(bot, chat_id):
    if not bot_active:
        return
    try:
        bayse_events = get_events()
        bayse_list = bayse_events.get("events", bayse_events.get("data", []))
        sofa_events = get_live_events()
        if not bayse_list:
            await bot.send_message(chat_id=chat_id, text="⚽ No Bayse events available.")
            return
        bal = get_balance()
        amount = bal * SPORTS_ALLOC
        found = 0
        for b_event in bayse_list[:20]:
            b_title = b_event.get("title", b_event.get("name", "")).lower()
            b_markets = b_event.get("markets", [])
            if not b_markets:
                continue
            for sofa_event in sofa_events[:30]:
                s_home = sofa_event.get("homeTeam", {}).get("name", "").lower()
                s_away = sofa_event.get("awayTeam", {}).get("name", "").lower()
                if s_home not in b_title and s_away not in b_title:
                    continue
                sofa_probs = get_sofascore_probability(sofa_event.get("id"))
                if not sofa_probs:
                    continue
                sofa_home_prob = sofa_probs.get("homeWin", 0)
                for market in b_markets[:3]:
                    market_id = market.get("id")
                    b_event_id = b_event.get("id")
                    b_prob = float(market.get("probability", market.get("price", 0)))
                    margin = sofa_home_prob - b_prob
                    if margin >= SPORTS_MARGIN:
                        result = place_order(b_event_id, market_id, "buy", amount)
                        trade_log.append(f"{datetime.now().strftime('%H:%M')} | {s_home} vs {s_away} | +{margin:.0%} | ₦{amount:,.0f}")
                        found += 1
                        await bot.send_message(chat_id=chat_id,
                            text=f"⚽ *Sports Trade!*\n\nMatch: `{s_home} vs {s_away}`\nEdge: `+{margin:.1%}`\nAmount: `₦{amount:,.2f}`\nStatus: `{result.get('status', result.get('id','submitted'))}`",
                            parse_mode="Markdown")
        if found == 0:
            await bot.send_message(chat_id=chat_id, text="⚽ No qualifying edge found.")
    except Exception as e:
        logger.error(f"Sports error: {e}")
        await bot.send_message(chat_id=chat_id, text=f"⚽ Sports scan error: {str(e)}")

async def do_crypto_scan(bot, chat_id):
    global last_crypto_trade
    if not bot_active:
        return
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "15m", "limit": 30},
            timeout=10
        )
        candles = r.json()
        prices = [float(c[4]) for c in candles]
        signal, rsi, macd = get_signal(prices)
        now = datetime.now()
        time_since = (now - last_crypto_trade).seconds if last_crypto_trade else 999
        if signal in ("BUY", "SELL") or (time_since >= 600 and signal in ("WEAK_BUY", "WEAK_SELL")):
            data = get_events()
            events = data.get("events", data.get("data", []))
            crypto = [e for e in events if "btc" in e.get("title", e.get("name","")).lower()]
            if not crypto:
                await bot.send_message(chat_id=chat_id, text="📈 No BTC markets found on Bayse.")
                return
            event = crypto[0]
            markets = event.get("markets", [])
            if not markets:
                await bot.send_message(chat_id=chat_id, text="📈 No BTC markets available.")
                return
            bal = get_balance()
            amount = bal * CRYPTO_ALLOC
            side = "buy" if "BUY" in signal else "sell"
            result = place_order(event.get("id"), markets[0].get("id"), side, amount)
            last_crypto_trade = now
            trade_log.append(f"{now.strftime('%H:%M')} | BTC {side.upper()} | RSI:{rsi:.1f} | ₦{amount:,.0f}")
            await bot.send_message(chat_id=chat_id,
                text=f"📈 *Crypto Trade!*\n\nSide: `{side.upper()}`\nAmount: `₦{amount:,.2f}`\nRSI: `{rsi:.2f}` | MACD: `{macd:.4f}`\nSignal: `{signal}`\nStatus: `{result.get('status', result.get('id','submitted'))}`",
                parse_mode="Markdown")
        else:
            await bot.send_message(chat_id=chat_id,
                text=f"📈 *Crypto Scan*\n\nSignal: `{signal}`\nRSI: `{rsi:.2f}`\nMACD: `{macd:.4f}`\nNo trade triggered.",
                parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Crypto error: {e}")
        await bot.send_message(chat_id=chat_id, text=f"📈 Crypto scan error: {str(e)}")

async def post_init(app: Application):
    log
cat > bot.py << 'EOF'
import logging
import os
import requests
import hmac
import hashlib
import base64
import json
import time
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
BAYSE_PUBLIC_KEY = os.getenv("BAYSE_API_KEY", "")
BAYSE_SECRET_KEY = os.getenv("BAYSE_API_SECRET", "")
BAYSE_BASE_URL = os.getenv("BAYSE_BASE_URL", "https://relay.bayse.markets")
CRYPTO_ALLOC = 0.10
SPORTS_ALLOC = 0.20
SPORTS_MARGIN = 0.10
ALLOWED_USERS = [int(x) for x in os.getenv("ALLOWED_USERS", "0").split(",")]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

trade_log = []
last_crypto_trade = None
bot_active = True

def make_signature(method, path, body=""):
    timestamp = str(int(time.time()))
    body_hash = hashlib.sha256(body.encode()).hexdigest()
    payload = f"{timestamp}.{method}.{path}.{body_hash}"
    signature = base64.b64encode(
        hmac.new(BAYSE_SECRET_KEY.encode(), payload.encode(), hashlib.sha256).digest()
    ).decode()
    return timestamp, signature

def write_headers(method, path, body=""):
    timestamp, signature = make_signature(method, path, body)
    return {
        "X-Public-Key": BAYSE_PUBLIC_KEY,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
        "Content-Type": "application/json"
    }

def get_balance():
    try:
        path = "/v1/wallet/assets"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        data = r.json()
        assets = data.get("assets", [])
        for asset in assets:
            if asset.get("isLocalCurrencyAsset") == True:
                return float(asset.get("availableBalance", 0))
        if assets:
            return float(assets[0].get("availableBalance", 0))
        return 0
    except Exception as e:
        logger.error(f"Balance error: {e}")
        return 0

def get_portfolio():
    try:
        path = "/v1/pm/portfolio"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_orders():
    try:
        path = "/v1/pm/orders"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_activities():
    try:
        path = "/v1/pm/activities"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_events():
    try:
        path = "/v1/pm/events"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_trades():
    try:
        path = "/v1/pm/trades"
        r = requests.get(f"{BAYSE_BASE_URL}{path}", headers=write_headers("GET", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def place_order(event_id, market_id, side, amount):
    try:
        path = f"/v1/pm/events/{event_id}/markets/{market_id}/orders"
        body = json.dumps({"side": side, "amount": amount})
        r = requests.post(f"{BAYSE_BASE_URL}{path}", headers=write_headers("POST", path, body), data=body, timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_live_events():
    try:
        r = requests.get("https://api.sofascore.com/api/v1/sport/football/events/live", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        return r.json().get("events", [])
    except:
        return []

def get_sofascore_probability(event_id):
    try:
        r = requests.get(f"https://api.sofascore.com/api/v1/event/{event_id}/probabilities", headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        return r.json().get("probabilities", {})
    except:
        return {}

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    return 100 - (100 / (1 + avg_gain / avg_loss))

def calculate_macd(prices):
    if len(prices) < 26:
        return 0, 0
    def ema(data, period):
        k = 2 / (period + 1)
        v = data[0]
        for p in data[1:]:
            v = p * k + v * (1 - k)
        return v
    return ema(prices[-26:], 12) - ema(prices[-26:], 26), ema(prices[-26:], 12)

def get_signal(prices):
    rsi = calculate_rsi(prices)
    macd, _ = calculate_macd(prices)
    if rsi < 35 and macd > 0:
        return "BUY", rsi, macd
    elif rsi > 65 and macd < 0:
        return "SELL", rsi, macd
    elif rsi < 40:
        return "WEAK_BUY", rsi, macd
    elif rsi > 60:
        return "WEAK_SELL", rsi, macd
    return "HOLD", rsi, macd

def is_allowed(user_id):
    return user_id in ALLOWED_USERS or ALLOWED_USERS == [0]

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        await update.message.reply_text("⛔ Unauthorized.")
        return
    keyboard = [
        [InlineKeyboardButton("💰 Balance", callback_data="balance"),
         InlineKeyboardButton("📊 Portfolio", callback_data="portfolio")],
        [InlineKeyboardButton("📋 Orders", callback_data="orders"),
         InlineKeyboardButton("📜 Activities", callback_data="activities")],
        [InlineKeyboardButton("⚽ Sports Scan", callback_data="sports"),
         InlineKeyboardButton("📈 Crypto Scan", callback_data="crypto_scan")],
        [InlineKeyboardButton("📈 Events", callback_data="events"),
         InlineKeyboardButton("🔄 Trades", callback_data="trades")],
        [InlineKeyboardButton("📜 Trade Log", callback_data="log"),
         InlineKeyboardButton("▶️ Start Bot", callback_data="start_bot")],
        [InlineKeyboardButton("⏹ Stop Bot", callback_data="stop_bot")],
    ]
    await update.message.reply_text(
        "🤖 *Gowagr Bot* is online!\n\nDual-market autotrader:\n• 📈 Crypto (BTC 15m)\n• ⚽ Sports (SofaScore)\n\nChoose an action:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def balance_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Checking balance...")
    bal = get_balance()
    await update.message.reply_text(
        f"💰 *Wallet Balance*\n\n`₦{bal:,.2f}`\n\n⚽ Sports budget: `₦{bal*SPORTS_ALLOC:,.2f}`\n📈 Crypto budget: `₦{bal*CRYPTO_ALLOC:,.2f}`",
        parse_mode="Markdown"
    )

async def portfolio_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading portfolio...")
    data = get_portfolio()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    positions = data.get("positions", data.get("data", []))
    if not positions:
        await update.message.reply_text("📊 No open positions.")
        return
    msg = "📊 *Portfolio*\n\n" + "\n".join([f"• {p.get('market', p.get('name','N/A'))} | {p.get('shares', p.get('amount','N/A'))}" for p in positions[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def orders_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading orders...")
    data = get_orders()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    orders = data.get("orders", data.get("data", []))
    if not orders:
        await update.message.reply_text("📋 No open orders.")
        return
    msg = "📋 *Orders*\n\n" + "\n".join([f"• {o.get('market','N/A')} | {o.get('side','N/A')} | ₦{o.get('amount',0):,.2f}" for o in orders[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def activities_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading activities...")
    data = get_activities()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    acts = data.get("activities", data.get("data", []))
    if not acts:
        await update.message.reply_text("📜 No activities.")
        return
    msg = "📜 *Activities*\n\n" + "\n".join([f"• {a.get('type','N/A')} | {a.get('description', a.get('market','N/A'))}" for a in acts[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def events_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading events...")
    data = get_events()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    events = data.get("events", data.get("data", []))
    if not events:
        await update.message.reply_text("📈 No events.")
        return
    msg = "📈 *Events*\n\n" + "\n".join([f"• {e.get('title', e.get('name','N/A'))}" for e in events[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def trades_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⏳ Loading trades...")
    data = get_trades()
    if "error" in data:
        await update.message.reply_text(f"❌ {data['error']}")
        return
    trades = data.get("trades", data.get("data", []))
    if not trades:
        await update.message.reply_text("🔄 No trades.")
        return
    msg = "🔄 *Trades*\n\n" + "\n".join([f"• {t.get('market','N/A')} | {t.get('side','N/A')} | ₦{t.get('amount',0):,.2f}" for t in trades[:10]])
    await update.message.reply_text(msg, parse_mode="Markdown")

async def log_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    if not trade_log:
        await update.message.reply_text("📜 No trades this session.")
        return
    await update.message.reply_text("📜 *Session Trades*\n\n" + "\n".join([f"• {t}" for t in trade_log[-10:]]), parse_mode="Markdown")

async def sports_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("⚽ Scanning SofaScore...")
    await do_sports_scan(ctx.bot, update.effective_chat.id)

async def crypto_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("📈 Scanning crypto markets...")
    await do_crypto_scan(ctx.bot, update.effective_chat.id)

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    d = query.data
    global bot_active
    if d == "balance":
        bal = get_balance()
        await query.message.reply_text(
            f"💰 *Balance*\n\n`₦{bal:,.2f}`\n\n⚽ Sports: `₦{bal*SPORTS_ALLOC:,.2f}`\n📈 Crypto: `₦{bal*CRYPTO_ALLOC:,.2f}`",
            parse_mode="Markdown"
        )
    elif d == "portfolio":
        data = get_portfolio()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        positions = data.get("positions", data.get("data", []))
        if not positions:
            await query.message.reply_text("📊 No open positions.")
            return
        msg = "📊 *Portfolio*\n\n" + "\n".join([f"• {p.get('market', p.get('name','N/A'))} | {p.get('shares', p.get('amount','N/A'))}" for p in positions[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "orders":
        data = get_orders()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        orders = data.get("orders", data.get("data", []))
        if not orders:
            await query.message.reply_text("📋 No open orders.")
            return
        msg = "📋 *Orders*\n\n" + "\n".join([f"• {o.get('market','N/A')} | {o.get('side','N/A')} | ₦{o.get('amount',0):,.2f}" for o in orders[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "activities":
        data = get_activities()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        acts = data.get("activities", data.get("data", []))
        if not acts:
            await query.message.reply_text("📜 No activities.")
            return
        msg = "📜 *Activities*\n\n" + "\n".join([f"• {a.get('type','N/A')} | {a.get('description', a.get('market','N/A'))}" for a in acts[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "sports":
        await query.message.reply_text("⚽ Scanning...")
        await do_sports_scan(ctx.bot, chat_id)
    elif d == "crypto_scan":
        await query.message.reply_text("📈 Scanning crypto...")
        await do_crypto_scan(ctx.bot, chat_id)
    elif d == "events":
        data = get_events()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        events = data.get("events", data.get("data", []))
        if not events:
            await query.message.reply_text("📈 No events.")
            return
        msg = "📈 *Events*\n\n" + "\n".join([f"• {e.get('title', e.get('name','N/A'))}" for e in events[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "trades":
        data = get_trades()
        if "error" in data:
            await query.message.reply_text(f"❌ {data['error']}")
            return
        trades = data.get("trades", data.get("data", []))
        if not trades:
            await query.message.reply_text("🔄 No trades.")
            return
        msg = "🔄 *Trades*\n\n" + "\n".join([f"• {t.get('market','N/A')} | {t.get('side','N/A')} | ₦{t.get('amount',0):,.2f}" for t in trades[:10]])
        await query.message.reply_text(msg, parse_mode="Markdown")
    elif d == "log":
        if not trade_log:
            await query.message.reply_text("📜 No trades this session.")
            return
        await query.message.reply_text("📜 *Session Trades*\n\n" + "\n".join([f"• {t}" for t in trade_log[-10:]]), parse_mode="Markdown")
    elif d == "start_bot":
        bot_active = True
        await query.message.reply_text("▶️ Bot *active!*", parse_mode="Markdown")
    elif d == "stop_bot":
        bot_active = False
        await query.message.reply_text("⏹ Bot *paused.*", parse_mode="Markdown")

async def do_sports_scan(bot, chat_id):
    if not bot_active:
        return
    try:
        bayse_events = get_events()
        bayse_list = bayse_events.get("events", bayse_events.get("data", []))
        sofa_events = get_live_events()
        if not bayse_list:
            await bot.send_message(chat_id=chat_id, text="⚽ No Bayse events available.")
            return
        bal = get_balance()
        amount = bal * SPORTS_ALLOC
        found = 0
        for b_event in bayse_list[:20]:
            b_title = b_event.get("title", b_event.get("name", "")).lower()
            b_markets = b_event.get("markets", [])
            if not b_markets:
                continue
            for sofa_event in sofa_events[:30]:
                s_home = sofa_event.get("homeTeam", {}).get("name", "").lower()
                s_away = sofa_event.get("awayTeam", {}).get("name", "").lower()
                if s_home not in b_title and s_away not in b_title:
                    continue
                sofa_probs = get_sofascore_probability(sofa_event.get("id"))
                if not sofa_probs:
                    continue
                sofa_home_prob = sofa_probs.get("homeWin", 0)
                for market in b_markets[:3]:
                    market_id = market.get("id")
                    b_event_id = b_event.get("id")
                    b_prob = float(market.get("probability", market.get("price", 0)))
                    margin = sofa_home_prob - b_prob
                    if margin >= SPORTS_MARGIN:
                        result = place_order(b_event_id, market_id, "buy", amount)
                        trade_log.append(f"{datetime.now().strftime('%H:%M')} | {s_home} vs {s_away} | +{margin:.0%} | ₦{amount:,.0f}")
                        found += 1
                        await bot.send_message(chat_id=chat_id,
                            text=f"⚽ *Sports Trade!*\n\nMatch: `{s_home} vs {s_away}`\nEdge: `+{margin:.1%}`\nAmount: `₦{amount:,.2f}`\nStatus: `{result.get('status', result.get('id','submitted'))}`",
                            parse_mode="Markdown")
        if found == 0:
            await bot.send_message(chat_id=chat_id, text="⚽ No qualifying edge found.")
    except Exception as e:
        logger.error(f"Sports error: {e}")
        await bot.send_message(chat_id=chat_id, text=f"⚽ Sports scan error: {str(e)}")

async def do_crypto_scan(bot, chat_id):
    global last_crypto_trade
    if not bot_active:
        return
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "15m", "limit": 30},
            timeout=10
        )
        candles = r.json()
        prices = [float(c[4]) for c in candles]
        signal, rsi, macd = get_signal(prices)
        now = datetime.now()
        time_since = (now - last_crypto_trade).seconds if last_crypto_trade else 999
        if signal in ("BUY", "SELL") or (time_since >= 600 and signal in ("WEAK_BUY", "WEAK_SELL")):
            data = get_events()
            events = data.get("events", data.get("data", []))
            crypto = [e for e in events if "btc" in e.get("title", e.get("name","")).lower()]
            if not crypto:
                await bot.send_message(chat_id=chat_id, text="📈 No BTC markets found on Bayse.")
                return
            event = crypto[0]
            markets = event.get("markets", [])
            if not markets:
                await bot.send_message(chat_id=chat_id, text="📈 No BTC markets available.")
                return
            bal = get_balance()
            amount = bal * CRYPTO_ALLOC
            side = "buy" if "BUY" in signal else "sell"
            result = place_order(event.get("id"), markets[0].get("id"), side, amount)
            last_crypto_trade = now
            trade_log.append(f"{now.strftime('%H:%M')} | BTC {side.upper()} | RSI:{rsi:.1f} | ₦{amount:,.0f}")
            await bot.send_message(chat_id=chat_id,
                text=f"📈 *Crypto Trade!*\n\nSide: `{side.upper()}`\nAmount: `₦{amount:,.2f}`\nRSI: `{rsi:.2f}` | MACD: `{macd:.4f}`\nSignal: `{signal}`\nStatus: `{result.get('status', result.get('id','submitted'))}`",
                parse_mode="Markdown")
        else:
            await bot.send_message(chat_id=chat_id,
                text=f"📈 *Crypto Scan*\n\nSignal: `{signal}`\nRSI: `{rsi:.2f}`\nMACD: `{macd:.4f}`\nNo trade triggered.",
                parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Crypto error: {e}")
        await bot.send_message(chat_id=chat_id, text=f"📈 Crypto scan error: {str(e)}")
await bot.send_message(chat_id=chat_id, text=f"📈 Crypto scan error: {str(e)}")

async def post_init(app: Application):
    logger.info("Gowagr Bot is running!")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).connect_timeout(30).read_timeout(30).write_timeout(30).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("portfolio", portfolio_cmd))
    app.add_handler(CommandHandler("orders", orders_cmd))
    app.add_handler(CommandHandler("activities", activities_cmd))
    app.add_handler(CommandHandler("events", events_cmd))
    app.add_handler(CommandHandler("trades", trades_cmd))
    app.add_handler(CommandHandler("log", log_cmd))
    app.add_handler(CommandHandler("sports", sports_cmd))
    app.add_handler(CommandHandler("crypto", crypto_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
