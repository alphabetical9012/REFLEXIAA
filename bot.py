import os
import asyncio
import threading
import logging
import psycopg2
from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
import google.generativeai as genai

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", "8080"))
RENDER_URL = os.getenv("RENDER_URL", "")

# --- Gemini ---
genai.configure(api_key=GEMINI_API_KEY)

with open("system_prompt.txt", "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

# --- Flask ---
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bot is running!", 200

# --- Автопинг ---
def keep_alive():
    import time
    import requests
    while True:
        try:
            if RENDER_URL:
                requests.get(RENDER_URL, timeout=10)
        except Exception as e:
            logger.warning(f"Пинг не удался: {e}")
        time.sleep(300)

# --- База данных ---
def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
        conn.commit()
    logger.info("База данных инициализирована")

def get_history(user_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT role, content FROM messages
                WHERE user_id = %s
                ORDER BY created_at ASC
            """, (user_id,))
            rows = cur.fetchall()
    return [{"role": r[0], "parts": [r[1]]} for r in rows]

def save_message(user_id, role, content):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO messages (user_id, role, content)
                VALUES (%s, %s, %s)
            """, (user_id, role, content))
        conn.commit()

def clear_history(user_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM messages WHERE user_id = %s", (user_id,))
        conn.commit()

# --- Gemini запрос ---
def ask_gemini(user_id, user_message):
    history = get_history(user_id)
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=SYSTEM_PROMPT
    )
    chat = model.start_chat(history=history)
    response = chat.send_message(user_message)
    return response.text

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я помогу отрефлексировать ситуацию из жизни и собрать честный черновик поста для соцсетей.\n\n"
        "Расскажи что происходит — и мы начнём.\n\n"
        "Примеры:\n"
        "• Целый день лежу на диване и не могу заставить себя работать\n"
        "• Поругался с другом и теперь злюсь на себя\n"
        "• Каждый раз переделываю работу по сто раз\n"
        "• Не могу отказать людям даже когда плохо"
    )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_history(update.effective_user.id)
    await update.message.reply_text("Контекст очищен. Расскажи с чего начнём?")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text.strip()

    await update.message.chat.send_action("typing")

    try:
        save_message(user_id, "user", user_text)
        reply = ask_gemini(user_id, user_text)
        save_message(user_id, "model", reply)
        await update.message.reply_text(reply)
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text("Что-то пошло не так. Попробуй ещё раз или напиши /reset")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Голосовые сообщения пока не поддерживаются. Напиши текстом — я отвечу.")

# --- Запуск ---
async def run_bot():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    logger.info("Бот запущен")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()

def main():
    if not TOKEN or not GEMINI_API_KEY:
        logger.error("Не заданы BOT_TOKEN или GEMINI_API_KEY")
        return
    init_db()
    threading.Thread(
        target=lambda: flask_app.run(host="0.0.0.0", port=PORT, use_reloader=False),
        daemon=True
    ).start()
    threading.Thread(target=keep_alive, daemon=True).start()
    asyncio.run(run_bot())

if __name__ == "__main__":
    main()
