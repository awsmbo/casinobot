import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
# Админ казино для каждого чата — тот, кто добавил бота (см. my_chat_member в bot.py).
# ADMIN_ID в .env больше не используется.
