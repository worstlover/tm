import os
import sqlite3
import logging
import traceback
import html
import threading
import time
import requests
import asyncio
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

# --- تنظیمات Flask برای Webhook ---
app = Flask(__name__)

# --- تنظیمات لاگینگ ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- متغیرهای محیطی ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "bot_database.db")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
DISPLAY_CHANNEL_USERNAME = os.getenv("DISPLAY_CHANNEL_USERNAME", "YourChannel")
MAIN_ADMIN_ID = int(os.getenv("MAIN_ADMIN_ID"))

# --- تنظیمات ربات ---
MESSAGE_INTERVAL = timedelta(minutes=2)
WORKING_HOURS_START = 8
WORKING_HOURS_END = 22

# --- وضعیت کاربران برای مکالمات ---
USER_STATE = {} # {user_id: "waiting_for_alias" | "waiting_for_channel_message" | "waiting_for_broadcast_message"}

# --- لیست کلمات ممنوعه (می‌توانید گسترش دهید) ---
FORBIDDEN_WORDS = [
    "فحش۱", "فحش۲", "کسخل", "کصکش", "کون", "کونی", "کیر", "کس", "جنده", "حرومزاده",
    "کونی", "بی‌ناموس", "بیناموس", "حرومزاده", "بیناموس", "کونی", "کونده", "کیری",
    "کسکش", "پفیوز", "لاشی", "دزد", "گوه", "گوهخور", "گوه خوری", "مادرجende",
]

# --- توابع پایگاه داده (SQLite) ---
def init_db():
    """پایگاه داده و جداول را مقداردهی اولیه می‌کند."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
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
                caption TEXT,
                message_time TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT
            )
        """)

        if MAIN_ADMIN_ID:
            cursor.execute("SELECT COUNT(*) FROM admins WHERE user_id = ?", (MAIN_ADMIN_ID,))
            if cursor.fetchone()[0] == 0:
                try:
                    cursor.execute("INSERT INTO admins (user_id, username) VALUES (?, ?)",
                                   (MAIN_ADMIN_ID, "main_admin"))
                    logger.info(f"Default main admin {MAIN_ADMIN_ID} added to database.")
                except Exception as e:
                    logger.error(f"Error adding default admin: {e}")

        conn.commit()
    logger.info("Database initialized.")

def is_admin(user_id: int) -> bool:
    """بررسی می‌کند آیا کاربر ادمین است."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
        return cursor.fetchone() is not None

# --- فیلترهای کاستوم برای دسترسی‌ها ---
class IsAdminFilter(filters.BaseFilter):
    def filter(self, message: Update):
        return is_admin(message.from_user.id)

class IsMainAdminFilter(filters.BaseFilter):
    def filter(self, message: Update):
        return message.from_user.id == MAIN_ADMIN_ID

IS_ADMIN_FILTER = IsAdminFilter()
IS_MAIN_ADMIN_FILTER = IsMainAdminFilter()


# --- توابع مدیریت کاربران و دیتابیس (ادامه) ---
def get_user_alias(user_id: int) -> str | None:
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT alias FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result[0] if result else None

def set_user_alias(user_id: int, username: str, alias: str) -> bool:
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO users (user_id, username, alias, is_banned, last_message_time)
                VALUES (?, ?, ?, COALESCE((SELECT is_banned FROM users WHERE user_id = ?), 0), COALESCE((SELECT last_message_time FROM users WHERE user_id = ?), NULL))
            """, (user_id, username, alias, user_id, user_id))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

def get_last_message_time(user_id: int) -> datetime | None:
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT last_message_time FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return datetime.fromisoformat(result[0]) if result and result[0] else None

def update_last_message_time(user_id: int) -> None:
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET last_message_time = ? WHERE user_id = ?",
                       (datetime.now().isoformat(), user_id))
        conn.commit()

def is_user_banned(user_id: int) -> bool:
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result[0] == 1 if result else False

