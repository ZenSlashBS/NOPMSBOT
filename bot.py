import os
import sqlite3
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, UserProfilePhotos
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.error import BadRequest

# Setup logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables (do not hardcode!)
TOKEN = os.getenv('BOT_TOKEN')
GROUP_ID = int(os.getenv('GROUP_ID'))  # e.g., -1001234567890 (forum group with topics)
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))  # Your Telegram user ID; default 0 (which won't match anyone)

# Database setup
conn = sqlite3.connect('mappings.db')
cur = conn.cursor()

# Create tables if not exists
cur.execute('''
    CREATE TABLE IF NOT EXISTS mappings (
        user_id INTEGER PRIMARY KEY,
        topic_id INTEGER
    )
''')
cur.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        is_premium INTEGER,
        profile_photo_id TEXT
    )
''')
cur.execute('''
    CREATE TABLE IF NOT EXISTS bans (
        user_id INTEGER PRIMARY KEY,
        banned INTEGER DEFAULT 0
    )
''')
conn.commit()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command in PM."""
    user = update.message.from_user
    user_id = user.id
    username = user.username
    first_name = user.first_name or "User"
    is_premium = 1 if user.is_premium else 0

    # Get profile photos
    photos: UserProfilePhotos = await context.bot.get_user_profile_photos(user_id, limit=1)
    profile_photo_id = None
    if photos.total_count > 0:
        profile_photo_id = photos.photos[0][-1].file_id  # Largest photo

    # Check if first time
    cur.execute('SELECT 1 FROM users WHERE user_id = ?', (user_id,))
    is_first = not cur.fetchone()

    # Insert or update user in DB
    cur.execute('''
        INSERT OR REPLACE INTO users (user_id, username, first_name, is_premium, profile_photo_id)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, username, first_name, is_premium, profile_photo_id))
    cur.execute('INSERT OR IGNORE INTO bans (user_id) VALUES (?)', (user_id,))
    conn.commit()

    # Welcome message with button
    button = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 HazexPy", url="t.me/HazexPy")]])
    welcome_text = f"👋 Welcome, {first_name}! Send me any message, and I'll forward it to the owner anonymously."

    if profile_photo_id:
        await update.message.reply_photo(photo=profile_photo_id, caption=welcome_text, reply_markup=button)
    else:
        await update.message.reply_text(welcome_text, reply_markup=button)

    if is_first:
        # Notify group only on first start
        premium_status = "⭐ Premium" if is_premium else "Non-Premium"
        username_display = f'@{username}' if username else 'NONE'
        direct_link = f'<a href="tg://user?id={user_id}">Message User</a>'
        info_text = f'🆕 New user started the bot!\nName: {first_name}\nID: <a href="tg://user?id={user_id}">{user_id}</a>\nUsername: {username_display}\nStatus: {premium_status}\nDirect: {direct_link}'

        ban_button = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Ban", callback_data=f"ban:{user_id}")]])

        if profile_photo_id:
            await context.bot.send_photo(chat_id=GROUP_ID, photo=profile_photo_id, caption=info_text, reply_markup=ban_button, parse_mode=ParseMode.HTML)
        else:
            await context.bot.send_message(chat_id=GROUP_ID, text=info_text, reply_markup=ban_button, parse_mode=ParseMode.HTML)

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward private messages to the group topic."""
    user_id = update.message.from_user.id

    # Check if banned
    cur.execute('SELECT banned FROM bans WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    is_banned = row[0] if row else 0

    if is_banned:
        await update.message.reply_text("🚫 You are banned by the admin. Your messages are still seen but no replies.")
        msg_prefix = "Banned Msg: 🤫 "
    else:
        msg_prefix = ""

    user_name = update.message.from_user.full_name

    # Check if topic exists
    cur.execute('SELECT topic_id FROM mappings WHERE user_id = ?', (user_id,))
    row = cur.fetchone()

    if row:
        topic_id = row[0]
    else:
        try:
            new_topic = await context.bot.create_forum_topic(
                chat_id=GROUP_ID,
                name=f"Message from {user_name} ({user_id})"
            )
            topic_id = new_topic.message_thread_id
            cur.execute('INSERT INTO mappings (user_id, topic_id) VALUES (?, ?)', (user_id, topic_id))
            conn.commit()
        except BadRequest as e:
            logger.error(f"Failed to create topic: {e}")
            await update.message.reply_text("Sorry, there was an internal error. Please try again later.")
            return

    # Copy the message to the topic
    await context.bot.copy_message(
        chat_id=GROUP_ID,
        from_chat_id=update.message.chat_id,
        message_id=update.message.message_id,
        message_thread_id=topic_id,
        caption=msg_prefix + (update.message.caption or "") if update.message.caption else None
    )

async def handle_group_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward replies from group topics back to users, only if from admin and user not banned."""
    if update.message.chat_id != GROUP_ID:
        return

    topic_id = update.message.message_thread_id
    if not topic_id:
        return

    # Check if sender is admin
    sender_id = update.message.from_user.id
    if sender_id != ADMIN_ID:
        return

    # Find user
    cur.execute('SELECT user_id FROM mappings WHERE topic_id = ?', (topic_id,))
    row = cur.fetchone()
    if not row:
        return

    user_id = row[0]

    # Check if banned
    cur.execute('SELECT banned FROM bans WHERE user_id = ?', (user_id,))
    ban_row = cur.fetchone()
    if ban_row and ban_row[0]:
        await update.message.reply_text("🚫 Unban the user first to reply.")
        return

    # Forward reply
    await context.bot.copy_message(
        chat_id=user_id,
        from_chat_id=update.message.chat_id,
        message_id=update.message.message_id
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries for ban/unban and broadcast."""
    query = update.callback_query
    data = query.data

    if data.startswith("ban:") or data.startswith("unban:"):
        user_id = int(data.split(":")[1])
        is_ban = data.startswith("ban:")

        # Update ban status
        new_banned = 1 if is_ban else 0
        cur.execute('UPDATE bans SET banned = ? WHERE user_id = ?', (new_banned, user_id))
        conn.commit()

        # New button
        new_text = "✅ Unban" if is_ban else "🚫 Ban"
        new_data = f"unban:{user_id}" if is_ban else f"ban:{user_id}"
        new_markup = InlineKeyboardMarkup([[InlineKeyboardButton(new_text, callback_data=new_data)]])

        await query.edit_message_reply_markup(reply_markup=new_markup)
        await query.answer("User " + ("banned" if is_ban else "unbanned") + " ✅")

    elif data == "broadcast_post":
        await query.answer("Broadcasting... 📢")

        # Get message
        message = query.message

        # Build user markup (without action buttons)
        original_markup = message.reply_markup.inline_keyboard
        user_rows = original_markup[:-1]  # Exclude last row (action)
        user_markup = InlineKeyboardMarkup(user_rows) if user_rows else None

        # Edit preview to remove action buttons, keep user buttons
        await query.edit_message_reply_markup(reply_markup=user_markup)

        # Broadcast to all users
        cur.execute('SELECT user_id FROM users')
        users = cur.fetchall()
        sent_count = 0
        for row in users:
            uid = row[0]
            try:
                if message.photo:
                    await context.bot.send_photo(chat_id=uid, photo=message.photo[-1].file_id, caption=message.caption, reply_markup=user_markup)
                else:
                    await context.bot.send_message(chat_id=uid, text=message.text, reply_markup=user_markup)
                sent_count += 1
            except Exception as e:
                logger.warning(f"Failed to send to {uid}: {e}")
        await message.reply_text(f"✅ Broadcast sent to {sent_count} users!")

    elif data == "broadcast_cancel":
        await query.answer("Cancelled ❌")
        await query.message.delete()

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /broadcast command."""
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 Only admin can broadcast.")
        return

    args = " ".join(context.args)
    if not args:
        await update.message.reply_text("Usage: /broadcast Msg -btnname:btnlink, btnname:btnlink --imglink.jpg")
        return

    # Parse args
    if "--" in args:
        msg_buttons, img_url = args.split("--", 1)
        img_url = img_url.strip()
    else:
        msg_buttons = args
        img_url = ""

    if "-" in msg_buttons:
        msg, buttons_str = msg_buttons.split("-", 1)
        msg = msg.strip()
        buttons_list = [b.strip() for b in buttons_str.split(",")]
        buttons = []
        for b in buttons_list:
            if ":" in b:
                name, link = b.split(":", 1)
                buttons.append((name.strip(), link.strip()))
    else:
        msg = msg_buttons.strip()
        buttons = []

    # Build user markup
    user_rows = [[InlineKeyboardButton(name, url=link)] for name, link in buttons]
    user_markup = InlineKeyboardMarkup(user_rows) if buttons else None

    # Action row
    action_row = [
        InlineKeyboardButton("📤 Post", callback_data="broadcast_post"),
        InlineKeyboardButton("❌ Cancel", callback_data="broadcast_cancel")
    ]

    # Full markup for preview
    full_rows = user_rows + [action_row]
    full_markup = InlineKeyboardMarkup(full_rows)

    # Send preview
    if img_url:
        try:
            await update.message.reply_photo(photo=img_url, caption=msg, reply_markup=full_markup)
        except Exception as e:
            await update.message.reply_text(f"Error with image: {e}")
            return
    else:
        await update.message.reply_text(text=msg, reply_markup=full_markup)

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /users command."""
    if update.message.from_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 Only admin can use this.")
        return

    cur.execute('SELECT COUNT(*) FROM users')
    count = cur.fetchone()[0]
    await update.message.reply_text(f"👥 Total users: {count}")

def main() -> None:
    """Run the bot."""
    application = Application.builder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_private_message))
    application.add_handler(MessageHandler(filters.ChatType.GROUPS, handle_group_reply))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("users", users_command))

    # Start polling
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
