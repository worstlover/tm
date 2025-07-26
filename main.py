#!/usr/bin/env python3
"""
ربات تلگرام مدیریت کانال ناشناس - ساده و کامل
"""

import os
import sys
import logging
import sqlite3
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ParseMode

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# تنظیمات ربات - از متغیرهای محیطی
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
CHANNEL_ID = os.getenv('CHANNEL_ID', '')
DATABASE_PATH = os.getenv('DATABASE_PATH', 'bot_database.db')

# لیست کلمات ممنوعه فارسی و انگلیسی
PROFANITY_WORDS = [
    # فارسی
    'احمق', 'خر', 'گاو', 'کره', 'لعنتی', 'مزخرف',
    # انگلیسی  
    'stupid', 'damn', 'shit', 'fuck', 'idiot', 'moron'
]

# پیام‌های رابط کاربری
MESSAGES = {
    'welcome': """🌟 به ربات کانال ناشناس خوش آمدید!

شما می‌توانید:
📝 پیام‌های متنی ارسال کنید
📷 عکس، ویدیو و فایل بفرستید
🎭 با نام مستعار منحصر به فرد ظاهر شوید

برای شروع روی /start کلیک کنید!""",
    
    'main_menu': """🏠 منوی اصلی

لطفاً یکی از گزینه‌های زیر را انتخاب کنید:""",
    
    'message_sent': '✅ پیام شما با موفقیت ارسال شد!',
    'message_filtered': '❌ پیام شما شامل کلمات نامناسب است.',
    'media_queued': '📤 فایل شما در صف بررسی قرار گرفت.',
    'rate_limited': '⏰ شما خیلی سریع پیام می‌فرستید. لطفاً کمی صبر کنید.',
    'outside_hours': '🕐 ارسال پیام فقط در ساعات کاری مجاز است.',
    'banned': '🚫 شما از ارسال پیام محروم شده‌اید.',
}

