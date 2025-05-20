import os
import sys
import json
import hmac
import hashlib
import asyncio
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Bot, Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Configuration
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GITHUB_REPO = "jeykul/fwcheck2"
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET')  # Optional
PORT = 51232
SUBSCRIBERS_FILE = "subscribers.json"

# Flask app
app = Flask(__name__)

# Telegram bot + application
bot = Bot(token=BOT_TOKEN)
loop = asyncio.new_event_loop()
application = ApplicationBuilder().token(BOT_TOKEN).build()

# Subscribers (loaded/saved to file)
def load_subscribers():
    try:
        with open(SUBSCRIBERS_FILE, 'r') as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_subscribers(subs):
    with open(SUBSCRIBERS_FILE, 'w') as f:
        json.dump(list(subs), f)

subscribers = load_subscribers()

def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", file=sys.stderr)

def escape_markdown(text):
    reserved = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in reserved:
        text = text.replace(char, f'\\{char}')
    return text

async def send_telegram_message_async(changes):
    if not changes:
        return

    MAX_LEN = 4096
    header = "📢 *WE GOT UPDATES\!*\n\n"
    footer = f"\n\n_Last checked at {escape_markdown(datetime.now().strftime('%H:%M:%S'))}_"
    chunks = []
    current = header

    for change, url in changes:
        line = f"• [{escape_markdown(change)}]({escape_markdown(url)})\n"
        if len(current) + len(line) + len(footer) > MAX_LEN:
            chunks.append(current + footer)
            current = header + line
        else:
            current += line

    if current != header:
        chunks.append(current + footer)

    for chat_id in subscribers:
        for chunk in chunks:
            await bot.send_message(
                chat_id=chat_id,
                text=chunk,
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_web_page_preview=True
            )

    log(f"Sent {len(chunks)} messages to {len(subscribers)} chats")

def send_telegram_message_sync(changes):
    asyncio.set_event_loop(loop)
    loop.run_until_complete(send_telegram_message_async(changes))

def verify_signature(payload, signature):
    if not signature:
        return False
    sha_name, signature = signature.split('=')
    if sha_name != 'sha256':
        return False
    mac = hmac.new(WEBHOOK_SECRET.encode(), msg=payload, digestmod=hashlib.sha256)
    return hmac.compare_digest(mac.hexdigest(), signature)

@app.route('/webhook', methods=['POST'])
def github_webhook():
    try:
        log("Received webhook request")

        if WEBHOOK_SECRET:
            sig = request.headers.get('X-Hub-Signature-256', '')
            if not verify_signature(request.data, sig):
                log("Invalid signature")
                return jsonify({"status": "error", "message": "Invalid signature"}), 403

        payload = request.json
        if not payload:
            log("Empty payload")
            return jsonify({"status": "error", "message": "Empty payload"}), 400

        if payload.get("ref") != "refs/heads/main":
            log("Ignoring non-main branch push")
            return jsonify({"status": "ignored", "message": "Not a main branch push"}), 200

        commits = payload.get("commits", [])
        if not commits:
            log("No commits found")
            return jsonify({"status": "ignored", "message": "No commits found"}), 200

        log(f"Processing {len(commits)} new commits")
        changes = []

        for commit in commits:
            message = commit.get("message", "")
            url = commit.get("url", "")
            changes += [
                (line, url)
                for line in message.split('\n')
                if any(x in line.lower() for x in ['created', 'updated'])
            ]

        if changes:
            log(f"Sending {len(changes)} changes to Telegram")
            send_telegram_message_sync(changes)

        log("Webhook processed successfully")
        return jsonify({"status": "success", "message": f"Processed {len(changes)} changes"}), 200

    except Exception as e:
        log(f"Error processing webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# /here command handler
async def here_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in subscribers:
        subscribers.add(chat_id)
        save_subscribers(subscribers)
        await update.message.reply_text("✅ This chat is now subscribed to firmware updates.")
        log(f"Subscribed chat: {chat_id}")
    else:
        await update.message.reply_text("ℹ️ This chat is already subscribed.")

# Register /here
application.add_handler(CommandHandler("here", here_handler))

def start_flask():
    log(f"Starting webhook server on port {PORT}...")
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    if not BOT_TOKEN:
        log("Error: TELEGRAM_BOT_TOKEN not set.")
        sys.exit(1)

    # Start Telegram polling in background
    import threading
    threading.Thread(target=lambda: application.run_polling(), daemon=True).start()

    # Start webhook server
    try:
        start_flask()
    finally:
        loop.close()