def ban_user(user_id: int, username: str | None) -> None:
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO users (user_id, username, is_banned, alias, last_message_time)
            VALUES (?, ?, 1, COALESCE((SELECT alias FROM users WHERE user_id = ?), NULL), COALESCE((SELECT last_message_time FROM users WHERE user_id = ?), NULL))
        """, (user_id, username, user_id, user_id))
        conn.commit()
        logger.info(f"User {user_id} ({username}) banned.")

def unban_user(user_id: int) -> bool:
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        return cursor.rowcount > 0

def add_pending_media(user_id: int, file_id: str, file_type: str, caption: str) -> int:
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO pending_media (user_id, file_id, file_type, caption, message_time) VALUES (?, ?, ?, ?, ?)",
                       (user_id, file_id, file_type, caption, datetime.now().isoformat()))
        conn.commit()
        return cursor.lastrowid

def get_pending_media(media_id: int | None = None) -> tuple | list[tuple] | None:
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        if media_id:
            cursor.execute("SELECT * FROM pending_media WHERE id = ?", (media_id,))
            return cursor.fetchone()
        else:
            cursor.execute("SELECT * FROM pending_media ORDER BY message_time ASC")
            return cursor.fetchall()

def delete_pending_media(media_id: int) -> bool:
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM pending_media WHERE id = ?", (media_id,))
        conn.commit()
        return cursor.rowcount > 0

def get_total_users() -> int:
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        return cursor.fetchone()[0]

def get_all_user_ids() -> list[int]:
    """تمام شناسه‌های کاربری را برای ارسال همگانی برمی‌گرداند."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        return [row[0] for row in cursor.fetchall()]

def get_banned_users_count() -> int:
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        return cursor.fetchone()[0]

def add_admin(user_id: int, username: str) -> bool:
    """یک ادمین جدید اضافه می‌کند."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO admins (user_id, username) VALUES (?, ?)", (user_id, username))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False # Admin already exists

def remove_admin(user_id: int) -> bool:
    """یک ادمین را حذف می‌کند."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        conn.commit()
        return cursor.rowcount > 0

def list_all_admins() -> list[tuple]:
    """لیست تمام ادمین‌ها را برمی‌گرداند."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username FROM admins")
        return cursor.fetchall()


# --- توابع کمکی ---
def is_working_hours() -> bool:
    """بررسی می‌کند آیا در ساعات کاری هستیم."""
    now = datetime.now()
    return WORKING_HOURS_START <= now.hour < WORKING_HOURS_END

def contains_forbidden_words(text: str) -> bool:
    """بررسی می‌کند آیا متن حاوی کلمات ممنوعه است."""
    if not text:
        return False
    text_lower = text.lower()
    for word in FORBIDDEN_WORDS:
        if f" {word} " in f" {text_lower} " or text_lower.startswith(word + " ") or text_lower.endswith(" " + word) or text_lower == word:
            return True
    return False


# --- کیبوردهای ربات ---
async def get_main_reply_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    """کیبورد اصلی کاربر را بر اساس وضعیت او ایجاد می‌کند."""
    keyboard_buttons = [[KeyboardButton("📝 ارسال پیام")]]
    if get_user_alias(user_id):
        keyboard_buttons.append([KeyboardButton("📊 آمار من"), KeyboardButton("ℹ️ راهنما")])
    else:
        keyboard_buttons.append([KeyboardButton("👤 تنظیم نام مستعار"), KeyboardButton("ℹ️ راهنما")])
    if is_admin(user_id):
        keyboard_buttons.append([KeyboardButton("⚙️ پنل مدیریت")])
    return ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True, one_time_keyboard=False)

async def get_admin_reply_keyboard() -> ReplyKeyboardMarkup:
    """کیبورد پنل مدیریت را ایجاد می‌کند."""
    keyboard_buttons = [
        [KeyboardButton("📋 پیام‌های در انتظار"), KeyboardButton("👥 مدیریت کاربران")],
        [KeyboardButton("📢 ارسال همگانی"), KeyboardButton("📊 آمار کل")],
        [KeyboardButton("🔙 بازگشت به منوی اصلی")]
    ]
    return ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True, one_time_keyboard=False)


# --- هندلرهای اصلی ربات ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ارسال پیام خوشامدگویی و نمایش کیبورد اصلی."""
    user_id = update.effective_user.id
    alias = get_user_alias(user_id)
    message = "به ربات مدیریت کانال ناشناس خوش آمدید! 👋\n"
    if alias:
        message += f"نام مستعار فعلی شما: **{alias}**\n"
        message += "برای ارسال پیام، روی دکمه **📝 ارسال پیام** کلیک کنید."
    else:
        message += "برای شروع، ابتدا یک نام مستعار برای خود انتخاب کنید.\n"
        message += "لطفاً روی دکمه **👤 تنظیم نام مستعار** کلیک کنید."
    reply_markup = await get_main_reply_keyboard(user_id)
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ارسال پیام راهنما."""
    user_id = update.effective_user.id
    response_text = (
        "**راهنمای ربات:**\n\n"
        "📝 *ارسال پیام*: برای شروع فرآیند ارسال پیام یا رسانه.\n"
        "👤 *تنظیم نام مستعار*: برای انتخاب نام مستعار خود.\n"
        "📊 *آمار من*: مشاهده آمار شخصی شما.\n"
        "/cancel: برای لغو هر عملیات در حال انجام.\n\n"
    )
    if is_admin(user_id):
        response_text += (
            "**دستورات مدیران:**\n"
            "⚙️ *پنل مدیریت*: دسترسی به ابزارهای مدیریتی.\n"
            "/ban [ID/Alias] : مسدود کردن کاربر.\n"
            "/unban [ID/Alias] : رفع مسدودیت کاربر.\n"
            "/userinfo [ID/Alias] : نمایش اطلاعات کاربر.\n"
            "/pending : مشاهده رسانه‌های در انتظار تایید.\n"
        )
    if update.effective_user.id == MAIN_ADMIN_ID:
        response_text += (
            "\n**دستورات ادمین اصلی:**\n"
            "/addadmin [User_ID] : افزودن ادمین جدید.\n"
            "/removeadmin [User_ID] : حذف ادمین.\n"
            "/listadmins : نمایش لیست ادمین‌ها.\n"
        )
    reply_markup = await get_main_reply_keyboard(user_id)
    await update.message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def set_alias_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندل کردن دکمه 'تنظیم نام مستعار'."""
    user_id = update.effective_user.id
    current_alias = get_user_alias(user_id)
    if current_alias:
        await update.message.reply_text(f"شما قبلاً نام مستعار **{current_alias}** را انتخاب کرده‌اید.", parse_mode=ParseMode.MARKDOWN)
        if USER_STATE.get(user_id) == "waiting_for_alias":
            del USER_STATE[user_id]
    else:
        USER_STATE[user_id] = "waiting_for_alias"
        await update.message.reply_text("لطفاً **نام مستعار** مورد نظر خود را ارسال کنید:")

