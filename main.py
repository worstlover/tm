import os
import sqlite3
import logging
from datetime import datetime, timedelta
from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode

# تنظیمات لاگینگ
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- متغیرهای محیطی ---
# مطمئن شوید که این متغیرها در Render یا لوکال تنظیم شده‌اند
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID")) # CHANNEL_ID باید عدد صحیح باشد
DATABASE_PATH = os.getenv("DATABASE_PATH", "bot_database.db")

# --- تنظیمات ربات ---
MESSAGE_INTERVAL = timedelta(minutes=2)  # محدودیت 2 دقیقه بین پیام‌ها
WORKING_HOURS_START = 8  # 8 صبح
WORKING_HOURS_END = 22  # 10 شب

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
    "فلاکتبار", "نفرت‌انگیز", "ناخوشایند", "مشمئزکننده", "کثیف", "زشت", "کريه",
    "شیطان", "ابلیس", "جن", "دیو", "اهریمن", "شیاطین", "جنایتکار", "جنایتکاران",
    "قاتلین", "نابودگران", "مفسدین", "ستمکاران", "ظالمین", "جهنمی", "عذاب‌آور",
    "نفرین", "لعنت", "مرگ", "تباهی", "نابودی", "هلاکت", "زوال", "فنا", "جهنم", "دوزخ",
    "شکنجه", "آزار", "اذیت", "خشونت", "تجاوز", "نفرت", "کینه", "خشم", "کینه_توز",
    "حسادت", "بخل", "طمع", "حرص", "دروغ", "فریب", "خیانت", "نامردی", "پستی", "رذالت",
    "بی‌غیرت", "بی‌شرف", "بی‌وجدان", "بی‌رحم", "سنگدل", "ظالم", "ستمگر", "متعصب",
    "جاهل", "نادان", "عقب‌مانده", "بدوی", "همجی", "وحشی", "افراطی", "تندرو", "خشونت‌طلب",
    "وحشتناک", "ترسناک", "مهیب", "کابوس", "فاجعه", "غم‌انگیز", "تلخ", "دردناک",
    "شوم", "نحس", "بدیمن", "شر", "پلیدی", "شرارت", "فساد", "ریا", "دروغگویی",
    "رذایل", "نکبت", "بدبختی", "مصیبت", "بحران", "فلاکت", "ویرانی", "تباهی",
    "هلاکت", "انحطاط", "انحراف", "خطا", "اشتباه", "گناه", "معصیت", "جرم", "بزه",
    "جنایت", "تبانی", "دسیسه", "توطئه", "مکر", "حیله", "فریبکاری", "نیرنگ", "کلاهبرداری",
    "تقلب", "سرقت", "غارت", "تاراج", "زورگیری", "باج‌خواهی", "اخاذی", "ارتشا",
    "رشوه‌خواری", "فساد_مالی", "اختلاس", "پولشویی", "قاچاق", "سوداگری", "انحصار",
    "احتکار", "گرانفروشی", "کم‌فروشی", "غش", "تدلیس", "تقلب_در_کالا", "تقلب_در_خدمات",
    "دروغ_پراکنی", "شایعه_سازی", "افترا", "تهمت", "بدنامی", "رسوایی", "فحاشی",
    "ناسزا", "بددهنی", "توهین", "تحقیر", "تمسخر", "استهزا", "جوک_زشت", "شوخی_رکیک",
    "تهدید", "ارعاب", "زورگویی", "گردن‌کشی", "قلدری", "جنایت", "بزهکاری", "مجرمیت",
    "شرارت", "پلیدی", "شیطنت", "شیادی", "فریبندگی", "ترفند", "حقه", "نیرنگ",
    "تزویر", "ریا", "دوز و کلک", "بازیگر", "متظاهر", "ریاکارانه", "دورو", "منافق",
    "توطئه‌آمیز", "دسیسه‌گر", "غدر", "بی‌وفایی", "عهدشکنی", "پیمان‌شکنی",
    "بی‌اخلاقی", "ناشایست", "نامناسب", "زشت", "ناپسند", "شنیع", "فجیع", "نفرت‌بار",
    "انزجارآور", "ناگوار", "سوء", "بد", "ناصواب", "منحرف", "گمراه", "خطاکار",
    "نافرمان", "عصیانگر", "سرکش", "متجاوز", "هتاک", "اهانت‌آمیز", "افتراآمیز",
    "زننده", "نکوهیده", "مذموم", "مورد_انتقاد", "منفی", "خرابکار", "اخلالگر",
    "ویرانگر", "مخرب", "آسیب‌رسان", "زیانبار", "مهلک", "کشنده", "مرگبار", "کشنده",
    "سمی", "آلوده", "مضر", "خطرناک", "وحشتناک", "ترسناک", "مخوف", "وحشتزا",
    "ترس‌انگیز", "ناامن", "پرخطر", "تهدیدآمیز", "آسیب‌پذیر", "بی‌دفاع", "ضعیف",
    "ناتوان", "عاجز", "بیچاره", "مفلوک", "تیره_روز", "بدبخت", "مصیبت‌زده",
    "فاجعه‌آور", "غم‌انگیز", "حزن‌آور", "اندوهبار", "دلخراش", "دردناک", "زجرآور",
    "شکنجه‌آور", "طاقت‌فرسا", "جانکاه", "پایان‌دهنده", "ویران‌کننده", "تباه‌کننده",
    "نابودکننده", "فناکننده", "مخرب", "شوم", "نحس", "بدشگون", "تاریک", "سیاه",
    "تیره", "عبوس", "غمبار", "اندوهگین", "مغموم", "افسرده", "افسرده‌کننده",
    "نومید", "مایوس", "مأیوس‌کننده", "دلگیر", "دلتنگ", "بی‌قرار", "بی‌تاب",
    "غمزده", "مصیبت_بار", "بحرانی", "خطرناک", "مهلک", "مرگبار", "کثیف", "زشت",
    "نامطبوع", "منزجرکننده", "حال_به_هم_زن", "غیر_قابل_تحمل", "فاسد", "خراب",
    "ناپاک", "نجس", "پلید", "کثیف", "چسبناک", "بودار", "گندیده", "پوسیده",
    "خراب‌شده", "از_بین_رفته", "نابود_شده", "ویران_شده", "سوخته", "مخروبه",
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

