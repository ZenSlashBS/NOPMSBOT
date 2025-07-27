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
cur.execute('''
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
''')
conn.commit()

# Get or set default mode
cur.execute('SELECT value FROM settings WHERE key = "msg_mode"')
mode_row = cur.fetchone()
if not mode_row:
    cur.execute('INSERT INTO settings (key, value) VALUES ("msg_mode", "topic")')
    conn.commit()

def get_mode():
    cur.execute('SELECT value FROM settings WHERE key = "msg_mode"')
    return cur.fetchone()[0]

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

    if user_id == ADMIN_ID:
        # Admin specific message
        current_mode = get_mode()
        topic_emoji = " ✅" if current_mode == "topic" else ""
        bot_emoji = " ✅" if current_mode == "bot" else ""
        mode_buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"🤖 Bot Msg Mode{bot_emoji}", callback_data="set_mode:bot"),
            InlineKeyboardButton(f"📂 Topic Msg Mode{topic_emoji}", callback_data="set_mode:topic")
        ]])
        admin_text = (
            "👋 Welcome, Admin!\n\n"
            "🛠️ Modes Explanation:\n"
            "- 🤖 Bot Msg Mode: User messages are forwarded to your PM with the bot (with forward tag). To reply, reply to the forwarded message in your PM with the bot.\n"
            "- 📂 Topic Msg Mode (Default): User messages create separate topics in the group for organized threaded conversations.\n\n"
            "📢 Other Commands:\n"
            "- /broadcast Msg -btnname:btnlink, ... --imglink.jpg: Send broadcast to all users.\n"
            "- /users: See total number of users.\n\n"
            "Select mode below:"
        )
        await update.message.reply_text(admin_text, reply_markup=mode_buttons)
        return

    # Regular user welcome
    button = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 HazexPy", url="t.me/HazexPy")]])
    welcome_text = f"👋 Welcome, {first_name}! Send me any message, and I'll forward it to the owner anonymously."

    if profile_photo_id:
        await update.message.reply_photo(photo=profile_photo_id, caption=welcome_text, reply_markup=button)
    else:
        await update.message.reply_text(welcome_text, reply_markup=button)

    current_mode = get_mode()

    # Create or get topic if topic mode
    topic_id = None
    if current_mode == "topic":
        cur.execute('SELECT topic_id FROM mappings WHERE user_id = ?', (user_id,))
        row = cur.fetchone()
        if row:
            topic_id = row[0]
        else:
            try:
                new_topic = await context.bot.create_forum_topic(
                    chat_id=GROUP_ID,
                    name=f"Message from {first_name} ({user_id})"
                )
                topic_id = new_topic.message_thread_id
                cur.execute('INSERT INTO mappings (user_id, topic_id) VALUES (?, ?)', (user_id, topic_id))
                conn.commit()
            except BadRequest as e:
                logger.error(f"Failed to create topic: {e}")
                return

    if is_first:
        # Notify admin on first start
        premium_status = "⭐ Premium" if is_premium else "Non-Premium"
        username_display = f'@{username}' if username else 'NONE'
        direct_link = f'<a href="tg://user?id={user_id}">Message User</a>'
        info_text = f'🆕 New user started the bot!\nName: {first_name}\nID: <a href="tg://user?id={user_id}">{user_id}</a>\nUsername: {username_display}\nStatus: {premium_status}\nDirect: {direct_link}'

        ban_button = InlineKeyboardMarkup([[InlineKeyboardButton("🚫 Ban", callback_data=f"ban:{user_id}")]])

        if current_mode == "topic":
            send_func = context.bot.send_photo if profile_photo_id else context.bot.send_message
            await send_func(
                chat_id=GROUP_ID,
                photo=profile_photo_id if profile_photo_id else None,
                caption=info_text if profile_photo_id else None,
                text=info_text if not profile_photo_id else None,
                reply_markup=ban_button,
                parse_mode=ParseMode.HTML,
                message_thread_id=topic_id
            )
        else:
            # Bot mode: Send to admin PM
            send_func = context.bot.send_photo if profile_photo_id else context.bot.send_message
            await send_func(
                chat_id=ADMIN_ID,
                photo=profile_photo_id if profile_photo_id else None,
                caption=info_text if profile_photo_id else None,
                text=info_text if not profile_photo_id else None,
                reply_markup=ban_button,
                parse_mode=ParseMode.HTML
            )

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward private messages to the admin."""
    user_id = update.message.from_user.id

    # Check if banned
    cur.execute('SELECT banned FROM bans WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    is_banned = row[0] if row else 0

    current_mode = get_mode()

    if current_mode == "topic":
        if is_banned:
            await update.message.reply_text("🚫 You are banned by the admin.")
        msg_prefix = "Banned Msg: 🤫 " if is_banned else ""

        user_name = update.message.from_user.full_name

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

        await context.bot.copy_message(
            chat_id=GROUP_ID,
            from_chat_id=update.message.chat_id,
            message_id=update.message.message_id,
            message_thread_id=topic_id,
            caption=msg_prefix + (update.message.caption or "") if update.message.caption else None
        )
    else:
        # Bot mode: Forward to admin PM
        if is_banned:
            await update.message.reply_text("🚫 You are banned by the admin.")

        sent_msg = await context.bot.forward_message(
            chat_id=ADMIN_ID,
            from_chat_id=update.message.chat_id,
            message_id=update.message.message_id
        )

        if is_banned:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text="Banned Msg: 🤫",
                reply_to_message_id=sent_msg.message_id
            )

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle replies from admin in PM to bot."""
    if update.message.from_user.id != ADMIN_ID:
        return

    if not update.message.reply_to_message:
        return

    reply_to = update.message.reply_to_message

    # If replied to "Banned Msg", get the original
    if reply_to.text == "Banned Msg: 🤫" and reply_to.reply_to_message:
        reply_to = reply_to.reply_to_message

    if not reply_to.forward_from:
        return

    user_id = reply_to.forward_from.id

    # Check ban
    cur.execute('SELECT banned FROM bans WHERE user_id = ?', (user_id,))
    ban_row = cur.fetchone()
    if ban_row and ban_row[0]:
        await update.message.reply_text("🚫 Unban the user first to reply.")
        return

    # Send to user
    await context.bot.copy_message(
        chat_id=user_id,
        from_chat_id=update.message.chat_id,
        message_id=update.message.message_id
    )

