import os
import sqlite3
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Environment variables (do not hardcode!)
TOKEN = os.getenv('BOT_TOKEN')
GROUP_ID = int(os.getenv('GROUP_ID'))  # e.g., -1001234567890 (forum group with topics)
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))  # Your Telegram user ID; default 0 (which won't match anyone)

# Database for mapping users to topics
conn = sqlite3.connect('mappings.db')
cur = conn.cursor()
cur.execute('''
    CREATE TABLE IF NOT EXISTS mappings (
        user_id INTEGER PRIMARY KEY,
        topic_id INTEGER
    )
''')
conn.commit()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command in PM."""
    await update.message.reply_text('Hi! Send me any message, and I\'ll forward it to the owner anonymously.')

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward private messages to the group topic."""
    user_id = update.message.from_user.id
    user_name = update.message.from_user.full_name

    # Check if topic exists for this user
    cur.execute('SELECT topic_id FROM mappings WHERE user_id = ?', (user_id,))
    row = cur.fetchone()

    if row:
        topic_id = row[0]
    else:
        # Create a new topic in the group
        new_topic = await context.bot.create_forum_topic(
            chat_id=GROUP_ID,
            name=f"Message from {user_name} ({user_id})"
        )
        topic_id = new_topic.message_thread_id
        cur.execute('INSERT INTO mappings (user_id, topic_id) VALUES (?, ?)', (user_id, topic_id))
        conn.commit()

    # Copy the message to the topic
    await context.bot.copy_message(
        chat_id=GROUP_ID,
        from_chat_id=update.message.chat_id,
        message_id=update.message.message_id,
        message_thread_id=topic_id
    )

async def handle_group_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward replies from group topics back to users, only if from admin."""
    if update.message.chat_id != GROUP_ID:
        return

    topic_id = update.message.message_thread_id
    if not topic_id:
        return  # Ignore general topic

    # Check if sender is admin
    sender_id = update.message.from_user.id
    if sender_id != ADMIN_ID:
        return  # Ignore non-admin replies

    # Find the user associated with this topic
    cur.execute('SELECT user_id FROM mappings WHERE topic_id = ?', (topic_id,))
    row = cur.fetchone()
    if not row:
        return

    user_id = row[0]

    # Copy the reply to the user
    await context.bot.copy_message(
        chat_id=user_id,
        from_chat_id=update.message.chat_id,
        message_id=update.message.message_id
    )

def main() -> None:
    """Run the bot."""
    application = Application.builder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_private_message))
    application.add_handler(MessageHandler(filters.ChatType.GROUPS, handle_group_reply))

    # Start polling
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
