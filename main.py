import os
import json
import logging
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Dispatcher,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing. Add it in Choreo Secrets.")

CHANNEL_ID = -1003370713141  # Replace with your channel ID
DB_PATH = "video_db.json"
PAGE_SIZE = 6

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)

# ---------------- DB HELPERS ----------------
def load_db():
    if not os.path.exists(DB_PATH):
        return []
    try:
        with open(DB_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.error("Failed to load DB: %s", e)
        return []

def save_db(entries):
    try:
        with open(DB_PATH, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Failed to save DB: %s", e)

def add_or_update_entry(title: str, file_id: str, msg_id: int):
    entries = load_db()
    for e in entries:
        if e.get("msg_id") == msg_id:
            e.update({"title": title, "file_id": file_id})
            save_db(entries)
            return
    new = {"id": str(msg_id), "title": title, "file_id": file_id, "msg_id": msg_id}
    entries.append(new)
    save_db(entries)

# ---------------- BOT HANDLERS ----------------
def send_menu_page(chat_id: int, page: int):
    entries = load_db()
    total = len(entries)
    if total == 0:
        bot.send_message(chat_id=chat_id, text="No videos available yet.")
        return

    start_idx = page * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    slice_entries = entries[start_idx:end_idx]

    keyboard = []
    for e in slice_entries:
        keyboard.append([InlineKeyboardButton(e["title"], callback_data=f"play:{e['id']}")])

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅ Prev", callback_data=f"page:{page-1}"))
    nav_row.append(InlineKeyboardButton(f"Page {page+1}/{(total-1)//PAGE_SIZE+1}", callback_data="noop"))
    if end_idx < total:
        nav_row.append(InlineKeyboardButton("Next ➡", callback_data=f"page:{page+1}"))
    keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("Refresh DB", callback_data="refresh_db")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    bot.send_message(chat_id=chat_id, text="Select a video:", reply_markup=reply_markup)

# Telegram Command Handlers
def start(update: Update, context):
    entries = load_db()
    if not entries:
        update.message.reply_text(
            "Hello! No videos yet in the channel.\nForward/upload videos to your storage channel."
        )
        return
    send_menu_page(chat_id=update.message.chat_id, page=0)

def callback_handler(update: Update, context):
    query = update.callback_query
    query.answer()
    data = query.data
    if not data or data == "noop":
        return
    if data == "refresh_db":
        query.message.delete()
        send_menu_page(chat_id=query.message.chat_id, page=0)
        return
    if data.startswith("page:"):
        try:
            page = int(data.split(":", 1)[1])
        except Exception:
            page = 0
        query.message.delete()
        send_menu_page(chat_id=query.message.chat_id, page=page)
        return
    if data.startswith("play:"):
        vid_id = data.split(":", 1)[1]
        entries = load_db()
        entry = next((e for e in entries if e["id"] == vid_id), None)
        if not entry:
            query.message.reply_text("Video not found. Try Refresh.")
            return
        file_id = entry.get("file_id")
        title = entry.get("title", "Video")
        try:
            bot.send_document(chat_id=query.message.chat_id, document=file_id, filename=f"{title}.mp4", caption=title)
        except Exception as e:
            logger.exception("Failed to send document: %s", e)
            query.message.reply_text(
                "Failed to send video. It might be protected or file_id expired. Re-upload to channel if needed."
            )

def channel_post_listener(update: Update, context):
    msg = update.channel_post
    if not msg or msg.chat.id != CHANNEL_ID:
        return
    caption = (msg.caption or "").strip()
    msg_id = msg.message_id
    if msg.document:
        add_or_update_entry(title=caption or f"Document {msg_id}", file_id=msg.document.file_id, msg_id=msg_id)
        logger.info("Saved document: %s", caption)
    elif msg.video:
        add_or_update_entry(title=caption or f"Video {msg_id}", file_id=msg.video.file_id, msg_id=msg_id)
        logger.info("Saved video: %s", caption)
    elif msg.photo:
        add_or_update_entry(title=caption or f"Photo {msg_id}", file_id=msg.photo[-1].file_id, msg_id=msg_id)
        logger.info("Saved photo: %s", caption)
    else:
        logger.info("Ignored channel post: %s", msg_id)

# ---------------- FLASK WEBHOOK ----------------
app = Flask(__name__)
dispatcher = Dispatcher(bot, None, workers=0)

# Register handlers
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CallbackQueryHandler(callback_handler))
dispatcher.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, channel_post_listener))

@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return "ok"

@app.route("/")
def home():
    return "Bot is alive!"

# ---------------- MAIN ----------------
if __name__ == "__main__":
    # Set webhook on startup
    WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # e.g. https://yourapp.choreoapps.dev/your_bot_token
    if not WEBHOOK_URL:
        raise RuntimeError("WEBHOOK_URL missing. Add it in Choreo Secrets.")
    bot.set_webhook(url=WEBHOOK_URL)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))
