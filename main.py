import os
import sqlite3
import logging
import traceback
import html
import threading
import time
import requests 
from datetime import datetime, timedelta
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

# --- تنظیمات لاگینگ ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- متغیرهای محیطی ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID")) # CHANNEL_ID باید عدد صحیح باشد
DATABASE_PATH = os.getenv("DATABASE_PATH", "bot_database.db")
# آدرس URL سرویس Render شما برای Keep-Alive. حتماً اینو تنظیم کنید!
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")

# آیدی کانال (به فرمت YourChannelUsername بدون @) برای نمایش در پیام‌ها
# اگر این متغیر محیطی تنظیم نشده باشد، از یک مقدار پیش‌فرض استفاده می‌شود.
# دقت کنید که اینجا @ را اضافه نمی‌کنیم، چون در زمان نمایش در تلگرام اضافه خواهد شد.
DISPLAY_CHANNEL_USERNAME = os.getenv("DISPLAY_CHANNEL_USERNAME", "YourChannel")

# آیدی کاربر ادمین اصلی (جهت دریافت اعلان‌های سیستمی و غیره)
# این باید یک User ID عددی باشد.
MAIN_ADMIN_ID = int(os.getenv("MAIN_ADMIN_ID"))


# --- تنظیمات ربات ---
MESSAGE_INTERVAL = timedelta(minutes=2)  # محدودیت 2 دقیقه بین پیام‌ها
WORKING_HOURS_START = 8  # 8 صبح (ساعت 8:00)
WORKING_HOURS_END = 22  # 10 شب (ساعت 22:00)

# --- وضعیت کاربران برای مکالمات ---
USER_STATE = {} # {user_id: "waiting_for_alias" | "waiting_for_channel_message"}

# --- لیست کلمات ممنوعه فارسی (شما می‌توانید این لیست را گسترش دهید) ---
FORBIDDEN_WORDS = [
    "فحش۱", "فحش۲", "کسخل", "کصکش", "کون", "کونی", "کیر", "کس", "جنده", "حرومزاده",
    "کونی", "بی‌ناموس", "بیناموس", "حرومزاده", "بیناموس", "کونی", "کونده", "کیری",
    "کسکش", "پفیوز", "لاشی", "دزد", "گوه", "گوهخور", "گوه خوری", "مادرجنده",
    "کوس", "کیرم", "کسخول", "ننت", "بیناموس", "کسده", "چاقال", "اوبی", "کونی", "کیری",
    "کسخل", "کصکش", "کون", "کونی", "کیر", "کس", "جنده", "حرومزاده", "لاشی", "کثافت", "احمق",
    "بی‌شعور", "نفهم", "نادان", "بیشرف", "هرزه", "فاحشه", "پست", "مایه_ننگ", "مزخرف",
    "گمشو", "خفه_شو", "حرامزاده", "عوضی", "پلید", "رذل", "کثیف", "هیز", "قرمساق", "بی‌وطن",
    "متجاوز", "قاتل", "دیوث", "دشمن", "خائن", "بی‌ریشه", "کودن", "ابله", "چلمن", "شلخته",
    "قراضه", "بی‌وجود", "مزخرفات", "خزعبلات", "چرندیات", "واژگون", "نابود", "ویران",
    "منفور", "مغرض", "فاسد", "ریاکار", "دروغگو", "کلاهبردار", "جعلکار", "گول‌زن",
    "توطئه‌گر", "فریبکار", "تبهکار", "متخلف", "قانون‌شکن", "مجرم", "جانی", "بزهکار",
    "ارازل", "اوباش", "زورگیر", "باجگیر", "تروریست", "انتحاری", "آشغال", "زباله",
    "چرت", "پرت", "مزخرف", "هتاک", "توهین‌آمیز", "زننده", "شرم‌آور", "رسوا", "افتضاح",
    "فلاکتبار", "نفرت‌انگیز", "ناخوشایند", "مشمئزکننده", "کثیف", "زشت", "کریه",
    "شیطان", "ابلیس", "جن", "دیو", "اهریمن", "شیاطین", "جنایتکار", "جنایتکاران",
    "قاتلین", "نابودگران", "مفسدین", "ستمکاران", "ظالمین", "جهنمی", "عذاب‌آور",
    "نفرین", "لعنت", "مرگ", "تباهی", "نابودی", "هلاکت", "زوال", "فنا", "جهنم", "دوزخ",
    "شکنجه", "آزار", "اذیت", "خشونت", "تجاوز", "نفرت", "کینه", "خشم", "کینه_توز",
    "حسادت", "بخل", "طمع", "حرص", "دروغ", "فریب", "خیانت", "نامردی", "پستی", "رذالت",
    "بی‌غیرت", "بی‌شرف", "بی‌وجدان", "بی‌رحم", "سنگدل", "ظالم", "ستمگر", "متعصب",
    "جاهل", "نادان", "عقب‌مانده", "بدوی", "همجی", "وحشی", "افراطی", "تندرو", "خشونت‌طلب",
    "وحشتناک", "ترسناک", "مهیب", "کابوس", "فاجعه", "غم‌انگیز", "تلخ", "دردناک",
    "شوم", "نحس", "بدشگون", "تاریک", "سیاه", "تیره", "عبوس", "غمبار", "اندوهگین",
    "مغموم", "افسرده", "افسرده‌کننده", "نومید", "مایوس", "مأیوس‌کننده", "دلگیر",
    "دلتنگ", "بی‌قرار", "بی‌تاب", "غمزده", "مصیبت_بار", "بحرانی", "خطرناک", "مهلک",
    "مرگبار", "کثیف", "زشت", "نامطبوع", "منزجرکننده", "حال_به_هم_زن", "غیر_قابل_تحمل",
    "فاسد", "خراب", "ناپاک", "نجس", "پلید", "کثیف", "چسبناک", "بودار", "گندیده",
    "پوسیده", "خراب_شده", "از_بین_رفته", "نابود_شده", "ویران_شده", "سوخته", "مخروبه",
    "داغون", "شلخته", "نامرتب", "کثیف", "بی‌نظم", "پریشان", "آشفته", "سردرگم",
    "بی‌هدف", "بی‌جهت", "بی‌فایده", "بیهوده", "پوچ", "خالی", "تهی", "بی‌ارزش",
    "بی‌اهمیت", "بی‌معنی", "مزخرف", "چرند", "پرت_و_پلا", "خزعبل", "بی‌خود",
    "مزخرف‌گو", "چرند_گو", "بیهوده_گو", "پر_حرف", "زیاده_گو", "ناشی", "غیر_حرفه‌ای",
    "آماتور", "بی‌تجربه", "کند", "تنبل", "بی‌حال", "بی‌تفاوت", "سرد", "بی‌احساس",
    "بی‌روح", "خالی_ذهن", "احمق", "کندذهن", "کم‌هوش", "ابله", "نفهم", "نادان",
    "بی‌سواد", "جاهل", "غیر_منطقی", "بی‌منطق", "غیرهوشمند", "نابخرد", "نادان_بزرگ"
]

