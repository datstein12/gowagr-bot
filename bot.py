import os
import requests
import hmac
import hashlib
import base64
import json
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
PUBLIC_KEY = os.getenv("BAYSE_API_KEY", "")
SECRET_KEY = os.getenv("BAYSE_API_SECRET", "")
BASE_URL = os.getenv("BAYSE_BASE_URL", "https://relay.bayse.markets")

PICK_MARKET, PICK_SIDE, PICK_AMOUNT = range(3)
user_trade = {}

def rh():
    return {"X-Public-Key": PUBLIC_KEY, "Content-Type": "application/json"}

def wh(method, path, body=""):
    ts = str(int(time.time()))
    bh = hashlib.sha256(body.encode()).hexdigest()
    payload = f"{ts}.{method}.{path}.{bh}"
    sig = base64.b64encode(
        hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).digest()
    ).decode()
    return {"X-Public-Key": PUBLIC_KEY, "X-Timestamp": ts, "X-Signature": sig, "Content-Type": "application/json"}

def api_get(path):
    try:
        r = requests.get(f"{BASE_URL}{path}", headers=rh(), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def api_post(path, body):
    try:
        b = json.dumps(body)
        r = requests.post(f"{BASE_URL}{path}", headers=wh("POST", path, b), data=b, timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def api_delete(path):
    try:
        r = requests.delete(f"{BASE_URL}{path}", headers=wh("DELETE", path), timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def get_balance():
    data = api_get("/v1/wallet/assets")
    assets = data.get("assets", [])
    if assets:
        return float(assets[0].get("availableBalance", 0))
    return 0

def main_menu():
    keyboard = [
        [InlineKeyboardButton("💰 Balance", callback_data="balance"),
         InlineKeyboardButton("📊 Portfolio", callback_data="portfolio")],
        [InlineKeyboardButton("📈 Markets", callback_data="markets"),
         InlineKeyboardButton("📋 My Orders", callback_data="orders")],
        [InlineKeyboardButton("🔄 My Trades", callback_data="trades"),
         InlineKeyboardButton("📜 Activities", callback_data="activities")],
        [InlineKeyboardButton("🛒 Place Trade", callback_data="place"),
         InlineKeyboardButton("❌ Cancel Order", callback_data="cancel")],
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to *Gowagr Bot*!\n\nYour manual trading assistant for Bayse Markets.\n\nWhat would you like to do?",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

async def menu_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Choose an action:", reply_markup=main_menu())

async def show_balance(bot, chat_id):
    bal = get_balance()
    await bot.send_message(
        chat_id=chat_id,
        text=f"💰 *Wallet Balance*\n\n`₦{bal:,.2f}`",
        parse_mode="Markdown"
    )

async def show_portfolio(bot, chat_id):
    data = api_get("/v1/pm/portfolio")
    if "error" in data:
        await bot.send_message(chat_id=chat_id, text=f"❌ Error: {data['error']}")
        return
    positions = data.get("positions", data.get("data", []))
    if not positions:
        await bot.send_message(chat_id=chat_id, text="📊 No open positions.")
        return
    msg = "📊 *Portfolio*\n\n"
    for p in positions[:15]:
        name = p.get("market", p.get("title", p.get("name", "N/A")))
        shares = p.get("shares", p.get("amount", "N/A"))
        value = p.get("value", p.get("currentValue", ""))
        msg += f"• {name}\n  Shares: `{shares}`"
        if value:
            msg += f" | Value: `₦{float(value):,.2f}`"
        msg += "\n\n"
    await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

async def show_markets(bot, chat_id):
    data = api_get("/v1/pm/events")
    if "error" in data:
        await bot.send_message(chat_id=chat_id, text=f"❌ Error: {data['error']}")
        return
    events = data.get("events", data.get("data", []))
    if not events:
        await bot.send_message(chat_id=chat_id, text="📈 No markets available.")
        return
    msg = "📈 *Available Markets*\n\n"
    for e in events[:15]:
        title = e.get("title", e.get("name", "N/A"))
        eid = e.get("id", "N/A")
        status = e.get("status", "")
        msg += f"• *{title}*\n  ID: `{eid}`"
        if status:
            msg += f" | {status}"
        msg += "\n\n"
    await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

async def show_orders(bot, chat_id):
    data = api_get("/v1/pm/orders")
    if "error" in data:
        await bot.send_message(chat_id=chat_id, text=f"❌ Error: {data['error']}")
        return
    orders = data.get("orders", data.get("data", []))
    if not orders:
        await bot.send_message(chat_id=chat_id, text="📋 No open orders.")
        return
    msg = "📋 *My Orders*\n\n"
    for o in orders[:15]:
        oid = o.get("id", "N/A")
        market = o.get("market", o.get("title", "N/A"))
        side = o.get("side", "N/A")
        amount = o.get("amount", 0)
        status = o.get("status", "N/A")
        msg += f"• {market}\n  ID: `{oid}`\n  Side: `{side}` | Amount: `₦{float(amount):,.2f}` | Status: `{status}`\n\n"
    await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

async def show_trades(bot, chat_id):
    data = api_get("/v1/pm/trades")
    if "error" in data:
        await bot.send_message(chat_id=chat_id, text=f"❌ Error: {data['error']}")
        return
    trades = data.get("trades", data.get("data", []))
    if not trades:
        await bot.send_message(chat_id=chat_id, text="🔄 No trades found.")
        return
    msg = "🔄 *My Trades*\n\n"
    for t in trades[:15]:
        market = t.get("market", t.get("title", "N/A"))
        side = t.get("side", "N/A")
        amount = t.get("amount", 0)
        price = t.get("price", "")
        msg += f"• {market}\n  Side: `{side}` | Amount: `₦{float(amount):,.2f}`"
        if price:
            msg += f" | Price: `{price}`"
        msg += "\n\n"
    await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

async def show_activities(bot, chat_id):
    data = api_get("/v1/pm/activities")
    if "error" in data:
        await bot.send_message(chat_id=chat_id, text=f"❌ Error: {data['error']}")
        return
    acts = data.get("activities", data.get("data", []))
    if not acts:
        await bot.send_message(chat_id=chat_id, text="📜 No activities.")
        return
    msg = "📜 *Recent Activities*\n\n"
    for a in acts[:15]:
        atype = a.get("type", "N/A")
        desc = a.get("description", a.get("market", a.get("title", "N/A")))
        amount = a.get("amount", "")
        msg += f"• `{atype}` — {desc}"
        if amount:
            msg += f" | `₦{float(amount):,.2f}`"
        msg += "\n"
    await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    d = query.data

    if d == "balance":
        await show_balance(ctx.bot, chat_id)
    elif d == "portfolio":
        await show_portfolio(ctx.bot, chat_id)
    elif d == "markets":
        await show_markets(ctx.bot, chat_id)
    elif d == "orders":
        await show_orders(ctx.bot, chat_id)
    elif d == "trades":
        await show_trades(ctx.bot, chat_id)
    elif d == "activities":
        await show_activities(ctx.bot, chat_id)
    elif d == "place":
        await ctx.bot.send_message(
            chat_id=chat_id,
            text="🛒 *Place a Trade*\n\nSend the *Event ID* you want to trade on.\n\nUse 📈 Markets to find Event IDs.",
            parse_mode="Markdown"
        )
        ctx.user_data["step"] = "event_id"
    elif d == "cancel":
        await ctx.bot.send_message(
            chat_id=chat_id,
            text="❌ *Cancel Order*\n\nSend the *Order ID* you want to cancel.\n\nUse 📋 My Orders to find Order IDs.",
            parse_mode="Markdown"
        )
        ctx.user_data["step"] = "cancel_id"
    elif d.startswith("side_"):
        side = d.replace("side_", "")
        ctx.user_data["side"] = side
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=f"✅ Side: *{side.upper()}*\n\nNow send the *amount* in ₦ you want to trade.\n\nExample: `500`",
            parse_mode="Markdown"
        )
        ctx.user_data["step"] = "amount"

async def message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    step = ctx.user_data.get("step")
    text = update.message.text.strip()
    chat_id = update.effective_chat.id

    if step == "event_id":
        ctx.user_data["event_id"] = text
        data = api_get(f"/v1/pm/events/{text}")
        if "error" in data:
            await update.message.reply_text(f"❌ Event not found: {data['error']}\n\nTry again with /menu")
            ctx.user_data.clear()
            return
        markets = data.get("markets", [])
        if not markets:
            await update.message.reply_text("❌ No markets found for this event.")
            ctx.user_data.clear()
            return
        msg = f"📈 *Markets for this event:*\n\n"
        for m in markets[:10]:
            mid = m.get("id", "N/A")
            name = m.get("name", m.get("title", "N/A"))
            price = m.get("price", m.get("probability", "N/A"))
            msg += f"• {name}\n  ID: `{mid}` | Price: `{price}`\n\n"
        msg += "Send the *Market ID* you want to trade:"
        await update.message.reply_text(msg, parse_mode="Markdown")
        ctx.user_data["step"] = "market_id"

    elif step == "market_id":
        ctx.user_data["market_id"] = text
        keyboard = [
            [InlineKeyboardButton("🟢 BUY", callback_data="side_buy"),
             InlineKeyboardButton("🔴 SELL", callback_data="side_sell")]
        ]
        await update.message.reply_text(
            "Choose your side:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif step == "amount":
        try:
            amount = float(text)
        except:
            await update.message.reply_text("❌ Invalid amount. Send a number like `500`", parse_mode="Markdown")
            return
        event_id = ctx.user_data.get("event_id")
        market_id = ctx.user_data.get("market_id")
        side = ctx.user_data.get("side")
        await update.message.reply_text(f"⏳ Placing {side.upper()} order for ₦{amount:,.2f}...")
        result = api_post(f"/v1/pm/events/{event_id}/markets/{market_id}/orders", {"side": side, "amount": amount})
        if "error" in result:
            await update.message.reply_text(f"❌ Trade failed: {result['error']}")
        else:
            order_id = result.get("id", result.get("orderId", "N/A"))
            status = result.get("status", "submitted")
            await update.message.reply_text(
                f"✅ *Trade Placed!*\n\nOrder ID: `{order_id}`\nSide: `{side.upper()}`\nAmount: `₦{amount:,.2f}`\nStatus: `{status}`",
                parse_mode="Markdown"
            )
        ctx.user_data.clear()

    elif step == "cancel_id":
        await update.message.reply_text(f"⏳ Cancelling order `{text}`...", parse_mode="Markdown")
        result = api_delete(f"/v1/pm/orders/{text}")
        if "error" in result:
            await update.message.reply_text(f"❌ Failed: {result['error']}")
        else:
            await update.message.reply_text(f"✅ Order `{text}` cancelled!", parse_mode="Markdown")
        ctx.user_data.clear()

async def post_init(app: Application):
    print("✅ Gowagr Bot is running!")

def main():
    app = Application.builder().token(TOKEN).post_init(post_init).connect_timeout(30).read_timeout(30).write_timeout(30).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