async def request_send_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندل کردن دکمه 'ارسال پیام'."""
    user_id = update.effective_user.id
    if is_user_banned(user_id):
        await update.message.reply_text("شما از ارسال پیام مسدود شده‌اید.")
        return
    if not get_user_alias(user_id):
        await update.message.reply_text("برای ارسال پیام، ابتدا باید نام مستعار خود را تنظیم کنید.")
        return
    USER_STATE[user_id] = "waiting_for_channel_message"
    await update.message.reply_text("اکنون پیام متنی یا رسانه (عکس/ویدیو) خود را برای ارسال به کانال بفرستید. برای لغو /cancel را ارسال کنید.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """پردازش پیام‌های ورودی بر اساس وضعیت کاربر."""
    user_id = update.effective_user.id
    user_username = update.effective_user.username or f"id_{user_id}"
    user_alias = get_user_alias(user_id)
    current_state = USER_STATE.get(user_id)

    # --- مدیریت تنظیم نام مستعار ---
    if current_state == "waiting_for_alias" and update.message.text:
        new_alias = update.message.text.strip()
        if not new_alias:
            await update.message.reply_text("نام مستعار نمی‌تواند خالی باشد.")
            return
        if contains_forbidden_words(new_alias):
            await update.message.reply_text("نام مستعار شما شامل کلمات ممنوعه است.")
            del USER_STATE[user_id]
            return
        if set_user_alias(user_id, user_username, new_alias):
            del USER_STATE[user_id]
            reply_markup = await get_main_reply_keyboard(user_id)
            await update.message.reply_text(f"نام مستعار شما با موفقیت به **{new_alias}** تنظیم شد.", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text("این نام مستعار قبلاً استفاده شده است. لطفاً نام دیگری انتخاب کنید.")
        return

    # --- مدیریت ارسال پیام همگانی توسط ادمین ---
    if current_state == "waiting_for_broadcast_message" and is_admin(user_id):
        all_user_ids = get_all_user_ids()
        successful_sends = 0
        failed_sends = 0

        await update.message.reply_text(f"در حال شروع ارسال پیام همگانی به {len(all_user_ids)} کاربر. این فرآیند ممکن است زمان‌بر باشد...")

        for uid in all_user_ids:
            try:
                await context.bot.copy_message(chat_id=uid, from_chat_id=user_id, message_id=update.message.message_id)
                successful_sends += 1
                await asyncio.sleep(0.1) # جلوگیری از اسپم و فشار به API تلگرام
            except TelegramError as e:
                logger.warning(f"Failed to send broadcast message to {uid}: {e}")
                failed_sends += 1

        del USER_STATE[user_id]
        reply_markup = await get_admin_reply_keyboard()
        await update.message.reply_text(
            f"✅ ارسال همگانی به پایان رسید.\n"
            f"ارسال موفق: {successful_sends}\n"
            f"ارسال ناموفق: {failed_sends}",
            reply_markup=reply_markup
        )
        return

    # --- مدیریت ارسال پیام به کانال ---
    if current_state != "waiting_for_channel_message":
        if update.message.text and not update.message.text.startswith('/'):
            known_buttons = ["📝 ارسال پیام", "👤 تنظیم نام مستعار", "📊 آمار من", "ℹ️ راهنما", "⚙️ پنل مدیریت", "📋 پیام‌های در انتظار", "👥 مدیریت کاربران", "📢 ارسال همگانی", "📊 آمار کل", "🔙 بازگشت به منوی اصلی"]
            if update.message.text not in known_buttons:
                reply_markup = await get_main_reply_keyboard(user_id)
                await update.message.reply_text("لطفاً از دکمه‌های منو استفاده کنید.", reply_markup=reply_markup)
        return

    if not is_working_hours() and not is_admin(user_id):
        await update.message.reply_text(f"ربات فقط در ساعات کاری ({WORKING_HOURS_START}:00 تا {WORKING_HOURS_END}:00) فعال است.")
        del USER_STATE[user_id]
        return

    last_time = get_last_message_time(user_id)
    if last_time and (datetime.now() - last_time) < MESSAGE_INTERVAL and not is_admin(user_id):
        remaining_time = MESSAGE_INTERVAL - (datetime.now() - last_time)
        await update.message.reply_text(f"لطفاً {int(remaining_time.total_seconds())} ثانیه دیگر صبر کنید.")
        return

    message_text = update.message.text
    caption_text = update.message.caption or ""

    if (message_text and contains_forbidden_words(message_text)) or (caption_text and contains_forbidden_words(caption_text)):
        await update.message.reply_text("پیام شما حاوی کلمات ممنوعه است و ارسال نشد.")
        del USER_STATE[user_id]
        return

    update_last_message_time(user_id)
    del USER_STATE[user_id] # وضعیت را در اینجا پاک می‌کنیم تا دوباره کاری نشود
    reply_markup = await get_main_reply_keyboard(user_id)

    # --- مدیریت رسانه (عکس و ویدیو) ---
    if update.message.photo or update.message.video:
        file_id = update.message.photo[-1].file_id if update.message.photo else update.message.video.file_id
        file_type = "photo" if update.message.photo else "video"
        media_id = add_pending_media(user_id, file_id, file_type, caption_text)

        all_admins = list_all_admins()
        for admin_id, _ in all_admins:
            try:
                admin_message = f"**رسانه جدید در انتظار تایید!** (ID: {media_id})\nاز: {user_alias} (`{user_id}`)"
                await context.bot.send_message(chat_id=admin_id, text=admin_message, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.warning(f"Could not send pending media notification to admin {admin_id}: {e}")
        await update.message.reply_text(f"رسانه شما دریافت شد و پس از تایید مدیر در کانال @{DISPLAY_CHANNEL_USERNAME} منتشر خواهد شد.", reply_markup=reply_markup)

    # --- مدیریت پیام متنی ---
    elif message_text:
        try:
            final_text = f"**{user_alias}:**\n{html.escape(message_text)}\n\n@{DISPLAY_CHANNEL_USERNAME}"
            await context.bot.send_message(chat_id=CHANNEL_ID, text=final_text, parse_mode=ParseMode.MARKDOWN)
            await update.message.reply_text(f"پیام شما با موفقیت در کانال @{DISPLAY_CHANNEL_USERNAME} منتشر شد.", reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error sending message to channel: {e}", exc_info=True)
            await update.message.reply_text("خطا در ارسال پیام به کانال. لطفاً با مدیر تماس بگیرید.", reply_markup=reply_markup)
    else:
        await update.message.reply_text("لطفاً یک پیام متنی یا رسانه ارسال کنید.", reply_markup=reply_markup)


async def cancel_operation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """لغو عملیات در حال انجام."""
    user_id = update.effective_user.id
    if user_id in USER_STATE:
        state = USER_STATE.pop(user_id)
        await update.message.reply_text(f"عملیات لغو شد.")
    else:
        await update.message.reply_text("هیچ عملیاتی برای لغو وجود ندارد.")

    reply_markup = await get_main_reply_keyboard(user_id)
    await update.message.reply_text("به منوی اصلی بازگشتید.", reply_markup=reply_markup)


# --- هندلرهای مدیریتی ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """نمایش پنل مدیریت."""
    if update.effective_user.id in USER_STATE:
        del USER_STATE[update.effective_user.id]
    reply_markup = await get_admin_reply_keyboard()
    await update.message.reply_text("**پنل مدیریت:**", parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """بازگشت به منوی اصلی از پنل ادمین."""
    user_id = update.effective_user.id
    if user_id in USER_STATE:
        del USER_STATE[user_id]
    reply_markup = await get_main_reply_keyboard(user_id)
    await update.message.reply_text("به منوی اصلی بازگشتید.", reply_markup=reply_markup)

async def manage_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """نمایش راهنمای مدیریت کاربران."""
    response_text = (
        "**مدیریت کاربران:**\n"
        "مسدود کردن: `/ban [ID_یا_Alias]`\n"
        "رفع مسدودیت: `/unban [ID_یا_Alias]`\n"
        "اطلاعات کاربر: `/userinfo [ID_یا_Alias]`\n"
    )
    await update.message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN)

async def _get_user_id_from_arg(arg: str) -> int | None:
    """دریافت شناسه کاربر از ID یا نام مستعار."""
    try:
        return int(arg)
    except ValueError:
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users WHERE alias = ?", (arg,))
            result = cursor.fetchone()
            return result[0] if result else None

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """مسدود کردن یک کاربر."""
    if not context.args:
        await update.message.reply_text("مثال: /ban 123456789")
        return
    target_arg = " ".join(context.args)
    target_user_id = await _get_user_id_from_arg(target_arg)
    if not target_user_id:
        await update.message.reply_text(f"کاربر '{target_arg}' یافت نشد.")
        return
    if is_admin(target_user_id):
        await update.message.reply_text("شما نمی‌توانید یک مدیر را مسدود کنید.")
        return
    ban_user(target_user_id, None)
    await update.message.reply_text(f"کاربر با ID: `{target_user_id}` با موفقیت مسدود شد.", parse_mode=ParseMode.MARKDOWN)

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """رفع مسدودیت یک کاربر."""
    if not context.args:
        await update.message.reply_text("مثال: /unban 123456789")
        return
    target_arg = " ".join(context.args)
    target_user_id = await _get_user_id_from_arg(target_arg)
    if not target_user_id:
        await update.message.reply_text(f"کاربر '{target_arg}' یافت نشد.")
        return
    if unban_user(target_user_id):
        await update.message.reply_text(f"کاربر با ID: `{target_user_id}` با موفقیت رفع مسدودیت شد.", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("این کاربر مسدود نیست یا یافت نشد.")

async def user_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """نمایش اطلاعات یک کاربر."""
    if not context.args:
        await update.message.reply_text("مثال: /userinfo 123456789")
        return
    target_arg = " ".join(context.args)
    user_id = await _get_user_id_from_arg(target_arg)
    if not user_id:
        await update.message.reply_text(f"کاربر '{target_arg}' یافت نشد.")
        return

    alias = get_user_alias(user_id) or "تنظیم نشده"
    is_banned_status = "بله" if is_user_banned(user_id) else "خیر"
    last_msg_time = get_last_message_time(user_id)
    last_msg_str = last_msg_time.strftime('%Y-%m-%d %H:%M:%S') if last_msg_time else "ندارد"

    response_text = (
        f"**اطلاعات کاربر:**\n"
        f"شناسه کاربری: `{user_id}`\n"
        f"نام مستعار: **{alias}**\n"
        f"وضعیت مسدودیت: {is_banned_status}\n"
        f"آخرین پیام: {last_msg_str}\n"
    )
    await update.message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN)


# --- دستورات ویژه ادمین اصلی ---
async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """افزودن ادمین جدید توسط ادمین اصلی."""
    if not context.args:
        await update.message.reply_text("لطفاً شناسه عددی کاربر را وارد کنید. مثال: /addadmin 123456")
        return
    try:
        user_id_to_add = int(context.args[0])
        # Fetch user to get username
        user = await context.bot.get_chat(user_id_to_add)
        username = user.username or f"id_{user_id_to_add}"
        if add_admin(user_id_to_add, username):
            await update.message.reply_text(f"کاربر {user_id_to_add} (@{username}) با موفقیت به لیست ادمین‌ها اضافه شد.")
        else:
            await update.message.reply_text("این کاربر در حال حاضر ادمین است.")
    except (ValueError, IndexError):
        await update.message.reply_text("شناسه کاربری نامعتبر است.")
    except TelegramError as e:
        await update.message.reply_text(f"خطا در یافتن کاربر: {e}")

async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """حذف ادمین توسط ادمین اصلی."""
    if not context.args:
        await update.message.reply_text("لطفاً شناسه عددی کاربر را وارد کنید. مثال: /removeadmin 123456")
        return
    try:
        user_id_to_remove = int(context.args[0])
        if user_id_to_remove == MAIN_ADMIN_ID:
            await update.message.reply_text("شما نمی‌توانید ادمین اصلی را حذف کنید.")
            return
        if remove_admin(user_id_to_remove):
            await update.message.reply_text(f"کاربر {user_id_to_remove} با موفقیت از لیست ادمین‌ها حذف شد.")
        else:
            await update.message.reply_text("کاربر مورد نظر ادمین نیست یا یافت نشد.")
    except (ValueError, IndexError):
        await update.message.reply_text("شناسه کاربری نامعتبر است.")

async def list_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """نمایش لیست تمام ادمین‌ها."""
    admins = list_all_admins()
    if not admins:
        await update.message.reply_text("هیچ ادمینی ثبت نشده است.")
        return

    response_text = "**لیست ادمین‌ها:**\n"
    for user_id, username in admins:
        is_main = " (اصلی)" if user_id == MAIN_ADMIN_ID else ""
        response_text += f"- `{user_id}` (@{username or 'N/A'}){is_main}\n"
    await update.message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN)


async def broadcast_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """درخواست ارسال پیام همگانی."""
    user_id = update.effective_user.id
    USER_STATE[user_id] = "waiting_for_broadcast_message"
    await update.message.reply_text(
        "لطفاً پیامی که می‌خواهید برای همه کاربران ارسال شود را بفرستید. "
        "این پیام می‌تواند شامل متن، عکس، ویدیو و... باشد.\n"
        "برای لغو /cancel را ارسال کنید."
    )

async def pending_media_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """نمایش رسانه‌های در انتظار تایید."""
    pending_items = get_pending_media()
    if not pending_items:
        await update.message.reply_text("هیچ رسانه‌ای در انتظار تایید وجود ندارد.")
        return
    await update.message.reply_text(f"{len(pending_items)} رسانه در انتظار تایید است:")
    for item in pending_items:
        media_id, user_id, file_id, file_type, caption, _ = item
        user_alias = get_user_alias(user_id) or f"ID: {user_id}"
        keyboard = [[InlineKeyboardButton("✅ تایید", callback_data=f"approve_{media_id}"),
                     InlineKeyboardButton("❌ رد", callback_data=f"reject_{media_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        message_caption = f"رسانه از: {user_alias}\nکپشن: {caption or 'ندارد'}"
        try:
            if file_type == "photo":
                await context.bot.send_photo(chat_id=update.effective_chat.id, photo=file_id, caption=message_caption, reply_markup=reply_markup)
            elif file_type == "video":
                await context.bot.send_video(chat_id=update.effective_chat.id, video=file_id, caption=message_caption, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Failed to send pending media {media_id} to admin: {e}")
            await update.message.reply_text(f"خطا در نمایش رسانه {media_id}: {e}")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """پردازش کلیک روی دکمه‌های اینلاین (تایید/رد)."""
    query = update.callback_query
    await query.answer()
    data = query.data
    action, media_id_str = data.split('_')
    media_id = int(media_id_str)
    media_item = get_pending_media(media_id)
    if not media_item:
        await query.edit_message_caption(caption="این رسانه قبلاً مدیریت شده است.")
        return
    _id, user_id, file_id, file_type, caption, _ = media_item
    user_alias = get_user_alias(user_id) or f"ID: {user_id}"
    if action == "approve":
        try:
            final_caption = f"**{user_alias}:**\n{html.escape(caption)}\n\n@{DISPLAY_CHANNEL_USERNAME}"
            if file_type == "photo":
                await context.bot.send_photo(chat_id=CHANNEL_ID, photo=file_id, caption=final_caption, parse_mode=ParseMode.MARKDOWN)
            elif file_type == "video":
                await context.bot.send_video(chat_id=CHANNEL_ID, video=file_id, caption=final_caption, parse_mode=ParseMode.MARKDOWN)
            await query.edit_message_caption(caption=f"✅ رسانه از {user_alias} تایید و منتشر شد.")
            delete_pending_media(media_id)
            await context.bot.send_message(chat_id=user_id, text=f"✅ رسانه شما در کانال @{DISPLAY_CHANNEL_USERNAME} منتشر شد.")
        except Exception as e:
            await query.edit_message_caption(caption=f"خطا در انتشار رسانه: {e}")
    elif action == "reject":
        delete_pending_media(media_id)
        await query.edit_message_caption(caption=f"❌ رسانه از {user_alias} رد شد.")
        await context.bot.send_message(chat_id=user_id, text=f"❌ رسانه شما رد شد و در کانال @{DISPLAY_CHANNEL_USERNAME} منتشر نشد.")


# --- آمار و خطا ---
async def my_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """نمایش آمار شخصی کاربر."""
    user_id = update.effective_user.id
    alias = get_user_alias(user_id) or "تنظیم نشده"
    is_banned_status = "بله" if is_user_banned(user_id) else "خیر"
    response_text = (
        f"**آمار شما:**\n"
        f"شناسه کاربری: `{user_id}`\n"
        f"نام مستعار: **{alias}**\n"
        f"وضعیت مسدودیت: {is_banned_status}\n"
    )
    await update.message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN)

async def total_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """نمایش آمار کلی ربات برای ادمین‌ها."""
    total_users = get_total_users()
    banned_users = get_banned_users_count()
    total_messages_pending = len(get_pending_media())
    response_text = (
        "**آمار کلی ربات:**\n"
        f"تعداد کل کاربران: {total_users}\n"
        f"کاربران مسدود شده: {banned_users}\n"
        f"رسانه‌های در انتظار تایید: {total_messages_pending}\n"
    )
    await update.message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """لاگ کردن خطاها و ارسال گزارش به ادمین اصلی."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    if MAIN_ADMIN_ID:
        tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
        tb_string = "".join(tb_list)
        message = f"An exception was raised:\n<pre>{html.escape(tb_string)}</pre>"
        try:
            await context.bot.send_message(chat_id=MAIN_ADMIN_ID, text=message, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Failed to send error message to main admin: {e}")


# --- تابع Keep-Alive برای Render ---
def keep_alive_ping():
    """سرویس را با پینگ کردن دوره‌ای زنده نگه می‌دارد."""
    if not RENDER_EXTERNAL_URL:
        logger.warning("RENDER_EXTERNAL_URL not set. Keep-alive ping will not run.")
        return
    while True:
        try:
            requests.get(RENDER_EXTERNAL_URL)
        except requests.exceptions.RequestException as e:
            logger.error(f"Keep-alive request failed: {e}")
        time.sleep(13 * 60) # هر 13 دقیقه


# --- راه‌اندازی ربات و وب‌سرور ---
application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

def setup_handlers(app_instance: Application):
    # دستورات عمومی
    app_instance.add_handler(CommandHandler("start", start_command))
    app_instance.add_handler(CommandHandler("help", help_command))
    app_instance.add_handler(CommandHandler("cancel", cancel_operation))
    app_instance.add_handler(CommandHandler("setalias", set_alias_button_handler))
    app_instance.add_handler(CommandHandler("mystats", my_stats_command))

    # دکمه‌های منوی اصلی
    app_instance.add_handler(MessageHandler(filters.Regex("^👤 تنظیم نام مستعار$") & ~filters.COMMAND, set_alias_button_handler))
    app_instance.add_handler(MessageHandler(filters.Regex("^📊 آمار من$") & ~filters.COMMAND, my_stats_command))
    app_instance.add_handler(MessageHandler(filters.Regex("^ℹ️ راهنما$") & ~filters.COMMAND, help_command))
    app_instance.add_handler(MessageHandler(filters.Regex("^📝 ارسال پیام$") & ~filters.COMMAND, request_send_message))

    # دکمه‌ها و دستورات پنل ادمین
    app_instance.add_handler(MessageHandler(filters.Regex("^⚙️ پنل مدیریت$") & ~filters.COMMAND & IS_ADMIN_FILTER, admin_panel))
    app_instance.add_handler(MessageHandler(filters.Regex("^📋 پیام‌های در انتظار$") & ~filters.COMMAND & IS_ADMIN_FILTER, pending_media_command))
    app_instance.add_handler(MessageHandler(filters.Regex("^👥 مدیریت کاربران$") & ~filters.COMMAND & IS_ADMIN_FILTER, manage_users))
    app_instance.add_handler(MessageHandler(filters.Regex("^📊 آمار کل$") & ~filters.COMMAND & IS_ADMIN_FILTER, total_stats_command))
    app_instance.add_handler(MessageHandler(filters.Regex("^📢 ارسال همگانی$") & ~filters.COMMAND & IS_ADMIN_FILTER, broadcast_prompt))
    app_instance.add_handler(MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$") & ~filters.COMMAND, back_to_main_menu))

    # دستورات ادمین
    app_instance.add_handler(CommandHandler("ban", ban_command, filters=IS_ADMIN_FILTER))
    app_instance.add_handler(CommandHandler("unban", unban_command, filters=IS_ADMIN_FILTER))
    app_instance.add_handler(CommandHandler("pending", pending_media_command, filters=IS_ADMIN_FILTER))
    app_instance.add_handler(CommandHandler("userinfo", user_info_command, filters=IS_ADMIN_FILTER))

    # دستورات ادمین اصلی
    app_instance.add_handler(CommandHandler("addadmin", add_admin_command, filters=IS_MAIN_ADMIN_FILTER))
    app_instance.add_handler(CommandHandler("removeadmin", remove_admin_command, filters=IS_MAIN_ADMIN_FILTER))
    app_instance.add_handler(CommandHandler("listadmins", list_admins_command, filters=IS_MAIN_ADMIN_FILTER))

    # هندلرهای پایانی
    app_instance.add_handler(CallbackQueryHandler(button_callback))
    app_instance.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.VIDEO & ~filters.COMMAND, handle_message))
    app_instance.add_error_handler(error_handler)