# --- توابع پایگاه داده (SQLite) ---
def init_db():
    """Initializes the SQLite database tables if they don't exist."""
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
                file_type TEXT, -- 'photo', 'video'
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
        conn.commit()
    logger.info("Database initialized.")

def is_admin(user_id: int) -> bool:
    """Checks if a given user_id is an admin."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
        return cursor.fetchone() is not None

# تعریف فیلتر کاستوم برای چک کردن ادمین بودن
class IsAdminFilter(filters.BaseFilter):
    def filter(self, message):
        return is_admin(message.from_user.id)

IS_ADMIN_FILTER = IsAdminFilter()

def get_user_alias(user_id: int) -> str | None:
    """Retrieves the alias for a given user_id."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT alias FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result[0] if result else None

def set_user_alias(user_id: int, username: str, alias: str) -> bool:
    """Sets or updates a user's alias. Returns True on success, False if alias already exists."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        try:
            # Preserve existing is_banned status if user already exists
            cursor.execute("""
                INSERT OR REPLACE INTO users (user_id, username, alias, is_banned, last_message_time)
                VALUES (?, ?, ?, COALESCE((SELECT is_banned FROM users WHERE user_id = ?), 0), COALESCE((SELECT last_message_time FROM users WHERE user_id = ?), NULL))
            """, (user_id, username, alias, user_id, user_id))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # This means the alias already exists for another user
            return False

def get_last_message_time(user_id: int) -> datetime | None:
    """Retrieves the last message time for a user."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT last_message_time FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        if result and result[0]:
            return datetime.fromisoformat(result[0])
        return None

def update_last_message_time(user_id: int) -> None:
    """Updates the last message time for a user to now."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET last_message_time = ? WHERE user_id = ?",
                       (datetime.now().isoformat(), user_id))
        conn.commit()

def is_user_banned(user_id: int) -> bool:
    """Checks if a user is banned."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result[0] == 1 if result else False # Default to not banned if user not found

def ban_user(user_id: int, username: str | None) -> None:
    """Bans a user, adding them if they don't exist."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        # Insert or replace, preserving alias and last_message_time if user exists
        cursor.execute("""
            INSERT OR REPLACE INTO users (user_id, username, is_banned, alias, last_message_time)
            VALUES (?, ?, 1, COALESCE((SELECT alias FROM users WHERE user_id = ?), NULL), COALESCE((SELECT last_message_time FROM users WHERE user_id = ?), NULL))
        """, (user_id, username, user_id, user_id))
        conn.commit()
        logger.info(f"User {user_id} ({username}) banned.")

def unban_user(user_id: int) -> bool:
    """Unbans a user. Returns True if user was found and unbanned, False otherwise."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        return cursor.rowcount > 0 # Returns true if a row was updated

def add_pending_media(user_id: int, file_id: str, file_type: str, caption: str) -> int:
    """Adds a media item to the pending queue."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO pending_media (user_id, file_id, file_type, caption, message_time) VALUES (?, ?, ?, ?, ?)",
                       (user_id, file_id, file_type, caption, datetime.now().isoformat()))
        conn.commit()
        logger.info(f"Pending media added: {file_type} from {user_id}")
        return cursor.lastrowid

def get_pending_media(media_id: int | None = None) -> tuple | list[tuple] | None:
    """Retrieves pending media items. If media_id is provided, returns single item."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        if media_id:
            cursor.execute("SELECT * FROM pending_media WHERE id = ?", (media_id,))
            return cursor.fetchone()
        else:
            cursor.execute("SELECT * FROM pending_media ORDER BY message_time ASC")
            return cursor.fetchall()

def delete_pending_media(media_id: int) -> bool:
    """Deletes a pending media item."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM pending_media WHERE id = ?", (media_id,))
        conn.commit()
        return cursor.rowcount > 0

def get_total_users() -> int:
    """Gets the total count of registered users."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        return cursor.fetchone()[0]

