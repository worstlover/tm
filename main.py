# main.py
import os
import sqlite3
import logging
import html
import threading
from datetime import datetime, timedelta

# کتابخانه‌های مورد نیاز برای ربات و وب‌سرور
from flask import Flask
from waitress import serve
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode

# --- تنظیمات لاگینگ ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- خواندن متغیرهای محیطی ---
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
DB_PATH = "bot_data.db"
MESSAGE_COOLDOWN = timedelta(minutes=2) # فاصله زمانی بین هر پیام

# --- وضعیت کاربران ---
USER_STATE = {}  # {user_id: "waiting_for_alias" | "waiting_for_message"}

# --- پایگاه داده ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        # جدول کاربران
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                alias TEXT UNIQUE,
                last_message_time TEXT,
                is_banned INTEGER DEFAULT 0
            )
        """)
        # جدول رسانه‌های در انتظار تایید
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
    logger.info("Database initialized successfully.")

# توابع کمکی دیتابیس
def get_user(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        if not row:
            # اگر کاربر وجود نداشت، او را اضافه می‌کنیم
            cursor.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
            conn.commit()
            return {"user_id": user_id, "alias": None, "last_message_time": None, "is_banned": 0}
        return {"user_id": row[0], "alias": row[1], "last_message_time": row[2], "is_banned": row[3]}

def set_alias(user_id, alias):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE users SET alias = ? WHERE user_id = ?", (alias, user_id))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False # این نام مستعار قبلا گرفته شده

def update_last_message_time(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE users SET last_message_time = ? WHERE user_id = ?",
                     (datetime.now().isoformat(), user_id))

def ban_toggle_user(user_id, ban_status: bool):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (1 if ban_status else 0, user_id))

def add_pending_media(user_id, file_id, file_type, caption):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO pending_media (user_id, file_id, file_type, caption) VALUES (?, ?, ?, ?)",
                       (user_id, file_id, file_type, caption))
        conn.commit()
        return cursor.lastrowid

def get_pending_media(media_id):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM pending_media WHERE id = ?", (media_id,))
        return cursor.fetchone()

def delete_pending_media(media_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM pending_media WHERE id = ?", (media_id,))

# --- دستورات اصلی ربات ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    alias = user.get('alias')

    text = "سلام! به ربات پیام‌رسان ناشناس خوش آمدید. 💌\n\n"
    if alias:
        text += f"نام مستعار شما: **{alias}**\n\n"
        text += "برای ارسال پیام، از دستور /send استفاده کنید یا مستقیماً پیامتان را بفرستید."
    else:
        text += "برای شروع، لطفاً با دستور /setalias یک نام مستعار برای خود انتخاب کنید."
    
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def set_alias_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    USER_STATE[user_id] = "waiting_for_alias"
    await update.message.reply_text("لطفا نام مستعار مورد نظر خود را وارد کنید:")

async def send_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    if user['is_banned']:
        await update.message.reply_text("⛔️ شما از ارسال پیام منع شده‌اید.")
        return
    if not user['alias']:
        await update.message.reply_text("لطفا ابتدا با /setalias یک نام مستعار برای خود انتخاب کنید.")
        return
    
    # بررسی فاصله زمانی مجاز بین دو پیام
    last_time_str = user.get('last_message_time')
    if last_time_str:
        last_time = datetime.fromisoformat(last_time_str)
        if (datetime.now() - last_time) < MESSAGE_COOLDOWN:
            await update.message.reply_text(f"⏳ لطفا کمی صبر کنید. شما هر {MESSAGE_COOLDOWN.seconds // 60} دقیقه یک‌بار می‌توانید پیام ارسال کنید.")
            return
    
    USER_STATE[user_id] = "waiting_for_message"
    await update.message.reply_text("پیام متنی، عکس یا ویدیوی خود را برای ارسال به کانال بفرستید:")

# --- پردازش پیام‌ها ---
async def handle_text_or_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = USER_STATE.get(user_id)

    # 1. کاربر در حال تنظیم نام مستعار است
    if state == "waiting_for_alias":
        alias = update.message.text.strip()
        if not alias or len(alias) > 30:
            await update.message.reply_text("نام مستعار باید بین ۱ تا ۳۰ کاراکتر باشد. لطفا دوباره تلاش کنید.")
            return
        
        if set_alias(user_id, alias):
            await update.message.reply_text(f"✅ نام مستعار شما با موفقیت به **{alias}** تغییر کرد.", parse_mode=ParseMode.MARKDOWN)
            del USER_STATE[user_id]
        else:
            await update.message.reply_text("❌ این نام مستعار قبلاً توسط شخص دیگری انتخاب شده. لطفا نام دیگری را امتحان کنید.")
        return

    # 2. کاربر در حال ارسال پیام است (یا از دستور /send استفاده کرده)
    # اگر کاربر مستقیما پیام فرستاده، ابتدا شرایط را چک می‌کنیم
    if state != "waiting_for_message":
        await send_command(update, context) # فراخوانی تابع send برای بررسی شرایط
        # اگر شرایط اوکی بود، وضعیت به waiting_for_message تغییر می‌کند
        if USER_STATE.get(user_id) != "waiting_for_message":
             return # اگر شرایط ارسال پیام را نداشت، خارج شو

    user = get_user(user_id)
    alias = user['alias']
    
    # ارسال پیام متنی
    if update.message.text:
        message_text = html.escape(update.message.text)
        final_text = f"**{alias}**: \n\n{message_text}"
        await context.bot.send_message(chat_id=CHANNEL_ID, text=final_text, parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text("✅ پیام شما با موفقیت در کانال منتشر شد.")
    
    # ارسال عکس یا ویدیو (نیاز به تایید ادمین)
    elif update.message.photo or update.message.video:
        file_id = update.message.photo[-1].file_id if update.message.photo else update.message.video.file_id
        file_type = "photo" if update.message.photo else "video"
        caption = update.message.caption or ""
        
        media_id = add_pending_media(user_id, file_id, file_type, caption)
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ تایید", callback_data=f"approve_{media_id}"),
             InlineKeyboardButton("❌ رد کردن", callback_data=f"reject_{media_id}")]
        ])
        
        preview_caption = f"رسانه جدید از طرف **{alias}** برای تایید:\n\n`{caption}`"
        
        # ارسال پیش‌نمایش برای ادمین
        if file_type == "photo":
            await context.bot.send_photo(chat_id=ADMIN_ID, photo=file_id, caption=preview_caption, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
        else:
            await context.bot.send_video(chat_id=ADMIN_ID, video=file_id, caption=preview_caption, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

        await update.message.reply_text("🖼 رسانه شما برای تایید به مدیر ارسال شد و در صورت تایید در کانال قرار می‌گیرد.")

    # بعد از ارسال موفق، زمان را آپدیت و وضعیت را پاک کن
    update_last_message_time(user_id)
    if user_id in USER_STATE:
        del USER_STATE[user_id]


# --- دستورات ادمین ---
async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        target_id = int(context.args[0])
        ban_toggle_user(target_id, True)
        await update.message.reply_text(f"کاربر {target_id} با موفقیت مسدود شد.")
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده صحیح: /ban USER_ID")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        target_id = int(context.args[0])
        ban_toggle_user(target_id, False)
        await update.message.reply_text(f"کاربر {target_id} با موفقیت از مسدودیت خارج شد.")
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده صحیح: /unban USER_ID")

async def userinfo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        target_id = int(context.args[0])
        user = get_user(target_id)
        status = "مسدود ⛔️" if user['is_banned'] else "فعال ✅"
        info = (
            f"اطلاعات کاربر: `{target_id}`\n"
            f"نام مستعار: **{user['alias']}**\n"
            f"وضعیت: {status}"
        )
        await update.message.reply_text(info, parse_mode=ParseMode.MARKDOWN)
    except (IndexError, ValueError):
        await update.message.reply_text("استفاده صحیح: /userinfo USER_ID")


# --- مدیریت دکمه‌های اینلاین (تایید/رد) ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    action, media_id_str = query.data.split("_")
    media_id = int(media_id_str)
    
    media_data = get_pending_media(media_id)
    if not media_data:
        await query.edit_message_caption(caption="این مورد قبلا مدیریت شده است.", reply_markup=None)
        return
        
    _, user_id, file_id, file_type, caption = media_data
    user_alias = get_user(user_id)['alias'] or "ناشناس"

    if action == "approve":
        final_caption = f"**{user_alias}**: \n\n{html.escape(caption)}"
        try:
            if file_type == "photo":
                await context.bot.send_photo(chat_id=CHANNEL_ID, photo=file_id, caption=final_caption, parse_mode=ParseMode.MARKDOWN)
            else:
                await context.bot.send_video(chat_id=CHANNEL_ID, video=file_id, caption=final_caption, parse_mode=ParseMode.MARKDOWN)
            
            await query.edit_message_caption(caption=f"✅ تایید شد و در کانال ارسال شد.\n(ارسال شده توسط {user_alias})", reply_markup=None)
            await context.bot.send_message(chat_id=user_id, text="✅ رسانه شما توسط مدیر تایید و در کانال منتشر شد.")
        except Exception as e:
            await query.edit_message_caption(caption=f"خطا در ارسال به کانال: {e}", reply_markup=None)

    elif action == "reject":
        await query.edit_message_caption(caption=f"❌ رد شد.\n(ارسال شده توسط {user_alias})", reply_markup=None)
        await context.bot.send_message(chat_id=user_id, text="❌ متاسفانه رسانه شما توسط مدیر رد شد.")

    delete_pending_media(media_id) # حذف از دیتابیس در هر صورت

# --- بخش وب‌سرور برای بیدار نگه داشتن ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "Bot is running with long polling...", 200

def run_web_server():
    port = int(os.getenv("PORT", 10000))
    serve(app, host="0.0.0.0", port=port)

# --- تابع اصلی ---
def main():
    # 1. بررسی متغیرهای ضروری
    if not all([TOKEN, ADMIN_ID, CHANNEL_ID]):
        logger.critical("FATAL: One or more environment variables (TOKEN, ADMIN_ID, CHANNEL_ID) are missing.")
        return

    # 2. مقداردهی اولیه دیتابیس
    init_db()

    # 3. راه‌اندازی وب‌سرور در یک ترد جداگانه (برای بیدار نگه داشتن)
    web_thread = threading.Thread(target=run_web_server)
    web_thread.daemon = True
    web_thread.start()
    logger.info("Web server thread started to keep the service alive.")

    # 4. راه‌اندازی ربات
    application = Application.builder().token(TOKEN).build()

    # ثبت دستورات
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("setalias", set_alias_command))
    application.add_handler(CommandHandler("send", send_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("unban", unban_command))
    application.add_handler(CommandHandler("userinfo", userinfo_command))
    
    # ثبت پردازشگر پیام‌ها
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_or_media))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, handle_text_or_media))
    
    # ثبت پردازشگر دکمه‌ها
    application.add_handler(CallbackQueryHandler(button_handler))

    # 5. اجرای ربات با روش لانگ پولینگ
    logger.info("Bot started with long polling...")
    application.run_polling()

if __name__ == "__main__":
    main()