setup_handlers(application)

@app.route(f"/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
async def telegram_webhook():
    """هندل کردن آپدیت‌های تلگرام از طریق وب‌هوک."""
    if request.method == "POST":
        try:
            update = Update.de_json(request.get_json(force=True), application.bot)
            application.update_queue.put_nowait(update)
        except Exception as e:
            logger.error(f"Error processing webhook update: {e}")
        return "ok", 200
    return "Method Not Allowed", 405

@app.route('/')
def home():
    """مسیر Health Check برای زنده نگه داشتن سرویس."""
    return "Bot is alive and kicking!", 200


# --- کدهای اصلاح شده برای اجرای صحیح ربات در ترد جانبی ---

async def run_application():
    """
    برنامه را برای پردازش آپدیت‌ها از صف مقداردهی اولیه کرده و اجرا می‌کند.
    این تابع signal handler نصب نمی‌کند و برای اجرا در ترد جانبی مناسب است.
    """
    logger.info("Starting application processor...")
    await application.initialize()
    await application.start()

    # این خط ترد را زنده و منتظر نگه می‌دارد تا آپدیت‌ها را پردازش کند
    await asyncio.Future()

def run_bot_in_thread():
    """پردازشگر ربات را در یک ترد جداگانه با event loop مخصوص به خود اجرا می‌کند."""
    logger.info("Dispatching bot processing thread.")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        # تابع run_application را در event loop جدید اجرا می‌کنیم
        loop.run_until_complete(run_application())
    except Exception as e:
        # لاگ کردن خطا در صورت بروز مشکل جدی در ترد
        logger.critical(f"Unhandled exception in bot processing thread: {e}", exc_info=True)
    finally:
        logger.info("Bot processing thread is shutting down.")
        # اطمینان از توقف صحیح برنامه قبل از بستن لوپ
        if application.running:
            loop.run_until_complete(application.stop())
        loop.close()

def main() -> None:
    """راه‌اندازی ربات و وب‌سرور Flask."""
    init_db()

    required_vars = ["TELEGRAM_BOT_TOKEN", "CHANNEL_ID", "MAIN_ADMIN_ID", "RENDER_EXTERNAL_URL"]
    for var in required_vars:
        if not os.getenv(var):
            raise ValueError(f"{var} environment variable is not set. Bot cannot start.")

    webhook_url = f"{RENDER_EXTERNAL_URL}/{TELEGRAM_BOT_TOKEN}"
    try:
        asyncio.run(application.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES))
        logger.info(f"Webhook set to: {webhook_url}")
    except Exception as e:
        logger.error(f"Failed to set webhook: {e}", exc_info=True)
        raise

    threading.Thread(target=run_bot_in_thread, name="TelegramBotProcessingThread", daemon=True).start()
    threading.Thread(target=keep_alive_ping, name="KeepAliveThread", daemon=True).start()

    port = int(os.getenv("PORT", 10000))
    logger.info(f"Starting Flask web server on host 0.0.0.0 and port {port}...")
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()