class SimpleBot:
    def __init__(self):
        self.db_path = DATABASE_PATH
        self.admins = set()
        self.init_database()
        self.load_admins()
    
    def init_database(self):
        """ایجاد پایگاه داده"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # جدول کاربران
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                display_name TEXT UNIQUE,
                join_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_activity DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_banned BOOLEAN DEFAULT FALSE,
                message_count INTEGER DEFAULT 0,
                last_message_time DATETIME
            )
        """)
        
        # جدول مدیران
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                added_date DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # جدول پیام‌ها
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                message_text TEXT,
                message_type TEXT DEFAULT 'text',
                sent_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        """)
        
        # جدول رسانه در انتظار تایید
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                file_id TEXT,
                file_type TEXT,
                caption TEXT,
                submit_date DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        """)
        
        # جدول تنظیمات
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        # تنظیمات پیش‌فرض
        default_settings = {
            'profanity_filter': 'true',
            'media_approval': 'true',
            'rate_limit_minutes': '2',
            'work_start_hour': '6',
            'work_end_hour': '23:59'
        }
        
        for key, value in default_settings.items():
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
        
        conn.commit()
        conn.close()
        logger.info("پایگاه داده مقداردهی شد")
    
    def load_admins(self):
        """بارگذاری لیست مدیران"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM admins")
        self.admins = {row[0] for row in cursor.fetchall()}
        conn.close()
        logger.info(f"تعداد {len(self.admins)} مدیر بارگذاری شد")
    
    def is_admin(self, user_id: int) -> bool:
        """بررسی مدیر بودن کاربر"""
        return user_id in self.admins
    
    def add_admin(self, user_id: int, username: str = None):
        """اضافه کردن مدیر جدید"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO admins (user_id, username) VALUES (?, ?)", 
                      (user_id, username))
        conn.commit()
        conn.close()
        self.admins.add(user_id)
        logger.info(f"مدیر جدید اضافه شد: {user_id}")
    
    def remove_admin(self, user_id: int):
        """حذف مدیر"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        self.admins.discard(user_id)
        logger.info(f"مدیر حذف شد: {user_id}")
    
    def get_or_create_user(self, user_id: int, username: str = None, 
                          first_name: str = None, last_name: str = None):
        """دریافت یا ایجاد کاربر"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        
        if not user:
            cursor.execute("""
                INSERT INTO users (user_id, username, first_name, last_name)
                VALUES (?, ?, ?, ?)
            """, (user_id, username, first_name, last_name))
            conn.commit()
        else:
            # به‌روزرسانی آخرین فعالیت
            cursor.execute("""
                UPDATE users SET last_activity = CURRENT_TIMESTAMP,
                username = COALESCE(?, username),
                first_name = COALESCE(?, first_name),
                last_name = COALESCE(?, last_name)
                WHERE user_id = ?
            """, (username, first_name, last_name, user_id))
            conn.commit()
        
        conn.close()
    
    def check_profanity(self, text: str) -> bool:
        """بررسی کلمات ممنوعه"""
        if not text:
            return False
        
        text_lower = text.lower()
        return any(word in text_lower for word in PROFANITY_WORDS)
    
    def check_rate_limit(self, user_id: int) -> bool:
        """بررسی محدودیت زمانی"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT value FROM settings WHERE key = 'rate_limit_minutes'")
        rate_limit = int(cursor.fetchone()[0])
        
        cursor.execute("SELECT last_message_time FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        
        if not result or not result[0]:
            return True
        
        last_time = datetime.fromisoformat(result[0])
        return datetime.now() - last_time > timedelta(minutes=rate_limit)
    
    def check_work_hours(self) -> bool:
        """بررسی ساعات کاری"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT value FROM settings WHERE key = 'work_start_hour'")
        start_hour = int(cursor.fetchone()[0])
        
        cursor.execute("SELECT value FROM settings WHERE key = 'work_end_hour'")
        end_hour = int(cursor.fetchone()[0])
        
        conn.close()
        
        current_hour = datetime.now().hour
        return start_hour <= current_hour <= end_hour
    
    def is_banned(self, user_id: int) -> bool:
        """بررسی مسدود بودن کاربر"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result and result[0]
    
    async def send_to_channel(self, context: ContextTypes.DEFAULT_TYPE, 
                            text: str, display_name: str):
        """ارسال پیام به کانال"""
        message = f"💬 {display_name}:\n\n{text}"
        await context.bot.send_message(chat_id=CHANNEL_ID, text=message)
    
    def log_message(self, user_id: int, text: str, msg_type: str = 'text'):
        """ثبت پیام در پایگاه داده"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO messages (user_id, message_text, message_type)
            VALUES (?, ?, ?)
        """, (user_id, text, msg_type))
        
        cursor.execute("""
            UPDATE users SET 
            message_count = message_count + 1,
            last_message_time = CURRENT_TIMESTAMP
            WHERE user_id = ?
        """, (user_id,))
        
        conn.commit()
        conn.close()

# ایجاد نمونه ربات
bot_instance = SimpleBot()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور شروع"""
    if not update.effective_user:
        return
    
    user = update.effective_user
    bot_instance.get_or_create_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    
    # کیبورد اصلی
    keyboard = [
        [KeyboardButton("📝 ارسال پیام"), KeyboardButton("👤 تنظیم نام مستعار")],
        [KeyboardButton("📊 آمار من"), KeyboardButton("ℹ️ راهنما")]
    ]
    
    # افزودن گزینه‌های مدیریت برای ادمین‌ها
    if bot_instance.is_admin(user.id):
        keyboard.append([KeyboardButton("⚙️ پنل مدیریت")])
    
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        MESSAGES['welcome'],
        reply_markup=reply_markup
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پردازش پیام‌های کاربران"""
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    message_text = update.message.text
    
    # بررسی مسدود بودن
    if bot_instance.is_banned(user_id):
        await update.message.reply_text(MESSAGES['banned'])
        return
    
    # بررسی ساعات کاری
    if not bot_instance.check_work_hours():
        await update.message.reply_text(MESSAGES['outside_hours'])
        return
    
    # بررسی محدودیت زمانی
    if not bot_instance.check_rate_limit(user_id):
        await update.message.reply_text(MESSAGES['rate_limited'])
        return
    
    # پردازش دستورات کیبورد
    if message_text == "📝 ارسال پیام":
        await update.message.reply_text("لطفاً پیام خود را تایپ کنید:")
        return
    
    elif message_text == "👤 تنظیم نام مستعار":
        await set_display_name_start(update, context)
        return
    
    elif message_text == "📊 آمار من":
        await show_user_stats(update, context)
        return
    
    elif message_text == "ℹ️ راهنما":
        await show_help(update, context)
        return
    
    elif message_text == "⚙️ پنل مدیریت" and bot_instance.is_admin(user_id):
        await admin_panel(update, context)
        return
    
    # پردازش پیام معمولی
    # بررسی فیلتر کلمات ممنوعه
    if bot_instance.check_profanity(message_text):
        await update.message.reply_text(MESSAGES['message_filtered'])
        return
    
    # دریافت نام مستعار کاربر
    conn = sqlite3.connect(bot_instance.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT display_name FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    display_name = result[0] if result and result[0] else f"کاربر_{user_id}"
    
    # ارسال به کانال
    try:
        await bot_instance.send_to_channel(context, message_text, display_name)
        bot_instance.log_message(user_id, message_text)
        await update.message.reply_text(MESSAGES['message_sent'])
    except Exception as e:
        logger.error(f"خطا در ارسال پیام: {e}")
        await update.message.reply_text("❌ خطا در ارسال پیام. لطفاً دوباره تلاش کنید.")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پردازش فایل‌های رسانه‌ای"""
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    # بررسی مسدود بودن
    if bot_instance.is_banned(user_id):
        await update.message.reply_text(MESSAGES['banned'])
        return
    
    # دریافت اطلاعات فایل
    file_id = None
    file_type = None
    caption = update.message.caption or ""
    
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_type = 'photo'
    elif update.message.video:
        file_id = update.message.video.file_id
        file_type = 'video'
    elif update.message.document:
        file_id = update.message.document.file_id
        file_type = 'document'
    elif update.message.audio:
        file_id = update.message.audio.file_id
        file_type = 'audio'
    elif update.message.voice:
        file_id = update.message.voice.file_id
        file_type = 'voice'
    
    # ذخیره در صف تایید
    conn = sqlite3.connect(bot_instance.db_path)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO pending_media (user_id, file_id, file_type, caption)
        VALUES (?, ?, ?, ?)
    """, (user_id, file_id, file_type, caption))
    conn.commit()
    conn.close()
    
    await update.message.reply_text(MESSAGES['media_queued'])
    
    # اطلاع به مدیران
    for admin_id in bot_instance.admins:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"📤 فایل جدید از کاربر {user_id}\nنوع: {file_type}\n\n/approve_{cursor.lastrowid} برای تایید"
            )
        except:
            pass

async def set_display_name_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع تنظیم نام مستعار"""
    await update.message.reply_text(
        "👤 نام مستعار جدید خود را وارد کنید:\n\n"
        "⚠️ توجه: نام مستعار فقط یک بار قابل تغییر است!"
    )
    context.user_data['setting_display_name'] = True

