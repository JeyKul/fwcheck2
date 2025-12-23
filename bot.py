import os
import sys
import json
import hmac
import hashlib
import asyncio
import time
from datetime import datetime
from collections import defaultdict
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

# Performance and message limits
MAX_CHANGES_PER_MESSAGE = 15  # Max updates per message
MAX_MESSAGES_PER_BATCH = 20  # Max messages to send at once
MESSAGE_COOLDOWN = 0.3  # Seconds between sending messages
RATE_LIMIT_DELAY = 1.0  # Seconds delay when rate limited

# Flask app
app = Flask(__name__)

# Telegram bot + application
bot = Bot(token=BOT_TOKEN)
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
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

def format_changes_list(commits):
    """Create a clean list of changes with commit URLs."""
    changes = []
    
    for commit in commits:
        message = commit.get("message", "")
        url = commit.get("url", "")
        
        # Skip empty messages
        if not message:
            continue
            
        # Get the first line of the commit message (main change)
        lines = message.strip().split('\n')
        main_line = lines[0].strip()
        
        # Format: "CSC/MODEL: VERSION (Android X)"
        if ':' in main_line:
            changes.append((main_line, url))
    
    return changes

def create_messages_from_changes(changes):
    """Create Telegram messages from list of changes."""
    if not changes:
        return []
    
    messages = []
    current_chunk = []
    current_length = 0
    
    header = f"📢 *WE GOT UPDATES\\!*\n\n"
    footer = f"\n\n_Last checked at {escape_markdown(datetime.now().strftime('%H:%M:%S'))}_"
    
    for change, url in changes:
        # Format each change: "• CSC/MODEL: VERSION (Android X) (URL)"
        change_text = escape_markdown(change)
        url_text = escape_markdown(url)
        
        # Telegram MarkdownV2 format: [text](url)
        line = f"• [{change_text}]({url_text})\n"
        line_length = len(f"• {change} ({url})\n")  # Approximate length
        
        # Check if adding this line would exceed Telegram's limit
        if current_length + line_length + len(header) + len(footer) > 4000:
            # Create message from current chunk
            message_text = header + "".join(current_chunk) + footer
            messages.append(message_text)
            
            # Start new chunk
            current_chunk = [line]
            current_length = line_length
        else:
            current_chunk.append(line)
            current_length += line_length
        
        # Also limit by number of changes per message
        if len(current_chunk) >= MAX_CHANGES_PER_MESSAGE:
            message_text = header + "".join(current_chunk) + footer
            messages.append(message_text)
            current_chunk = []
            current_length = 0
    
    # Add remaining changes as last message
    if current_chunk:
        message_text = header + "".join(current_chunk) + footer
        messages.append(message_text)
    
    return messages

