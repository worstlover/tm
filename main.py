# main.py
import os
import sqlite3
import logging
import html
import threading
from datetime import datetime, timedelta, time

# --- کتابخانه‌های مورد نیاز ---
from flask import Flask
from waitress import serve
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode

# --- تنظیمات اصلی و خواندن متغیرهای محیطی ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME") # نام کاربری کانال با @ (مثال: @MyChannel)

# --- ثابت‌ها و تنظیمات ربات ---
DB_PATH = "bot_database.db"
USER_STATE = {}  # {user_id: "waiting_for_alias" | "waiting_for_message"}
MESSAGE_COOLDOWN = timedelta(minutes=3)
WORKING_HOURS_START = time(7, 0)  # 7 AM
WORKING_HOURS_END = time(1, 0)    # 1 AM (روز بعد)

FORBIDDEN_WORDS = [
    "فحش", "کلمه_بد", "کصخل", "کصکش", "کون", "کونی", "کیر", "کس", "جنده", "حرومزاده",
    "کونی", "بی‌ناموس", "بیناموس", "حرومزاده", "بیناموس", "کونی", "کونده", "کیری",
    "کسکش", "پفیوز", "لاشی", "دزد", "گوه", "گوهخور", "گوه خوری", "مادرجende",
]

# --- مدیریت پایگاه داده (SQLite) ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                alias TEXT UNIQUE,
                last_message_time TEXT,
                is_banned INTEGER DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                file_id TEXT,
                file_type TEXT,
                caption TEXT
            )
        """)
        conn.commit()
    logger.info("Database initialized.")

def get_user(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT alias, last_message_time, is_banned FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
            conn.commit()
            return {"alias": None, "last_message_time": None, "is_banned": 0}
        return {"alias": row[0], "last_message_time": row[1], "is_banned": row[2]}

def set_user_alias(user_id, alias):
    with sqlite3.connect(DB_PATH) as conn:
        try:
            conn.execute("UPDATE users SET alias = ? WHERE user_id = ?", (alias, user_id))
            return True
        except sqlite3.IntegrityError:
            return False

def update_user_message_time(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE users SET last_message_time = ? WHERE user_id = ?", (datetime.now().isoformat(), user_id))

def toggle_ban_user(user_id, should_ban):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (1 if should_ban else 0, user_id))

def add_media_for_approval(user_id, file_id, file_type, caption):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO pending_media (user_id, file_id, file_type, caption) VALUES (?, ?, ?, ?)",
                       (user_id, file_id, file_type, caption))
        return cursor.lastrowid

def get_pending_media_by_id(media_id):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM pending_media WHERE id = ?", (media_id,))
        return cursor.fetchone()

def remove_pending_media(media_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM pending_media WHERE id = ?", (media_id,))

# --- توابع کمکی ربات ---
def is_working_hours():
    """چک می‌کند آیا در ساعات کاری هستیم (7 صبح تا 1 بامداد)"""
    now = datetime.now().time()
    if WORKING_HOURS_START <= WORKING_HOURS_END: # 07:00 -> 23:00
        return WORKING_HOURS_START <= now <= WORKING_HOURS_END
    else: # 07:00 -> 01:00 (روز بعد)
        return now >= WORKING_HOURS_START or now <= WORKING_HOURS_END

def contains_profanity(text: str):
    """چک می‌کند آیا متن حاوی کلمات ممنوعه است"""
    return any(word in text for word in FORBIDDEN_WORDS)

def get_main_keyboard():
    """کیبورد اصلی را ایجاد می‌کند"""
    keyboard = [
        [KeyboardButton("📝 ارسال پیام")],
        [KeyboardButton("👤 تغییر نام مستعار"), KeyboardButton("ℹ️ راهنما")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# --- وب‌سرور برای بیدار نگه داشتن ---
flask_app = Flask(__name__)
@flask_app.route('/')
def health_check():
    return "Bot is alive!", 200

def run_web_server():
    port = int(os.getenv("PORT", 10000))
    serve(flask_app, host="0.0.0.0", port=port)

# --- هندلرهای ربات ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    alias = user.get('alias')
    welcome_text = "سلام! به ربات پیام‌رسان ناشناس خوش آمدید. 💌\n\n"
    if alias:
        welcome_text += f"نام مستعار فعلی شما: **{alias}**\n\n"
    else:
        welcome_text += "برای شروع، لطفاً یک نام مستعار برای خود انتخاب کنید.\n"
    welcome_text += "از دکمه‌های زیر برای کار با ربات استفاده کنید."
    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.MARKDOWN)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "**راهنمای ربات:**\n\n"
        "📝 **ارسال پیام:** برای فرستادن پیام متنی، عکس یا ویدیو به کانال.\n"
        "👤 **تغییر نام مستعار:** برای عوض کردن نامی که با آن پیام می‌دهید.\n\n"
        f"تمامی پیام‌ها به صورت ناشناس در کانال {CHANNEL_USERNAME} منتشر می‌شوند."
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def handle_button_press(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پردازش دکمه‌های اصلی کیبورد"""
    user_id = update.effective_user.id
    text = update.message.text

    if text == "📝 ارسال پیام":
        user = get_user(user_id)
        # 1. چک کردن بن بودن
        if user['is_banned']:
            await update.message.reply_text("⛔️ شما توسط ادمین مسدود شده‌اید و نمی‌توانید پیام ارسال کنید.")
            return
        # 2. چک کردن نام مستعار
        if not user['alias']:
            await update.message.reply_text("لطفاً ابتدا با دکمه «👤 تغییر نام مستعار» یک نام برای خود انتخاب کنید.")
            return
        # 3. چک کردن ساعات کاری
        if not is_working_hours():
            await update.message.reply_text("⏳ ربات در حال حاضر غیرفعال است. ساعات فعالیت از ۷ صبح تا ۱ بامداد می‌باشد.")
            return
        # 4. چک کردن فاصله زمانی مجاز
        last_time_str = user.get('last_message_time')
        if last_time_str:
            time_since_last_message = datetime.now() - datetime.fromisoformat(last_time_str)
            if time_since_last_message < MESSAGE_COOLDOWN:
                remaining_seconds = int((MESSAGE_COOLDOWN - time_since_last_message).total_seconds())
                await update.message.reply_text(f"لطفاً {remaining_seconds} ثانیه دیگر صبر کنید.")
                return
        
        USER_STATE[user_id] = "waiting_for_message"
        await update.message.reply_text("✅ بسیار خب! اکنون پیام متنی، عکس یا ویدیوی خود را بفرستید:")

    elif text == "👤 تغییر نام مستعار":
        USER_STATE[user_id] = "waiting_for_alias"
        await update.message.reply_text("لطفاً نام مستعار جدید خود را وارد کنید:")

    elif text == "ℹ️ راهنما":
        await help_command(update, context)