async def show_user_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش آمار کاربر"""
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    conn = sqlite3.connect(bot_instance.db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT display_name, message_count, join_date, last_activity
        FROM users WHERE user_id = ?
    """, (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        display_name, msg_count, join_date, last_activity = result
        stats_text = f"""📊 آمار شما:

👤 نام مستعار: {display_name or 'تنظیم نشده'}
📝 تعداد پیام‌ها: {msg_count}
📅 تاریخ عضویت: {join_date[:10]}
⏰ آخرین فعالیت: {last_activity[:16]}"""
    else:
        stats_text = "❌ اطلاعات کاربری یافت نشد."
    
    await update.message.reply_text(stats_text)

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نمایش راهنما"""
    help_text = """📋 راهنمای استفاده:

🔸 برای ارسال پیام، روی "📝 ارسال پیام" کلیک کنید
🔸 برای تنظیم نام مستعار، روی "👤 تنظیم نام مستعار" کلیک کنید
🔸 می‌توانید عکس، ویدیو و فایل ارسال کنید
🔸 فایل‌های رسانه‌ای نیاز به تایید مدیر دارند
🔸 پیام‌های متنی بلافاصله منتشر می‌شوند

⚠️ قوانین:
• از کلمات نامناسب استفاده نکنید
• بین پیام‌ها حداقل 2 دقیقه فاصله بگذارید
• فقط در ساعات 8 تا 22 پیام ارسال کنید"""
    
    await update.message.reply_text(help_text)

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پنل مدیریت"""
    if not update.effective_user or not bot_instance.is_admin(update.effective_user.id):
        return
    
    keyboard = [
        [InlineKeyboardButton("📋 پیام‌های در انتظار", callback_data="pending_media")],
        [InlineKeyboardButton("👥 مدیریت کاربران", callback_data="manage_users")],
        [InlineKeyboardButton("⚙️ تنظیمات", callback_data="bot_settings")],
        [InlineKeyboardButton("📊 آمار کل", callback_data="total_stats")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("⚙️ پنل مدیریت:", reply_markup=reply_markup)

def main():
    """تابع اصلی"""
    if not BOT_TOKEN:
        logger.error("توکن ربات تنظیم نشده است!")
        sys.exit(1)
    
    if not CHANNEL_ID:
        logger.error("شناسه کانال تنظیم نشده است!")
        sys.exit(1)
    
    # ایجاد اپلیکیشن
    application = Application.builder().token(BOT_TOKEN).build()
    
    # ثبت هندلرها
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.DOCUMENT | 
                                         filters.AUDIO | filters.VOICE, handle_media))
    
    logger.info("ربات شروع به کار کرد...")
    
    # شروع ربات
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()