def is_admin(user_id):
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
        return cursor.fetchone() is not None

def get_user_alias(user_id):
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT alias FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result[0] if result else None

def set_user_alias(user_id, username, alias):
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT OR REPLACE INTO users (user_id, username, alias, is_banned) VALUES (?, ?, ?, COALESCE((SELECT is_banned FROM users WHERE user_id = ?), 0))",
                           (user_id, username, alias, user_id))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False # Alias already exists

def get_last_message_time(user_id):
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT last_message_time FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        if result and result[0]:
            return datetime.fromisoformat(result[0])
        return None

def update_last_message_time(user_id):
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET last_message_time = ? WHERE user_id = ?",
                       (datetime.now().isoformat(), user_id))
        conn.commit()

def is_user_banned(user_id):
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result[0] == 1 if result else False

def ban_user(user_id, username):
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO users (user_id, username, is_banned, alias, last_message_time) VALUES (?, ?, 1, COALESCE((SELECT alias FROM users WHERE user_id = ?), NULL), COALESCE((SELECT last_message_time FROM users WHERE user_id = ?), NULL))",
                       (user_id, username, user_id, user_id))
        conn.commit()

def unban_user(user_id):
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        return cursor.rowcount > 0

def add_pending_media(user_id, file_id, file_type, caption):
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO pending_media (user_id, file_id, file_type, caption, message_time) VALUES (?, ?, ?, ?, ?)",
                       (user_id, file_id, file_type, caption, datetime.now().isoformat()))
        conn.commit()
        return cursor.lastrowid

def get_pending_media(media_id=None):
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        if media_id:
            cursor.execute("SELECT * FROM pending_media WHERE id = ?", (media_id,))
            return cursor.fetchone()
        else:
            cursor.execute("SELECT * FROM pending_media ORDER BY message_time ASC")
            return cursor.fetchall()