def get_banned_users_count() -> int:
    """Gets the count of banned users."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        return cursor.fetchone()[0]

def get_total_messages_published() -> int:
    """Gets the total count of messages (from pending_media table, assuming once approved, they are counted).
    NOTE: This is a placeholder. For actual count of *published* messages,
    you would need a separate table or a 'status' column in pending_media
    to differentiate between pending, approved, and rejected.
    Here, it simply counts all entries in pending_media.
    """
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM pending_media") # Adjust if you have a "published" status
        return cursor.fetchone()[0]


# --- توابع کمکی ---
def is_working_hours() -> bool:
    """Checks if current time is within working hours."""
    now = datetime.now()
    return WORKING_HOURS_START <= now.hour < WORKING_HOURS_END

def contains_forbidden_words(text: str) -> bool:
    """Checks if the given text contains any forbidden words (case-insensitive)."""
    if not text:
        return False
    text_lower = text.lower()
    for word in FORBIDDEN_WORDS:
        # Check for whole words to reduce false positives
        # Using regex for more robust word boundary matching could be better for production
        if f" {word} " in f" {text_lower} " or text_lower.startswith(word + " ") or text_lower.endswith(" " + word) or text_lower == word:
            return True
    return False

# --- هندلرهای ربات (Async Functions - برای سازگاری با Python-Telegram-Bot v20+) ---

async def get_main_reply_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    """Generates the main reply keyboard based on user's status."""
    keyboard_buttons = []
    
    # دکمه "ارسال پیام" همیشه باید باشه تا کاربر بتونه شروع به ارسال کنه
    keyboard_buttons.append([KeyboardButton("📝 ارسال پیام")])

    if get_user_alias(user_id):
        # User has an alias
        keyboard_buttons.append([KeyboardButton("📊 آمار من"), KeyboardButton("ℹ️ راهنما")])
    else:
        # User needs to set an alias - should ideally be handled at start/first message
        keyboard_buttons.append([KeyboardButton("👤 تنظیم نام مستعار"), KeyboardButton("ℹ️ راهنما")])

    if is_admin(user_id):
        keyboard_buttons.append([KeyboardButton("⚙️ پنل مدیریت")])

    return ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True, one_time_keyboard=False)

