import os
import time
import uuid
import asyncio
import mimetypes
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from quart import Quart, request, Response

# --- دریافت اطلاعات از Render ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SESSION_STRING = os.environ.get("SESSION_STRING") # متغیر جدید برای رفع ارور
ADMIN_ID = 98097025
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")

# --- تنظیمات پیش‌فرض (قابل تغییر از پنل) ---
SETTINGS = {
    'expire_time': 3600,  # پیش‌فرض 1 ساعت
    'is_active': True
}

# --- راه‌اندازی کلاینت ---
# استفاده از StringSession برای جلوگیری از لاگین تکراری و ارور FloodWait
if SESSION_STRING:
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
else:
    # حالت اضطراری (اگر سشن ست نشده باشد)
    client = TelegramClient('bot_session', API_ID, API_HASH)

app = Quart(__name__)
links_db = {}

@app.before_serving
async def startup():
    print("🤖 Bot is starting...")
    await client.start(bot_token=BOT_TOKEN)
    print("✅ Bot connected!")

# --- دستور پنل مدیریت ---
@client.on(events.NewMessage(pattern='/admin', from_users=ADMIN_ID))
async def admin_panel(event):
    buttons = [
        [Button.inline(f"وضعیت ربات: {'✅ فعال' if SETTINGS['is_active'] else '❌ غیرفعال'}", data="toggle_active")],
        [Button.inline("⏱ تنظیم پیش‌فرض: 30 دقیقه", data="set_time_1800"),
         Button.inline("⏱ تنظیم پیش‌فرض: 1 ساعت", data="set_time_3600")],
        [Button.inline("⏱ تنظیم پیش‌فرض: 2 ساعت", data="set_time_7200"),
         Button.inline("🗑 حذف همه لینک‌ها", data="clear_all")]
    ]
    await event.reply("🛠 **پنل مدیریت ربات**\n\nتنظیمات مورد نظر را انتخاب کنید:", buttons=buttons)

# --- هندلر دریافت فایل ---
@client.on(events.NewMessage(incoming=True))
async def handle_file(event):
    if event.sender_id != ADMIN_ID:
        return
    
    # نادیده گرفتن دستورات متنی
    if event.text and event.text.startswith('/'):
        return

    if not SETTINGS['is_active']:
        await event.reply("❌ ربات در حال حاضر توسط ادمین غیرفعال شده است.")
        return

    if not event.media:
        await event.reply("❌ لطفاً فقط فایل ارسال کنید.")
        return

    # پردازش و تولید لینک
    try:
        msg = await event.reply("🔄 در حال پردازش...")
        
        unique_id = str(uuid.uuid4())[:8]
        expire_time = time.time() + SETTINGS['expire_time']
        
        # تشخیص نوع فایل برای متن پیام
        file_type = "فایل"
        mime_type = event.message.file.mime_type
        can_stream = False
        
        if mime_type:
            if 'video' in mime_type:
                file_type = "ویدیو"
                can_stream = True
            elif 'audio' in mime_type:
                file_type = "صوت"
                can_stream = True
            elif 'image' in mime_type:
                file_type = "عکس"
                can_stream = True

        links_db[unique_id] = {
            'msg': event.message,
            'expire': expire_time,
            'filename': event.message.file.name or f"{file_type}_{unique_id}",
            'mime': mime_type
        }
        
        dl_link = BASE_URL.rstrip('/') + f"/dl/{unique_id}"
        stream_link = BASE_URL.rstrip('/') + f"/stream/{unique_id}"
        
        # ساخت متن نهایی
        response_text = (
            f"✅ **لینک شما آماده است!**\n\n"
            f"📂 نوع: {file_type}\n"
            f"⏳ انقضا: {SETTINGS['expire_time'] // 60} دقیقه دیگر\n\n"
            f"📥 **لینک دانلود مستقیم:**\n`{dl_link}`\n"
        )
        
        # اگر ویدیو یا آهنگ بود، لینک پخش آنلاین هم بده
        if can_stream:
            response_text += f"\n▶️ **لینک تماشای آنلاین:**\n`{stream_link}`"

        buttons = [[Button.inline("❌ حذف لینک", data=f"del_{unique_id}")]]
        
        await msg.edit(response_text, buttons=buttons, link_preview=False)

    except Exception as e:
        print(f"Error: {e}")
        await event.reply("خطایی رخ داد.")

# --- هندلر دکمه‌ها (مدیریت + لینک‌ها) ---
@client.on(events.CallbackQuery)
async def callback_handler(event):
    data = event.data.decode('utf-8')
    
    # --- بخش مدیریت ---
    if data == "toggle_active":
        SETTINGS['is_active'] = not SETTINGS['is_active']
        status = "✅ فعال" if SETTINGS['is_active'] else "❌ غیرفعال"
        await event.answer(f"وضعیت ربات تغییر کرد به: {status}")
        # رفرش پنل
        await admin_panel(event)
        
    elif data.startswith("set_time_"):
        seconds = int(data.split("_")[2])
        SETTINGS['expire_time'] = seconds
        await event.answer(f"زمان پیش‌فرض روی {seconds//60} دقیقه تنظیم شد.")
        
    elif data == "clear_all":
        count = len(links_db)
        links_db.clear()
        await event.answer(f"تمام {count} لینک فعال حذف شدند.", alert=True)

    # --- بخش مدیریت لینک فایل ---
    elif data.startswith("del_"):
        _, uid = data.split("_")
        if uid in links_db:
            del links_db[uid]
            await event.answer("لینک حذف شد.", alert=True)
            await event.edit("🗑 این لینک دستی حذف شد.")
        else:
            await event.answer("لینک یافت نشد یا منقضی شده.", alert=True)

# --- دانلود و استریم ---
async def serve_file(unique_id, disposition):
    data = links_db.get(unique_id)
    if not data:
        return "❌ Error: Link not found or deleted.", 404
    
    if time.time() > data['expire']:
        del links_db[unique_id]
        return "⏳ Error: Link expired.", 403
        
    msg = data['msg']
    file_name = data['filename']
    file_size = msg.file.size
    mime_type = data['mime'] or 'application/octet-stream'

    headers = {
        'Content-Type': mime_type,
        'Content-Disposition': f'{disposition}; filename="{file_name}"',
        'Content-Length': str(file_size),
        'Accept-Ranges': 'bytes' # برای پلیرهای آنلاین مهمه
    }

    async def file_generator():
        async for chunk in client.iter_download(msg.media):
            yield chunk

    return Response(file_generator(), headers=headers)

@app.route('/dl/<unique_id>')
async def download_route(unique_id):
    return await serve_file(unique_id, 'attachment')

@app.route('/stream/<unique_id>')
async def stream_route(unique_id):
    return await serve_file(unique_id, 'inline')

@app.route('/')
async def home():
    return "Bot is running with Admin Panel! 🚀"

if __name__ == '__main__':
    PORT = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=PORT)