def delete_pending_media(media_id):
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM pending_media WHERE id = ?", (media_id,))
        conn.commit()
        return cursor.rowcount > 0

def get_total_users():
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        return cursor.fetchone()[0]

def get_banned_users_count():
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        return cursor.fetchone()[0]

def get_total_messages():
    # این تابع فرض می کند که هر ورودی در pending_media یک پیام است.
    # اگر پیام های متنی مستقیم را هم میخواهید بشمارید، نیاز به جدول مجزا دارید.
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM pending_media")
        return cursor.fetchone()[0]


# --- توابع کمکی ---
def is_working_hours():
    now = datetime.now()
    return WORKING_HOURS_START <= now.hour < WORKING_HOURS_END

def contains_forbidden_words(text):
    if not text:
        return False
    # برای جلوگیری از تشخیص کلمات جزئی، از مرز کلمات (مثلاً با regex) استفاده کنید
    # اما برای سادگی، فعلاً چک می‌کنیم که آیا کلمه به عنوان زیررشته وجود دارد
    for word in FORBIDDEN_WORDS:
        if word in text.lower():
            return True
    return False

# --- هندلرهای ربات (Async Functions) ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    alias = get_user_alias(user_id)
    message = (
        "به ربات مدیریت کانال ناشناس خوش آمدید! 👋\n"
        "این ربات به شما امکان می‌دهد پیام‌ها و رسانه‌ها را به صورت ناشناس در کانال ارسال کنید.\n\n"
    )
    if alias:
        message += f"نام مستعار فعلی شما: **{alias}**\n"
        message += "برای ارسال پیام متنی، کافیست پیام خود را برای من ارسال کنید."
    else:
        message += "برای شروع، ابتدا باید یک نام مستعار برای خود انتخاب کنید.\n"
        message += "لطفاً از دستور /setalias [نام_مستعار] استفاده کنید."
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    response_text = (
        "راهنمای استفاده از ربات مدیریت کانال ناشناس:\n\n"
        "**دستورات کاربری:**\n"
        "📝 **ارسال پیام:** کافیست پیام متنی یا رسانه خود (عکس/ویدیو) را برای من ارسال کنید.\n"
        "👤 **/setalias [نام_مستعار]**: نام مستعار خود را تنظیم کنید (فقط یک بار).\n"
        "📊 **/mystats**: مشاهده آمار شخصی (پیام‌های ارسالی، وضعیت مسدودیت).\n"
        "ℹ️ **/help**: نمایش این راهنما.\n\n"
    )
    if is_admin(user_id):
        response_text += (
            "**دستورات مدیر:**\n"
            "⚙️ **/adminpanel**: دسترسی به پنل مدیریت.\n"
            "👥 **/manageusers**: مدیریت کاربران (مسدود/رفع مسدودیت).\n"
            "📋 **/pending**: مشاهده پیام‌های رسانه‌ای در انتظار تایید.\n"
            "📊 **/totalstats**: مشاهده آمار کلی ربات.\n"
        )
    await update.message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN)