async def get_admin_reply_keyboard() -> ReplyKeyboardMarkup:
    """Generates the admin reply keyboard."""
    keyboard_buttons = [
        [KeyboardButton("📋 پیام‌های در انتظار"), KeyboardButton("👥 مدیریت کاربران")],
        [KeyboardButton("📊 آمار کل"), KeyboardButton("🔙 بازگشت به منوی اصلی")]
    ]
    return ReplyKeyboardMarkup(keyboard_buttons, resize_keyboard=True, one_time_keyboard=False)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message and prompts user to set alias with reply keyboard."""
    user_id = update.effective_user.id
    alias = get_user_alias(user_id)
    message = (
        "به ربات مدیریت کانال ناشناس خوش آمدید! 👋\n"
        "این ربات به شما امکان می‌دهد پیام‌ها و رسانه‌ها را به صورت ناشناس در کانال ارسال کنید.\n\n"
    )
    
    if alias:
        message += f"نام مستعار فعلی شما: **{alias}**\n"
        message += "برای ارسال پیام متنی یا رسانه، لطفاً روی دکمه **📝 ارسال پیام** کلیک کنید."
    else:
        message += "برای شروع، ابتدا باید یک نام مستعار برای خود انتخاب کنید.\n"
        message += "لطفاً روی دکمه **👤 تنظیم نام مستعار** کلیک کنید."

    reply_markup = await get_main_reply_keyboard(user_id)
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a help message with available commands using reply keyboard."""
    user_id = update.effective_user.id
    response_text = (
        "راهنمای استفاده از ربات مدیریت کانال ناشناس:\n\n"
        "**دستورات کاربری:**\n"
        "📝 *ارسال پیام*: برای شروع ارسال پیام یا رسانه به کانال.\n" 
        "👤 *تنظیم نام مستعار*: برای تنظیم یا مشاهده نام مستعار (فقط برای بار اول).\n" 
        "📊 *آمار من*: مشاهده آمار شخصی.\n"
        "ℹ️ *راهنما*: نمایش این راهنما.\n\n"
    )
    
    if is_admin(user_id):
        response_text += (
            "**دستورات مدیر:**\n"
            "⚙️ *پنل مدیریت*: دسترسی به ابزارهای مدیریتی.\n" 
            "👥 *مدیریت کاربران*: مسدود/رفع مسدودیت کاربران.\n"
            "📋 *پیام‌های در انتظار*: تایید/رد رسانه‌ها.\n"
            "📊 *آمار کل*: مشاهده آمار کلی ربات.\n"
        )
    
    reply_markup = await get_main_reply_keyboard(user_id)
    await update.message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def set_alias_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the 'تنظیم نام مستعار' button click or /setalias command."""
    user_id = update.effective_user.id
    current_alias = get_user_alias(user_id)
    if current_alias:
        await update.message.reply_text(f"شما قبلاً نام مستعار **{current_alias}** را انتخاب کرده‌اید. در صورت نیاز به تغییر، با مدیران تماس بگیرید.", parse_mode=ParseMode.MARKDOWN)
        # پاک کردن حالت اگر کاربر دکمه رو الکی زده
        if USER_STATE.get(user_id) == "waiting_for_alias":
            del USER_STATE[user_id]
            reply_markup = await get_main_reply_keyboard(user_id)
            await update.message.reply_text("منوی اصلی:", reply_markup=reply_markup) # بازگشت به منوی اصلی
    else:
        USER_STATE[user_id] = "waiting_for_alias"
        await update.message.reply_text("لطفاً **نام مستعار** مورد نظر خود را در پیام بعدی *ارسال* کنید:")

async def request_send_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the 'ارسال پیام' button click."""
    user_id = update.effective_user.id
    user_alias = get_user_alias(user_id)

    if is_user_banned(user_id):
        await update.message.reply_text("شما از ارسال پیام مسدود شده‌اید.")
        return

    if not user_alias:
        await update.message.reply_text("برای ارسال پیام، ابتدا باید با دستور /setalias یا دکمه **تنظیم نام مستعار** نام مستعار خود را تنظیم کنید.")
        return

    USER_STATE[user_id] = "waiting_for_channel_message"
    await update.message.reply_text("حالا پیام متنی یا رسانه (عکس/ویدیو) خود را برای ارسال به کانال بفرستید. برای لغو /cancel را ارسال کنید.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming text messages and media based on user's state."""
    user_id = update.effective_user.id
    user_username = update.effective_user.username or f"id_{user_id}"
    user_alias = get_user_alias(user_id)
    current_state = USER_STATE.get(user_id)

    # --- بررسی وضعیت کاربر برای setalias ---
    if current_state == "waiting_for_alias" and update.message.text:
        new_alias = update.message.text.strip()
        if not new_alias:
            await update.message.reply_text("نام مستعار نمی‌تواند خالی باشد. لطفاً یک نام معتبر وارد کنید.")
            return

        if contains_forbidden_words(new_alias):
            await update.message.reply_text("نام مستعار شما شامل کلمات ممنوعه است. لطفاً نام دیگری انتخاب کنید.")
            del USER_STATE[user_id] # وضعیت را ریست می‌کنیم
            reply_markup = await get_main_reply_keyboard(user_id)
            await update.message.reply_text("عملیات تنظیم نام مستعار لغو شد. می‌توانید دوباره تلاش کنید.", reply_markup=reply_markup)
            return
        
        if set_user_alias(user_id, username=user_username, alias=new_alias):
            await update.message.reply_text(f"نام مستعار شما با موفقیت به **{new_alias}** تنظیم شد.")
            del USER_STATE[user_id] # وضعیت را ریست می‌کنیم
            reply_markup = await get_main_reply_keyboard(user_id)
            await update.message.reply_text("به منوی اصلی بازگشتید:", reply_markup=reply_markup) # نمایش کیبورد اصلی و پیام نهایی
        else:
            await update.message.reply_text("این نام مستعار قبلاً استفاده شده است. لطفاً نام دیگری انتخاب کنید.")
            # وضعیت را همچنان در waiting_for_alias نگه می‌داریم تا کاربر دوباره نام مستعار بدهد
        return # مهم: از تابع خارج می‌شویم تا به بقیه handle_message نرویم

    # --- فقط در صورتی که کاربر در حالت 'waiting_for_channel_message' باشد پیام را پردازش کن ---
    if current_state != "waiting_for_channel_message":
        # اگر پیام یک دکمه یا دستور شناخته شده نیست، به کاربر اطلاع بده
        if update.message.text and not update.message.text.startswith('/') and \
           not (update.message.text in ["📝 ارسال پیام", "👤 تنظیم نام مستعار", "📊 آمار من", "ℹ️ راهنما", "⚙️ پنل مدیریت", "📋 پیام‌های در انتظار", "👥 مدیریت کاربران", "📊 آمار کل", "🔙 بازگشت به منوی اصلی"]):
            await update.message.reply_text("لطفاً از دکمه‌های موجود در منوی اصلی استفاده کنید تا اقدام مورد نظر خود را انجام دهید.")
            reply_markup = await get_main_reply_keyboard(user_id)
            await update.message.reply_text("منوی اصلی:", reply_markup=reply_markup)
        return

    # --- بررسی شرایط ارسال پیام (فقط وقتی در حالت 'waiting_for_channel_message' هستیم) ---
    if not is_working_hours() and not is_admin(user_id):
        await update.message.reply_text(f"متاسفانه ربات فقط در ساعات کاری ({WORKING_HOURS_START}:00 تا {WORKING_HOURS_END}:00) فعال است.")
        del USER_STATE[user_id] # پاک کردن حالت
        reply_markup = await get_main_reply_keyboard(user_id)
        await update.message.reply_text("به منوی اصلی بازگشتید:", reply_markup=reply_markup)
        return

    if is_user_banned(user_id):
        await update.message.reply_text("شما از ارسال پیام مسدود شده‌اید.")
        del USER_STATE[user_id] # پاک کردن حالت
        reply_markup = await get_main_reply_keyboard(user_id)
        await update.message.reply_text("به منوی اصلی بازگشتید:", reply_markup=reply_markup)
        return

    if not user_alias:
        await update.message.reply_text("لطفاً ابتدا با دستور /setalias یا دکمه **تنظیم نام مستعار** نام مستعار خود را تنظیم کنید.")
        del USER_STATE[user_id] # پاک کردن حالت
        reply_markup = await get_main_reply_keyboard(user_id)
        await update.message.reply_text("به منوی اصلی بازگشتید:", reply_markup=reply_markup)
        return

    last_time = get_last_message_time(user_id)
    if last_time and (datetime.now() - last_time) < MESSAGE_INTERVAL and not is_admin(user_id):
        remaining_time = MESSAGE_INTERVAL - (datetime.now() - last_time)
        minutes = int(remaining_time.total_seconds() // 60)
        seconds = int(remaining_time.total_seconds() % 60)
        await update.message.reply_text(f"لطفاً صبر کنید. شما می‌توانید هر {int(MESSAGE_INTERVAL.total_seconds() // 60)} دقیقه یک پیام ارسال کنید. زمان باقی‌مانده: {minutes} دقیقه و {seconds} ثانیه.")
        # نیازی به حذف state نیست، کاربر همچنان در حال ارسال پیام است
        return

    message_text = update.message.text
    caption_text = update.message.caption if (update.message.photo or update.message.video) else ""

    # --- فیلتر کلمات ممنوعه ---
    if message_text and contains_forbidden_words(message_text):
        await update.message.reply_text("پیام شما حاوی کلمات ممنوعه است و ارسال نخواهد شد. عملیات لغو شد.")
        del USER_STATE[user_id] # پاک کردن حالت
        reply_markup = await get_main_reply_keyboard(user_id)
        await update.message.reply_text("به منوی اصلی بازگشتید:", reply_markup=reply_markup)
        return
    if caption_text and contains_forbidden_words(caption_text):
        await update.message.reply_text("کپشن شما حاوی کلمات ممنوعه است و رسانه شما تایید نخواهد شد. عملیات لغو شد.")
        del USER_STATE[user_id] # پاک کردن حالت
        reply_markup = await get_main_reply_keyboard(user_id)
        await update.message.reply_text("به منوی اصلی بازگشتید:", reply_markup=reply_markup)
        return

    # --- به‌روزرسانی زمان آخرین پیام ---
    update_last_message_time(user_id)

    # --- مدیریت رسانه (عکس و ویدیو) ---
    if update.message.photo or update.message.video:
        file_id = None
        file_type = None

        if update.message.photo:
            file_id = update.message.photo[-1].file_id # بالاترین کیفیت عکس
            file_type = "photo"
        elif update.message.video:
            file_id = update.message.video.file_id
            file_type = "video"

        if file_id:
            media_id = add_pending_media(user_id, file_id, file_type, caption_text)
            admin_message = (
                f"**رسانه جدید در انتظار تایید!**\n"
                f"از: {user_alias} (ID: `{user_id}`)\n"
                f"نوع: {file_type.capitalize()}\n"
                f"کپشن: {caption_text if caption_text else 'بدون کپشن'}\n\n"
                f"برای تایید/رد: /pending {media_id}"
            )
            
            # ارسال به همه ادمین‌های ثبت شده در دیتابیس
            with sqlite3.connect(DATABASE_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id FROM admins")
                admin_ids = [row[0] for row in cursor.fetchall()]

            for admin_db_id in admin_ids:
                try:
                    await context.bot.send_message(
                        chat_id=admin_db_id,
                        text=admin_message,
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e:
                    logger.warning(f"Could not send pending media notification to admin {admin_db_id}: {e}")

            await update.message.reply_text(f"رسانه شما دریافت شد و پس از تایید مدیر در کانال @{DISPLAY_CHANNEL_USERNAME} منتشر خواهد شد.")
        else:
            await update.message.reply_text("خطا در دریافت رسانه.")

    # --- مدیریت پیام متنی ---
    elif message_text:
        try:
            # اضافه کردن DISPLAY_CHANNEL_USERNAME به انتهای پیام متنی با یک @
            final_text = f"**{user_alias}:**\n{message_text}\n\n@{DISPLAY_CHANNEL_USERNAME}"
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=final_text,
                parse_mode=ParseMode.MARKDOWN
            )
            await update.message.reply_text(f"پیام شما با موفقیت در کانال @{DISPLAY_CHANNEL_USERNAME} منتشر شد.")
        except Exception as e:
            logger.error(f"Error sending message to channel: {e}", exc_info=True)
            # ارسال پیام خطا به ادمین اصلی
            if MAIN_ADMIN_ID:
                try:
                    await context.bot.send_message(chat_id=MAIN_ADMIN_ID, text=f"خطا در ارسال پیام به کانال:\n{e}\n\nپیام از کاربر: `{user_id}` (`{user_alias}`)\n\nمتن: {message_text}")
                except Exception as admin_e:
                    logger.error(f"Could not notify main admin about channel message error: {admin_e}")

            await update.message.reply_text("خطا در ارسال پیام به کانال. لطفاً با مدیر تماس بگیرید.")
    else:
        await update.message.reply_text("لطفاً یک پیام متنی یا رسانه (عکس/ویدیو) ارسال کنید.")
    
    # بعد از هر ارسال موفق یا ناموفق پیام، وضعیت کاربر را به حالت عادی برگردان
    del USER_STATE[user_id]
    reply_markup = await get_main_reply_keyboard(user_id)
    await update.message.reply_text("به منوی اصلی بازگشتید:", reply_markup=reply_markup)


async def cancel_operation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancels any ongoing operation (like waiting for message to channel)."""
    user_id = update.effective_user.id
    if user_id in USER_STATE:
        del USER_STATE[user_id]
        await update.message.reply_text("عملیات لغو شد.")
    else:
        await update.message.reply_text("هیچ عملیاتی برای لغو وجود ندارد.")
    
    reply_markup = await get_main_reply_keyboard(user_id)
    await update.message.reply_text("به منوی اصلی بازگشتید:", reply_markup=reply_markup)


# --- هندلرهای مدیریتی ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays the admin panel options with reply keyboard."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("شما دسترسی به پنل مدیریت ندارید.")
        return

    # هر حالت فعلی کاربر رو پاک کن قبل از ورود به پنل ادمین
    if user_id in USER_STATE:
        del USER_STATE[user_id]

    response_text = "**پنل مدیریت:**"
    reply_markup = await get_admin_reply_keyboard()
    await update.message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)


async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Returns to the main menu from admin panel."""
    user_id = update.effective_user.id
    # هر حالت فعلی کاربر رو پاک کن
    if user_id in USER_STATE:
        del USER_STATE[user_id]
        
    reply_markup = await get_main_reply_keyboard(user_id)
    await update.message.reply_text("به منوی اصلی بازگشتید.", reply_markup=reply_markup)


async def manage_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Provides instructions for user management."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("شما دسترسی به این بخش ندارید.")
        return

    response_text = (
        "**مدیریت کاربران:**\n"
        "مسدود کردن کاربر: `/ban [User_ID_یا_Alias]`\n"
        "رفع مسدودیت کاربر: `/unban [User_ID_یا_Alias]`\n"
        "برای یافتن ID کاربر، می‌توانید از /mystats کاربر یا از User ID Bot (@userinfobot) استفاده کنید."
    )
    await update.message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN)

async def _get_user_id_from_arg(arg: str) -> int | None:
    """Helper to get user_id from either ID or alias."""
    try:
        return int(arg)
    except ValueError:
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users WHERE alias = ?", (arg,))
            result = cursor.fetchone()
            return result[0] if result else None

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bans a user by their ID or alias."""
    admin_id = update.effective_user.id
    if not is_admin(admin_id):
        await update.message.reply_text("شما دسترسی به این دستور را ندارید.")
        return

    if not context.args:
        await update.message.reply_text("لطفاً ID یا نام مستعار کاربری که می‌خواهید مسدود کنید را وارد کنید. مثال: /ban 123456789")
        return

    target_arg = " ".join(context.args)
    target_user_id = await _get_user_id_from_arg(target_arg)

    if not target_user_id:
        await update.message.reply_text(f"کاربری با ID یا نام مستعار '{target_arg}' یافت نشد.")
        return

    if is_admin(target_user_id):
        await update.message.reply_text("شما نمی‌توانید یک مدیر را مسدود کنید.")
        return

    # Fetch username from DB if exists, otherwise use a placeholder
    target_username = f"id_{target_user_id}"
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT username FROM users WHERE user_id = ?", (target_user_id,))
        res = cursor.fetchone()
        if res:
            target_username = res[0]

    ban_user(target_user_id, target_username)
    await update.message.reply_text(f"کاربر با ID: `{target_user_id}` (نام مستعار: {get_user_alias(target_user_id) or 'نامشخص'}) با موفقیت مسدود شد.", parse_mode=ParseMode.MARKDOWN)

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unbans a user by their ID or alias."""
    admin_id = update.effective_user.id
    if not is_admin(admin_id):
        await update.message.reply_text("شما دسترسی به این دستور را ندارید.")
        return

    if not context.args:
        await update.message.reply_text("لطفاً ID یا نام مستعار کاربری که می‌خواهید رفع مسدودیت کنید را وارد کنید. مثال: /unban 123456789")
        return

    target_arg = " ".join(context.args)
    target_user_id = await _get_user_id_from_arg(target_arg)

    if not target_user_id:
        await update.message.reply_text(f"کاربری با ID یا نام مستعار '{target_arg}' یافت نشد.")
        return

    if unban_user(target_user_id):
        await update.message.reply_text(f"کاربر با ID: `{target_user_id}` (نام مستعار: {get_user_alias(target_user_id) or 'نامشخص'}) با موفقیت رفع مسدودیت شد.", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("این کاربر مسدود نیست یا یافت نشد.")

async def pending_media_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays pending media for admin review."""
    admin_id = update.effective_user.id
    if not is_admin(admin_id):
        await update.message.reply_text("شما دسترسی به این دستور را ندارید.")
        return

    pending_items = get_pending_media()
    if not pending_items:
        await update.message.reply_text("هیچ رسانه‌ای در انتظار تایید وجود ندارد.")
        return

    for item in pending_items:
        media_id, user_id, file_id, file_type, caption, _ = item
        user_alias = get_user_alias(user_id) or f"ID: {user_id}"

        keyboard = [
            [
                InlineKeyboardButton("✅ تایید", callback_data=f"approve_{media_id}"),
                InlineKeyboardButton("❌ رد", callback_data=f"reject_{media_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        message_caption = f"رسانه در انتظار تایید (ID: {media_id})\nاز: {user_alias}\nکپشن: {caption if caption else 'بدون کپشن'}"
        
        try:
            if file_type == "photo":
                await context.bot.send_photo(chat_id=admin_id, photo=file_id, caption=message_caption, reply_markup=reply_markup)
            elif file_type == "video":
                await context.bot.send_video(chat_id=admin_id, video=file_id, caption=message_caption, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Failed to send pending media {media_id} to admin {admin_id}: {e}", exc_info=True)
            await update.message.reply_text(f"خطا در نمایش رسانه {media_id} به شما: {e}")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles callback queries from inline keyboard buttons (e.g., approve/reject media)."""
    query = update.callback_query
    await query.answer() # پاسخ به کلیک کاربر برای حذف حالت لودینگ

    admin_id = query.from_user.id
    if not is_admin(admin_id):
        await query.edit_message_text("شما دسترسی به این عمل را ندارید.")
        return

    data = query.data
    action, media_id_str = data.split('_')
    media_id = int(media_id_str)

    media_item = get_pending_media(media_id)
    if not media_item:
        await query.edit_message_text(f"این رسانه قبلاً مدیریت شده یا وجود ندارد. (ID: {media_id})")
        return

    _id, user_id, file_id, file_type, caption, _ = media_item
    user_alias = get_user_alias(user_id) or f"ID: {user_id}"

    if action == "approve":
        try:
            # اضافه کردن DISPLAY_CHANNEL_USERNAME به انتهای کپشن در کانال با یک @
            final_caption = f"**{user_alias}:**\n{caption}\n\n@{DISPLAY_CHANNEL_USERNAME}"
            if file_type == "photo":
                await context.bot.send_photo(chat_id=CHANNEL_ID, photo=file_id, caption=final_caption, parse_mode=ParseMode.MARKDOWN)
            elif file_type == "video":
                await context.bot.send_video(chat_id=CHANNEL_ID, video=file_id, caption=final_caption, parse_mode=ParseMode.MARKDOWN)
            
            await query.edit_message_text(f"رسانه (ID: {media_id}) از {user_alias} با موفقیت تایید و منتشر شد.")
            delete_pending_media(media_id)
            # Notify user that their media was approved (optional)
            try:
                await context.bot.send_message(chat_id=user_id, text=f"پیام رسانه‌ای شما در کانال @{DISPLAY_CHANNEL_USERNAME} منتشر شد! ✅")
            except Exception as e:
                logger.warning(f"Could not notify user {user_id} about approved media: {e}")

        except Exception as e:
            await query.edit_message_text(f"خطا در انتشار رسانه (ID: {media_id}): {e}")
            logger.error(f"Error publishing media {media_id}: {e}", exc_info=True)
            # ارسال پیام خطا به ادمین اصلی
            if MAIN_ADMIN_ID:
                try:
                    await context.bot.send_message(chat_id=MAIN_ADMIN_ID, text=f"خطا در انتشار رسانه (ID: {media_id}) از کاربر: `{user_id}` (`{user_alias}`)\n\n{e}")
                except Exception as admin_e:
                    logger.error(f"Could not notify main admin about media publishing error: {admin_e}")

    elif action == "reject":
        delete_pending_media(media_id)
        await query.edit_message_text(f"رسانه (ID: {media_id}) از {user_alias} رد شد.")
        # Notify user that their media was rejected (optional)
        try:
            await context.bot.send_message(chat_id=user_id, text=f"پیام رسانه‌ای شما رد شد. ❌ به کانال @{DISPLAY_CHANNEL_USERNAME} ارسال نشد.")
        except Exception as e:
            logger.warning(f"Could not notify user {user_id} about rejected media: {e}")

async def my_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays personal statistics for the user."""
    user_id = update.effective_user.id
    alias = get_user_alias(user_id) or "نام مستعار تنظیم نشده"
    is_banned_status = "بله" if is_user_banned(user_id) else "خیر"

    # برای شمارش دقیق تعداد پیام‌ها، باید سیستم جداگانه‌ای برای اینکار در دیتابیس پیاده‌سازی شود.
    # به عنوان مثال، می‌توانید یک ستون message_count در جدول users اضافه کنید.
    message_count = 0 # Placeholder for actual message count

    response_text = (
        f"**آمار شخصی شما:**\n"
        f"شناسه کاربری: `{user_id}`\n"
        f"نام مستعار: **{alias}**\n"
        f"وضعیت مسدودیت: {is_banned_status}\n"
        f"تعداد پیام‌های ارسالی (این ویژگی در حال توسعه است): {message_count}\n"
    )
    await update.message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN)

async def total_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays overall bot statistics for admins."""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("شما دسترسی به این بخش را ندارید.")
        return

    total_users = get_total_users()
    banned_users = get_banned_users_count()
    total_messages_pending = len(get_pending_media()) # تعداد پیام‌های در انتظار
    total_messages_published = get_total_messages_published() # این تابع فقط رسانه های تایید شده را برمیگرداند

    response_text = (
        "**آمار کلی ربات:**\n"
        f"تعداد کل کاربران ثبت شده: {total_users}\n"
        f"کاربران مسدود شده: {banned_users}\n"
        f"رسانه‌های در انتظار تایید: {total_messages_pending}\n"
        f"کل پیام‌های رسانه‌ای منتشر شده: {total_messages_published}\n"
    )
    await update.message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a traceback to the user (if admin)."""
    logger.error("Exception while handling an update:", exc_info=context.error)

    # ارسال traceback به ادمین اصلی
    if MAIN_ADMIN_ID:
        tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
        tb_string = "".join(tb_list)
        message = (
            "An exception was raised while handling an update:\n"
            f"<pre>{html.escape(tb_string)}</pre>"
        )
        try:
            await context.bot.send_message(chat_id=MAIN_ADMIN_ID, text=message, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Failed to send error message to main admin {MAIN_ADMIN_ID}: {e}")
    else:
        logger.warning("MAIN_ADMIN_ID is not set, could not send error traceback.")

    # Determine target for error message for user
    message_target = None
    if update.effective_user and is_admin(update.effective_user.id):
        message_target = update.effective_user.id
    elif update.effective_chat:
        message_target = update.effective_chat.id

    if message_target and message_target != MAIN_ADMIN_ID: # اگر خودش ادمین اصلی نبود بهش یک پیام عمومی بده
        try:
            await context.bot.send_message(chat_id=message_target, text="خطایی در سیستم رخ داده است. مدیران ربات در جریان قرار گرفتند. لطفاً بعداً تلاش کنید.")
        except Exception as e:
            logger.error(f"Failed to send generic error message to {message_target}: {e}")
    else:
        logger.warning("Error occurred, but no effective chat/user to send notification.")

# --- تابع Keep-Alive برای جلوگیری از خواب رفتن Render ---
def keep_alive():
    """Pings the Render external URL at regular intervals to keep the service alive."""
    if not RENDER_EXTERNAL_URL:
        logger.warning("RENDER_EXTERNAL_URL is not set. Keep-alive function will not run.")
        return

    while True:
        try:
            response = requests.get(RENDER_EXTERNAL_URL)
            if response.status_code == 200:
                logger.info(f"Keep-alive ping successful at {datetime.now()}.")
            else:
                logger.warning(f"Keep-alive ping failed with status code {response.status_code}.")
        except requests.exceptions.RequestException as e:
            logger.error(f"Keep-alive request failed: {e}")
        
        # پینگ هر 10 تا 15 دقیقه (برای Render Worker معمولاً 5-15 دقیقه خوبه)
        time.sleep(13 * 60) # 13 دقیقه

# --- تابع اصلی برای راه‌اندازی ربات ---
def main() -> None:
    """Starts the bot and the keep-alive thread."""
    init_db()

    # بررسی وجود متغیرهای محیطی حیاتی
    if not TELEGRAM_BOT_TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN environment variable is not set. Bot cannot start.")
        raise ValueError("TELEGRAM_BOT_TOKEN is not set. Please set it in your environment variables.")
    
    if not CHANNEL_ID:
        logger.critical("CHANNEL_ID environment variable is not set. Bot cannot start.")
        raise ValueError("CHANNEL_ID is not set. Please set it in your environment variables.")

    if not MAIN_ADMIN_ID:
        logger.critical("MAIN_ADMIN_ID environment variable is not set. Critical errors will not be reported to a specific admin.")
        # نیازی به raise ValueError نیست، چون ربات بدون ادمین اصلی هم می‌تونه کار کنه ولی با قابلیت‌های محدودتر

    # شروع Keep-Alive در یک ترد جداگانه
    if RENDER_EXTERNAL_URL:
        keep_alive_thread = threading.Thread(target=keep_alive)
        keep_alive_thread.daemon = True # باعث می‌شود ترد با بسته شدن برنامه اصلی بسته شود
        keep_alive_thread.start()
        logger.info("Keep-alive thread started.")
    else:
        logger.warning("RENDER_EXTERNAL_URL not set. Keep-alive feature is disabled. Bot might go to sleep on Render.")


    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # --- اضافه کردن هندلرها ---
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel_operation)) # دستور /cancel برای لغو عملیات
    # این CommandHandler برای /setalias سنتی است، میتونید حذفش کنید اگر فقط دکمه رو میخواید
    application.add_handler(CommandHandler("setalias", set_alias_button_handler)) 

    # هندلر برای دکمه های ریپلای کیبورد (کاربرپسند)
    application.add_handler(MessageHandler(filters.Regex("^👤 تنظیم نام مستعار$") & ~filters.COMMAND, set_alias_button_handler))
    application.add_handler(MessageHandler(filters.Regex("^📊 آمار من$") & ~filters.COMMAND, my_stats_command))
    application.add_handler(MessageHandler(filters.Regex("^ℹ️ راهنما$") & ~filters.COMMAND, help_command))
    application.add_handler(MessageHandler(filters.Regex("^📝 ارسال پیام$") & ~filters.COMMAND, request_send_message)) # هندلر جدید برای دکمه "ارسال پیام"

    application.add_handler(MessageHandler(filters.Regex("^⚙️ پنل مدیریت$") & ~filters.COMMAND & IS_ADMIN_FILTER, admin_panel))
    application.add_handler(MessageHandler(filters.Regex("^📋 پیام‌های در انتظار$") & ~filters.COMMAND & IS_ADMIN_FILTER, pending_media_command))
    application.add_handler(MessageHandler(filters.Regex("^👥 مدیریت کاربران$") & ~filters.COMMAND & IS_ADMIN_FILTER, manage_users))
    application.add_handler(MessageHandler(filters.Regex("^📊 آمار کل$") & ~filters.COMMAND & IS_ADMIN_FILTER, total_stats_command))
    application.add_handler(MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$") & ~filters.COMMAND & IS_ADMIN_FILTER, back_to_main_menu))


    # هندلرهای مدیریتی (برای حالتی که ادمین‌ها دستور رو تایپ کنن، اگرچه دکمه‌ها بهترن)
    application.add_handler(CommandHandler("adminpanel", admin_panel, filters=IS_ADMIN_FILTER))
    application.add_handler(CommandHandler("manageusers", manage_users, filters=IS_ADMIN_FILTER))
    application.add_handler(CommandHandler("ban", ban_command, filters=IS_ADMIN_FILTER))
    application.add_handler(CommandHandler("unban", unban_command, filters=IS_ADMIN_FILTER))
    application.add_handler(CommandHandler("pending", pending_media_command, filters=IS_ADMIN_FILTER))
    application.add_handler(CommandHandler("mystats", my_stats_command)) # این برای همه کاربرانه
    application.add_handler(CommandHandler("totalstats", total_stats_command, filters=IS_ADMIN_FILTER))

    # هندلر اصلی برای پیام‌های متنی و رسانه: این حالا فقط پیام‌های وقتی کاربر در حالت خاصی است را می‌گیرد
    application.add_handler(
        MessageHandler(filters.TEXT | filters.PHOTO | filters.VIDEO & ~filters.COMMAND, handle_message)
    )

    # هندلر برای دکمه‌های اینلاین (تایید/رد رسانه)
    application.add_handler(CallbackQueryHandler(button_callback))

    # افزودن Error Handler
    application.add_error_handler(error_handler)

    logger.info("Bot started polling...")
    application.run_polling(poll_interval=3, timeout=30) 

if __name__ == "__main__":
    main()