async def handle_group_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward replies from group topics back to users (for topic mode)."""
    if update.message.chat_id != GROUP_ID:
        return

    if get_mode() != "topic":
        return

    topic_id = update.message.message_thread_id
    if not topic_id:
        return

    sender_id = update.message.from_user.id
    if sender_id != ADMIN_ID:
        return

    cur.execute('SELECT user_id FROM mappings WHERE topic_id = ?', (topic_id,))
    row = cur.fetchone()
    if not row:
        return

    user_id = row[0]

    cur.execute('SELECT banned FROM bans WHERE user_id = ?', (user_id,))
    ban_row = cur.fetchone()
    if ban_row and ban_row[0]:
        await update.message.reply_text("🚫 Unban the user first to reply.")
        return

    await context.bot.copy_message(
        chat_id=user_id,
        from_chat_id=update.message.chat_id,
        message_id=update.message.message_id
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries."""
    query = update.callback_query
    data = query.data

    if data.startswith("set_mode:"):
        new_mode = data.split(":")[1]
        cur.execute('INSERT OR REPLACE INTO settings (key, value) VALUES ("msg_mode", ?)', (new_mode,))
        conn.commit()

        topic_emoji = " ✅" if new_mode == "topic" else ""
        bot_emoji = " ✅" if new_mode == "bot" else ""
        new_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"🤖 Bot Msg Mode{bot_emoji}", callback_data="set_mode:bot"),
            InlineKeyboardButton(f"📂 Topic Msg Mode{topic_emoji}", callback_data="set_mode:topic")
        ]])
        await query.edit_message_reply_markup(reply_markup=new_markup)
        await query.answer(f"Mode set to {new_mode.capitalize()} Msg Mode ✅")
        return

    if data.startswith("ban:") or data.startswith("unban:"):
        user_id = int(data.split(":")[1])
        is_ban = data.startswith("ban:")

        new_banned = 1 if is_ban else 0
        cur.execute('UPDATE bans SET banned = ? WHERE user_id = ?', (new_banned, user_id))
        conn.commit()

        new_text = "✅ Unban" if is_ban else "🚫 Ban"
        new_data = f"unban:{user_id}" if is_ban else f"ban:{user_id}"
        new_markup = InlineKeyboardMarkup([[InlineKeyboardButton(new_text, callback_data=new_data)]])

        await query.edit_message_reply_markup(reply_markup=new_markup)
        await query.answer("User " + ("banned" if is_ban else "unbanned") + " ✅")

    elif data == "broadcast_post":
        await query.answer("Broadcasting... 📢")

        message = query.message

        original_markup = message.reply_markup.inline_keyboard
        user_rows = original_markup[:-1]
        user_markup = InlineKeyboardMarkup(user_rows) if user_rows else None

        await query.edit_message_reply_markup(reply_markup=user_markup)

        cur.execute('SELECT user_id FROM users')
        users = cur.fetchall()
        sent_count = 0
        for row in users:
            uid = row[0]
            try:
                if message.photo:
                    await context.bot.send_photo(chat_id=uid, photo=message.photo[-1].file_id, caption=message.caption, reply_markup=user_markup, parse_mode=ParseMode.HTML)
                else:
                    await context.bot.send_message(chat_id=uid, text=message.text, reply_markup=user_markup, parse_mode=ParseMode.HTML)
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

    user_rows = [[InlineKeyboardButton(name, url=link)] for name, link in buttons]
    user_markup = InlineKeyboardMarkup(user_rows) if buttons else None

    action_row = [
        InlineKeyboardButton("📤 Post", callback_data="broadcast_post"),
        InlineKeyboardButton("❌ Cancel", callback_data="broadcast_cancel")
    ]

    full_rows = user_rows + [action_row]
    full_markup = InlineKeyboardMarkup(full_rows)

    if img_url:
        try:
            await update.message.reply_photo(photo=img_url, caption=msg, reply_markup=full_markup, parse_mode=ParseMode.HTML)
        except Exception as e:
            await update.message.reply_text(f"Error with image: {e}")
            return
    else:
        await update.message.reply_text(text=msg, reply_markup=full_markup, parse_mode=ParseMode.HTML)

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
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND & ~filters.User(ADMIN_ID), handle_private_message))
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.User(ADMIN_ID) & ~filters.COMMAND, handle_admin_reply))
    application.add_handler(MessageHandler(filters.ChatType.GROUPS, handle_group_reply))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("users", users_command))

    # Start polling
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