async def send_telegram_message_async(messages, max_retries=3):
    if not messages or not subscribers:
        return
    
    sent_count = 0
    rate_limit_count = 0
    
    for chat_id in subscribers:
        for i, message in enumerate(messages):
            if sent_count >= MAX_MESSAGES_PER_BATCH:
                log(f"Rate limit reached: sent {sent_count} messages")
                break
            
            for attempt in range(max_retries):
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode=ParseMode.MARKDOWN_V2,
                        disable_web_page_preview=True
                    )
                    sent_count += 1
                    
                    # Add cooldown between messages
                    if i < len(messages) - 1:
                        await asyncio.sleep(MESSAGE_COOLDOWN)
                    
                    break  # Success, break retry loop
                    
                except Exception as e:
                    error_msg = str(e).lower()
                    if "retry after" in error_msg:
                        # Rate limited
                        rate_limit_count += 1
                        wait_time = RATE_LIMIT_DELAY * (2 ** attempt)  # Exponential backoff
                        log(f"Rate limited, waiting {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    elif "message is too long" in error_msg:
                        # Message too long, try to split it
                        log(f"Message too long for {chat_id}, splitting...")
                        # This shouldn't happen with our chunking, but just in case
                        half = len(message) // 2
                        msg1 = message[:half]
                        msg2 = message[half:]
                        await bot.send_message(
                            chat_id=chat_id,
                            text=msg1,
                            parse_mode=ParseMode.MARKDOWN_V2,
                            disable_web_page_preview=True
                        )
                        await asyncio.sleep(MESSAGE_COOLDOWN)
                        await bot.send_message(
                            chat_id=chat_id,
                            text=msg2,
                            parse_mode=ParseMode.MARKDOWN_V2,
                            disable_web_page_preview=True
                        )
                        sent_count += 2
                        break
                    elif attempt == max_retries - 1:
                        log(f"Failed to send message to {chat_id}: {e}")
                    else:
                        await asyncio.sleep(1)  # Short delay before retry
    
    log(f"Sent {sent_count} messages to {len(subscribers)} chats (rate limited {rate_limit_count} times)")

def send_telegram_message_sync(messages):
    try:
        loop.run_until_complete(send_telegram_message_async(messages))
    except Exception as e:
        log(f"Error in send_telegram_message_sync: {e}")

def verify_signature(payload, signature):
    if not signature or not WEBHOOK_SECRET:
        return True  # Skip verification if no secret configured
    
    try:
        sha_name, signature = signature.split('=')
        if sha_name != 'sha256':
            return False
        mac = hmac.new(WEBHOOK_SECRET.encode(), msg=payload, digestmod=hashlib.sha256)
        return hmac.compare_digest(mac.hexdigest(), signature)
    except Exception:
        return False

@app.route('/webhook', methods=['POST'])
def github_webhook():
    start_time = time.time()
    
    try:
        log("=" * 60)
        log("Received webhook request")

        if WEBHOOK_SECRET:
            sig = request.headers.get('X-Hub-Signature-256', '')
            if not verify_signature(request.data, sig):
                log("ERROR: Invalid signature")
                return jsonify({"status": "error", "message": "Invalid signature"}), 403

        payload = request.json
        if not payload:
            log("ERROR: Empty payload")
            return jsonify({"status": "error", "message": "Empty payload"}), 400

        # Check if this is a main branch push
        ref = payload.get("ref", "")
        if ref != "refs/heads/main":
            log(f"INFO: Ignoring non-main branch push: {ref}")
            return jsonify({"status": "ignored", "message": "Not a main branch push"}), 200

        commits = payload.get("commits", [])
        log(f"INFO: Processing {len(commits)} commits")
        
        if not commits:
            log("INFO: No commits found")
            return jsonify({"status": "ignored", "message": "No commits found"}), 200

        # Create list of changes with URLs
        changes = format_changes_list(commits)
        
        if not changes:
            log("INFO: No firmware updates found in commits")
            return jsonify({"status": "ignored", "message": "No firmware updates found"}), 200

        # Create Telegram messages
        messages = create_messages_from_changes(changes)
        log(f"INFO: Created {len(messages)} message(s) from {len(changes)} changes")
        
        # Send to Telegram
        log(f"INFO: Sending updates to {len(subscribers)} subscribers")
        send_telegram_message_sync(messages)
        
        duration = time.time() - start_time
        log(f"SUCCESS: Webhook processed in {duration:.2f}s")
        log("=" * 60)
        
        return jsonify({
            "status": "success", 
            "message": f"Processed {len(changes)} changes in {len(messages)} messages",
            "changes": len(changes),
            "messages": len(messages),
            "duration": f"{duration:.2f}s"
        }), 200

    except Exception as e:
        duration = time.time() - start_time
        log(f"ERROR: Webhook processing failed after {duration:.2f}s: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

# /here command handler
async def here_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    username = update.effective_chat.username or "Unknown"
    
    if chat_id not in subscribers:
        subscribers.add(chat_id)
        save_subscribers(subs)
        await update.message.reply_text(
            "✅ *Subscribed\\!*\n\nThis chat will now receive firmware updates\\.\n\n"
            "_To stop receiving updates, use /stop_",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        log(f"SUBSCRIBE: Chat {chat_id} (@{username}) subscribed")
    else:
        await update.message.reply_text(
            "ℹ️ *Already Subscribed*\n\nThis chat is already receiving firmware updates\\.\n\n"
            "_To stop receiving updates, use /stop_",
            parse_mode=ParseMode.MARKDOWN_V2
        )

# /stop command handler  
async def stop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    username = update.effective_chat.username or "Unknown"
    
    if chat_id in subscribers:
        subscribers.remove(chat_id)
        save_subscribers(subs)
        await update.message.reply_text(
            "❌ *Unsubscribed*\n\nYou will no longer receive firmware updates\\.\n\n"
            "_To subscribe again, use /here_",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        log(f"UNSUBSCRIBE: Chat {chat_id} (@{username}) unsubscribed")
    else:
        await update.message.reply_text(
            "ℹ️ *Not Subscribed*\n\nThis chat was not subscribed to updates\\.\n\n"
            "_To subscribe, use /here_",
            parse_mode=ParseMode.MARKDOWN_V2
        )

# /status command handler
async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    username = update.effective_chat.username or "Unknown"
    
    is_subscribed = chat_id in subscribers
    total_subscribers = len(subscribers)
    
    status_text = f"📊 *Bot Status*\n\n"
    status_text += f"• Your status: {'✅ Subscribed' if is_subscribed else '❌ Not subscribed'}\n"
    status_text += f"• Total subscribers: {total_subscribers}\n"
    status_text += f"• Your chat ID: `{chat_id}`\n"
    status_text += f"• Username: @{username}\n\n"
    
    if is_subscribed:
        status_text += "_Use /stop to unsubscribe_"
    else:
        status_text += "_Use /here to subscribe_"
    
    await update.message.reply_text(
        status_text,
        parse_mode=ParseMode.MARKDOWN_V2
    )

# Register command handlers
application.add_handler(CommandHandler("here", here_handler))
application.add_handler(CommandHandler("stop", stop_handler))
application.add_handler(CommandHandler("status", status_handler))

def start_flask():
    log(f"Starting webhook server on port {PORT}...")
    log(f"Bot token: {BOT_TOKEN[:10]}...")
    log(f"Webhook secret: {'Set' if WEBHOOK_SECRET else 'Not set'}")
    log(f"Subscribers: {len(subscribers)}")
    log("=" * 60)
    
    app.run(host="0.0.0.0", port=PORT, threaded=True)

if __name__ == "__main__":
    if not BOT_TOKEN:
        log("ERROR: TELEGRAM_BOT_TOKEN environment variable not set.")
        sys.exit(1)

    # Start Telegram polling in background
    import threading
    def start_polling():
        try:
            log("Starting Telegram bot polling...")
            application.run_polling()
        except Exception as e:
            log(f"ERROR in polling: {e}")
    
    polling_thread = threading.Thread(target=start_polling, daemon=True)
    polling_thread.start()
    
    # Give polling thread a moment to start
    time.sleep(2)
    
    # Start webhook server
    try:
        start_flask()
    except KeyboardInterrupt:
        log("Shutting down...")
    except Exception as e:
        log(f"ERROR in Flask server: {e}")
    finally:
        # Save subscribers before exiting
        save_subscribers(subscribers)
        log("Subscribers saved")