async def setalias_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    username = update.effective_user.username or f"id_{user_id}"

    if len(context.args) == 0:
        await update.message.reply_text("لطفاً یک نام مستعار وارد کنید. مثال: /setalias روبات")
        return

    new_alias = " ".join(context.args)
    if contains_forbidden_words(new_alias):
        await update.message.reply_text("نام مستعار شما شامل کلمات ممنوعه است. لطفاً نام دیگری انتخاب کنید.")
        return

    current_alias = get_user_alias(user_id)
    if current_alias:
        await update.message.reply_text(f"شما قبلاً نام مستعار **{current_alias}** را انتخاب کرده‌اید و فقط یک بار امکان تغییر آن وجود دارد. در صورت نیاز به تغییر، با مدیران تماس بگیرید.", parse_mode=ParseMode.MARKDOWN)
        return

    if set_user_alias(user_id, username, new_alias):
        await update.message.reply_text(f"نام مستعار شما با موفقیت به **{new_alias}** تنظیم شد.", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("این نام مستعار قبلاً استفاده شده است. لطفاً نام دیگری انتخاب کنید.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_username = update.effective_user.username or f"id_{user_id}"
    user_alias = get_user_alias(user_id)

    if not is_working_hours() and not is_admin(user_id):
        await update.message.reply_text("متاسفانه ربات در ساعات کاری (۸ صبح تا ۱۰ شب) فعال است.")
        return

    if is_user_banned(user_id):
        await update.message.reply_text("شما از ارسال پیام مسدود شده‌اید.")
        return

    if not user_alias:
        await update.message.reply_text("لطفاً ابتدا با دستور /setalias نام مستعار خود را تنظیم کنید.")
        return

    last_time = get_last_message_time(user_id)
    if last_time and (datetime.now() - last_time) < MESSAGE_INTERVAL and not is_admin(user_id):
        remaining_time = MESSAGE_INTERVAL - (datetime.now() - last_time)
        minutes = int(remaining_time.total_seconds() // 60)
        seconds = int(remaining_time.total_seconds() % 60)
        await update.message.reply_text(f"لطفاً صبر کنید. شما می‌توانید هر ۲ دقیقه یک پیام ارسال کنید. زمان باقی‌مانده: {minutes} دقیقه و {seconds} ثانیه.")
        return

    message_text = update.message.text
    if message_text and contains_forbidden_words(message_text):
        await update.message.reply_text("پیام شما حاوی کلمات ممنوعه است و ارسال نخواهد شد.")
        return

    update_last_message_time(user_id) # آپدیت زمان آخرین پیام پس از تمام بررسی‌ها

    if update.message.photo or update.message.video:
        file_id = None
        file_type = None
        caption = update.message.caption or ""

        if contains_forbidden_words(caption):
            await update.message.reply_text("کپشن شما حاوی کلمات ممنوعه است و رسانه شما تایید نخواهد شد.")
            return

        if update.message.photo:
            file_id = update.message.photo[-1].file_id # بالاترین کیفیت عکس
            file_type = "photo"
        elif update.message.video:
            file_id = update.message.video.file_id
            file_type = "video"

        if file_id:
            media_id = add_pending_media(user_id, file_id, file_type, caption)
            admin_message = (
                f"**رسانه جدید در انتظار تایید!**\n"
                f"از: {user_alias} (ID: {user_id})\n"
                f"نوع: {file_type.capitalize()}\n"
                f"کپشن: {caption}\n\n"
                f"برای تایید/رد: /pending {media_id}"
            )
            # ارسال به همه ادمین‌ها
            for admin_id_row in context.bot.get_chat_administrators(chat_id=CHANNEL_ID): # این روش نیاز به تغییر دارد اگر ربات ادمین کانال نباشد
                 if is_admin(admin_id_row.user.id): # اطمینان از اینکه فقط ادمین های ثبت شده دریافت کنند
                    await context.bot.send_message(
                        chat_id=admin_id_row.user.id,
                        text=admin_message,
                        parse_mode=ParseMode.MARKDOWN
                    )
            await update.message.reply_text("رسانه شما دریافت شد و پس از تایید مدیر در کانال منتشر خواهد شد.")
        else:
            await update.message.reply_text("خطا در دریافت رسانه.")

    elif message_text:
        # ارسال پیام متنی به کانال
        try:
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=f"**{user_alias}:**\n{message_text}",
                parse_mode=ParseMode.MARKDOWN
            )
            await update.message.reply_text("پیام شما با موفقیت در کانال منتشر شد.")
        except Exception as e:
            logger.error(f"Error sending message to channel: {e}")
            await update.message.reply_text("خطا در ارسال پیام به کانال. لطفاً با مدیر تماس بگیرید.")
    else:
        await update.message.reply_text("لطفاً یک پیام متنی یا رسانه (عکس/ویدیو) ارسال کنید.")


# --- هندلرهای مدیریتی ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("شما دسترسی به پنل مدیریت ندارید.")
        return

    response_text = (
        "**پنل مدیریت:**\n\n"
        "📋 **/pending**: مشاهده و مدیریت رسانه‌های در انتظار تایید.\n"
        "👥 **/manageusers**: مدیریت کاربران (مسدود/رفع مسدودیت).\n"
        "📊 **/totalstats**: مشاهده آمار کلی ربات.\n"
        # می‌توانید دستورات مدیریتی دیگر را اینجا اضافه کنید
    )
    await update.message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN)


async def manage_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("شما دسترسی به این بخش ندارید.")
        return

    response_text = (
        "**مدیریت کاربران:**\n"
        "مسدود کردن کاربر: `/ban [User_ID/Alias]`\n"
        "رفع مسدودیت کاربر: `/unban [User_ID/Alias]`\n"
        "برای یافتن ID کاربر، می‌توانید از /mystats کاربر یا از User ID Bot استفاده کنید."
    )
    await update.message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN)


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin_id = update.effective_user.id
    if not is_admin(admin_id):
        await update.message.reply_text("شما دسترسی به این دستور را ندارید.")
        return

    if not context.args:
        await update.message.reply_text("لطفاً ID یا نام مستعار کاربری که می‌خواهید مسدود کنید را وارد کنید. مثال: /ban 123456789")
        return

    target = " ".join(context.args)
    target_user_id = None

    # Try to convert to int (User ID)
    try:
        target_user_id = int(target)
    except ValueError:
        # If not an ID, try to find alias
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users WHERE alias = ?", (target,))
            result = cursor.fetchone()
            if result:
                target_user_id = result[0]

    if not target_user_id:
        await update.message.reply_text(f"کاربری با ID یا نام مستعار '{target}' یافت نشد.")
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
    admin_id = update.effective_user.id
    if not is_admin(admin_id):
        await update.message.reply_text("شما دسترسی به این دستور را ندارید.")
        return

    if not context.args:
        await update.message.reply_text("لطفاً ID یا نام مستعار کاربری که می‌خواهید رفع مسدودیت کنید را وارد کنید. مثال: /unban 123456789")
        return

    target = " ".join(context.args)
    target_user_id = None

    # Try to convert to int (User ID)
    try:
        target_user_id = int(target)
    except ValueError:
        # If not an ID, try to find alias
        with sqlite3.connect(DATABASE_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users WHERE alias = ?", (target,))
            result = cursor.fetchone()
            if result:
                target_user_id = result[0]

    if not target_user_id:
        await update.message.reply_text(f"کاربری با ID یا نام مستعار '{target}' یافت نشد.")
        return

    if unban_user(target_user_id):
        await update.message.reply_text(f"کاربر با ID: `{target_user_id}` (نام مستعار: {get_user_alias(target_user_id) or 'نامشخص'}) با موفقیت رفع مسدودیت شد.", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("این کاربر مسدود نیست یا یافت نشد.")


async def pending_media_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
                {"text": "✅ تایید", "callback_data": f"approve_{media_id}"},
                {"text": "❌ رد", "callback_data": f"reject_{media_id}"}
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        message_caption = f"رسانه در انتظار تایید (ID: {media_id})\nاز: {user_alias}\nکپشن: {caption}"
        
        if file_type == "photo":
            await context.bot.send_photo(chat_id=admin_id, photo=file_id, caption=message_caption, reply_markup=reply_markup)
        elif file_type == "video":
            await context.bot.send_video(chat_id=admin_id, video=file_id, caption=message_caption, reply_markup=reply_markup)

# Callback handler for pending media actions
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer() # پاسخ به کلیک کاربر برای حذف حالت لودینگ

    data = query.data
    action, media_id = data.split('_')
    media_id = int(media_id)

    media_item = get_pending_media(media_id)
    if not media_item:
        await query.edit_message_text(f"این رسانه قبلاً مدیریت شده یا وجود ندارد. (ID: {media_id})")
        return

    _id, user_id, file_id, file_type, caption, _ = media_item
    user_alias = get_user_alias(user_id) or f"ID: {user_id}"

    if action == "approve":
        try:
            if file_type == "photo":
                await context.bot.send_photo(chat_id=CHANNEL_ID, photo=file_id, caption=f"**{user_alias}:**\n{caption}", parse_mode=ParseMode.MARKDOWN)
            elif file_type == "video":
                await context.bot.send_photo(chat_id=CHANNEL_ID, photo=file_id, caption=f"**{user_alias}:**\n{caption}")