async def handle_user_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پردازش ورودی کاربر بر اساس وضعیت او"""
    user_id = update.effective_user.id
    state = USER_STATE.get(user_id)

    if not state:
        await update.message.reply_text("لطفاً از دکمه‌های منو استفاده کنید.", reply_markup=get_main_keyboard())
        return

    # --- کاربر در حال تنظیم نام مستعار است ---
    if state == "waiting_for_alias":
        alias = update.message.text.strip()
        if not (1 < len(alias) < 30) or contains_profanity(alias):
            await update.message.reply_text("نام مستعار باید بین ۲ تا ۳۰ کاراکتر و بدون کلمات نامناسب باشد. دوباره تلاش کنید.")
            return
        
        if set_user_alias(user_id, alias):
            await update.message.reply_text(f"✅ نام مستعار شما با موفقیت به **{alias}** تغییر کرد.", parse_mode=ParseMode.MARKDOWN)
            del USER_STATE[user_id]
        else:
            await update.message.reply_text("❌ این نام قبلاً انتخاب شده است. لطفاً نام دیگری را وارد کنید.")
        return

    # --- کاربر در حال ارسال پیام است ---
    if state == "waiting_for_message":
        user_alias = get_user(user_id)['alias']
        message_text = update.message.text or update.message.caption or ""
        
        # چک کردن کلمات رکیک
        if contains_profanity(message_text):
            await update.message.reply_text("پیام شما حاوی کلمات نامناسب است و ارسال نشد. لطفاً پیام خود را ویرایش کنید.")
            return # وضعیت کاربر پاک نمی‌شود تا بتواند پیام جدید بفرستد

        final_message_body = f"**{user_alias}**: \n\n{html.escape(message_text)}\n\n{CHANNEL_USERNAME}"

        # اگر پیام متنی است
        if update.message.text:
            await context.bot.send_message(chat_id=CHANNEL_ID, text=final_message_body, parse_mode=ParseMode.MARKDOWN)
            await update.message.reply_text("✅ پیام شما با موفقیت در کانال منتشر شد.")
            update_user_message_time(user_id)
            del USER_STATE[user_id]
        
        # اگر پیام رسانه است
        elif update.message.photo or update.message.video:
            file_id = update.message.photo[-1].file_id if update.message.photo else update.message.video.file_id
            file_type = "photo" if update.message.photo else "video"
            
            media_id = add_media_for_approval(user_id, file_id, file_type, message_text)
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تایید", callback_data=f"approve_{media_id}"),
                 InlineKeyboardButton("❌ رد", callback_data=f"reject_{media_id}")]])
            
            admin_caption = f"رسانه جدید از طرف **{user_alias}** برای تایید:\n\n`{message_text}`"
            
            if file_type == "photo":
                await context.bot.send_photo(chat_id=ADMIN_ID, photo=file_id, caption=admin_caption, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
            else:
                await context.bot.send_video(chat_id=ADMIN_ID, video=file_id, caption=admin_caption, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

            await update.message.reply_text("🖼 رسانه شما برای تایید به مدیر ارسال شد.")
            update_user_message_time(user_id)
            del USER_STATE[user_id]

async def handle_admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت دکمه‌های تایید/رد توسط ادمین"""
    query = update.callback_query
    await query.answer()
    
    action, media_id_str = query.data.split("_")
    media_id = int(media_id_str)
    
    media_data = get_pending_media_by_id(media_id)
    if not media_data:
        await query.edit_message_caption(caption="این مورد قبلاً مدیریت شده است.")
        return
        
    _, user_id, file_id, file_type, caption_text = media_data
    user_alias = get_user(user_id)['alias'] or "ناشناس"

    final_caption = f"**{user_alias}**: \n\n{html.escape(caption_text)}\n\n{CHANNEL_USERNAME}"
    
    if action == "approve":
        try:
            if file_type == "photo":
                await context.bot.send_photo(chat_id=CHANNEL_ID, photo=file_id, caption=final_caption, parse_mode=ParseMode.MARKDOWN)
            else:
                await context.bot.send_video(chat_id=CHANNEL_ID, video=file_id, caption=final_caption, parse_mode=ParseMode.MARKDOWN)
            
            await query.edit_message_caption(caption=f"✅ تایید شد و به کانال ارسال گردید.")
            await context.bot.send_message(user_id, f"✅ رسانه شما توسط مدیر تایید و در کانال {CHANNEL_USERNAME} منتشر شد.")
        except Exception as e:
            await query.edit_message_caption(caption=f"خطا در ارسال: {e}")
            logger.error(f"Failed to send approved media to channel: {e}")

    elif action == "reject":
        await query.edit_message_caption(caption=f"❌ رسانه رد شد.")
        await context.bot.send_message(user_id, f"❌ متاسفانه رسانه شما توسط مدیر رد شد و در کانال {CHANNEL_USERNAME} منتشر نشد.")

    remove_pending_media(media_id)

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        target_id = int(context.args[0])
        toggle_ban_user(target_id, True)
        await update.message.reply_text(f"کاربر {target_id} مسدود شد.")
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده: /ban USER_ID")

# --- تابع اصلی ---
def main():
    if not all([TOKEN, ADMIN_ID, CHANNEL_ID, CHANNEL_USERNAME]):
        logger.critical("FATAL: یک یا چند متغیر محیطی (TOKEN, ADMIN_ID, CHANNEL_ID, CHANNEL_USERNAME) تنظیم نشده‌اند.")
        return

    init_db()

    # راه‌اندازی وب‌سرور در ترد جداگانه برای جلوگیری از خواب رفتن
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    logger.info("Web server started in a separate thread.")

    # راه‌اندازی ربات
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ban", ban_command))
    # می‌توانید دستور unban و userinfo را مشابه ban اضافه کنید

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex("^(📝 ارسال پیام|👤 تغییر نام مستعار|ℹ️ راهنما)$"), handle_button_press))
    application.add_handler(MessageHandler((filters.TEXT | filters.PHOTO | filters.VIDEO) & ~filters.COMMAND, handle_user_input))
    application.add_handler(CallbackQueryHandler(handle_admin_buttons))

    logger.info("Bot is starting with long polling...")
    application.run_polling()

if __name__ == "__main__":
    main()