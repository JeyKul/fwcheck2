import os
import sys
import requests
import asyncio
from datetime import datetime
from telegram import Bot
from telegram.constants import ParseMode
from flask import Flask, request, jsonify

# Configuration
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
GITHUB_REPO = "jeykul/fwcheck2"
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET')  # Optional for security
PORT =  # Port for the webhook server

# Initialize Flask app
app = Flask(__name__)

# Initialize Telegram bot
bot = Bot(token=BOT_TOKEN)

# Global event loop
loop = asyncio.new_event_loop()

def log(message):
    """Helper function for logging"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", file=sys.stderr)

async def send_telegram_message_async(changes):
    """Send formatted message to Telegram (async)"""
    if not changes:
        return

    # Telegram message length limit
    MAX_MESSAGE_LENGTH = 4096

    # Format message header
    header = "📢 *New Firmware Updates Detected\!*\n\n"
    footer = f"\n\n_Checked at {escape_markdown(datetime.now().strftime('%H:%M:%S'))}_"

    # Split changes into chunks
    message_chunks = []
    current_chunk = header

    for change, url in changes:
        change_line = f"• [{escape_markdown(change)}]({escape_markdown(url)})\n"
        
        # If adding this line would exceed the limit, start a new chunk
        if len(current_chunk) + len(change_line) + len(footer) > MAX_MESSAGE_LENGTH:
            message_chunks.append(current_chunk + footer)
            current_chunk = header + change_line
        else:
            current_chunk += change_line

    # Add the last chunk
    if current_chunk != header:
        message_chunks.append(current_chunk + footer)

    # Send all chunks
    for chunk in message_chunks:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=chunk,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True
        )

    log(f"Sent {len(message_chunks)} messages with {len(changes)} changes")

def send_telegram_message_sync(changes):
    """Run the async Telegram message function in the global event loop"""
    asyncio.set_event_loop(loop)
    loop.run_until_complete(send_telegram_message_async(changes))

def escape_markdown(text):
    """Escape reserved MarkdownV2 characters"""
    reserved_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in reserved_chars:
        text = text.replace(char, f'\\{char}')
    return text

@app.route('/webhook', methods=['POST'])
def github_webhook():
    """Handle GitHub webhook events"""
    try:
        log("Received webhook request")
        
        # Verify payload (optional but recommended)
        if WEBHOOK_SECRET:
            signature = request.headers.get('X-Hub-Signature-256', '')
            if not verify_signature(request.data, signature):
                log("Invalid signature")
                return jsonify({"status": "error", "message": "Invalid signature"}), 403

        # Parse JSON payload
        payload = request.json
        if not payload:
            log("Empty payload")
            return jsonify({"status": "error", "message": "Empty payload"}), 400

        # Check if it's a push to the main branch
        if payload.get("ref") != "refs/heads/main":
            log("Ignoring non-main branch push")
            return jsonify({"status": "ignored", "message": "Not a main branch push"}), 200

        # Process commits
        commits = payload.get("commits", [])
        if not commits:
            log("No commits found")
            return jsonify({"status": "ignored", "message": "No commits found"}), 200

        log(f"Processing {len(commits)} new commits")

        # Extract changes
        changes = []
        for commit in commits:
            commit_message = commit.get("message", "")
            commit_url = commit.get("url", "")
            changes.extend([
                (line, commit_url) 
                for line in commit_message.split('\n') 
                if any(x in line for x in ['created', 'updated'])
            ])

        # Send updates to Telegram
        if changes:
            log(f"Sending {len(changes)} changes to Telegram")
            send_telegram_message_sync(changes)

        log("Webhook processed successfully")
        return jsonify({"status": "success", "message": f"Processed {len(changes)} changes"}), 200

    except Exception as e:
        log(f"Error processing webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

def verify_signature(payload, signature):
    """Verify GitHub webhook signature (optional)"""
    import hmac
    import hashlib
    if not signature:
        return False
    sha_name, signature = signature.split('=')
    if sha_name != 'sha256':
        return False
    mac = hmac.new(WEBHOOK_SECRET.encode(), msg=payload, digestmod=hashlib.sha256)
    return hmac.compare_digest(mac.hexdigest(), signature)

def start_webhook_server():
    """Start the Flask webhook server"""
    log(f"Starting webhook server on port {PORT}...")
    app.run(host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    if not BOT_TOKEN or not CHAT_ID:
        log("Error: Missing environment variables")
        sys.exit(1)

    # Set up the global event loop
    asyncio.set_event_loop(loop)
    try:
        start_webhook_server()
    finally:
        loop.close()
