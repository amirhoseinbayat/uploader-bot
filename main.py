import os
import time
import uuid
import re
import asyncio
import certifi 
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaWebPage
from quart import Quart, request, Response
from motor.motor_asyncio import AsyncIOMotorClient

# --- تنظیمات ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SESSION_STRING = os.environ.get("SESSION_STRING")
MONGO_URL = os.environ.get("MONGO_URL")

# ⚠️ آیدی عددی خودتان را وارد کنید
ADMIN_ID = 98097025  

BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")
SETTINGS = {'expire_time': 3600, 'is_active': True}

# --- اتصال به دیتابیس (نسخه ضد ضربه SSL) ---
mongo_client = None
links_col = None

if not MONGO_URL:
    print("❌ خطا: MONGO_URL تنظیم نشده است!")
else:
    try:
        # 🟢 تغییر مهم: غیرفعال کردن سخت‌گیری SSL برای اتصال تضمینی
        mongo_client = AsyncIOMotorClient(
            MONGO_URL, 
            tls=True,
            tlsAllowInvalidCertificates=True  # این خط مشکل هندشیک را حل می‌کند
        )
        db = mongo_client['uploader_bot']
        links_col = db['links']
    except Exception as e:
        print(f"❌ خطا در تعریف کلاینت مونگو: {e}")

# --- اتصال به تلگرام ---
if SESSION_STRING:
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
else:
    client = TelegramClient('bot_session', API_ID, API_HASH)

app = Quart(__name__)

@app.before_serving
async def startup():
    print("🤖 Bot Starting...")
    if not SESSION_STRING:
        await client.start(bot_token=BOT_TOKEN)
    else:
        try: await client.connect()
        except: await client.start(bot_token=BOT_TOKEN)
    
    # تست اتصال واقعی به دیتابیس
    if mongo_client:
        try:
            # یک پینگ واقعی می‌فرستیم تا مطمئن شویم وصل شده
            await mongo_client.admin.command('ping')
            print(f"✅ Bot Connected! MongoDB Status: 🟢 Connected (SSL Bypassed)")
        except Exception as e:
            print(f"❌ MongoDB Error: {e}")
            # اگر باز هم وصل نشد، لاگ کامل بدهد
    else:
        print(f"⚠️ MongoDB URL Missing!")

# --- تابع کمکی: ساخت لینک ---
async def generate_link_for_message(message, reply_to_msg):
    # چک کردن وضعیت دیتابیس قبل از ذخیره
    if links_col is None:
        await reply_to_msg.edit("❌ دیتابیس وصل نیست. لطفاً لاگ‌ها را چک کنید.")
        return

    try:
        unique_id = str(uuid.uuid4())[:8]
        expire_time = time.time() + SETTINGS['expire_time']
        
        file_name = "file"
        mime_type = "application/octet-stream"
        file_size = 0
        
        if hasattr(message, 'file') and message.file:
            if message.file.name:
                file_name = message.file.name
            else:
                ext = message.file.ext or ""
                file_name = f"downloaded_file{ext}"
            mime_type = message.file.mime_type
            file_size = message.file.size
        else:
            return

        can_stream = False
        if 'video' in mime_type or 'audio' in mime_type:
            can_stream = True

        link_data = {
            'unique_id': unique_id,
            'chat_id': message.chat_id,
            'msg_id': message.id,
            'expire': expire_time,
            'filename': file_name,
            'mime': mime_type,
            'size': file_size
        }
        
        # ذخیره در دیتابیس
        await links_col.insert_one(link_data)
        
        dl_url = f"{BASE_URL}/dl/{unique_id}"
        stream_url = f"{BASE_URL}/stream/{unique_id}"
        
        txt = (f"✅ **لینک ابدی شد!** (Database)\n📄 `{file_name}`\n📦 حجم: {file_size // 1024 // 1024} MB\n\n📥 **دانلود:**\n`{dl_url}`")
        if can_stream:
            txt += f"\n\n▶️ **پخش آنلاین:**\n`{stream_url}`"
            
        await reply_to_msg.edit(txt, buttons=[[Button.inline("❌ حذف", data=f"del_{unique_id}")]])
        
    except Exception as e:
        print(f"Error saving to DB: {e}")
        await reply_to_msg.edit(f"❌ خطا در ذخیره: {e}")

# --- هندلر استارت ---
@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if event.sender_id == ADMIN_ID:
        buttons = [
            [Button.inline(f"وضعیت: {'✅ فعال' if SETTINGS['is_active'] else '❌'}", data="toggle_active")],
            [Button.inline("⏱ 1 ساعت", data="set_time_3600"), Button.inline("🗑 فرمت دیتابیس", data="clear_all")]
        ]
        status = "🟢 دیتابیس متصل است" if mongo_client else "🔴 دیتابیس قطع است"
        await event.reply(f"👋 **سلام قربان!**\n{status}\nفایل بفرستید.", buttons=buttons)

# --- هندلر دریافت فایل ---
@client.on(events.NewMessage(incoming=True))
async def handle_file(event):
    if event.sender_id != ADMIN_ID: return
    if event.text and event.text.startswith('/'): return
    if isinstance(event.media, MessageMediaWebPage): return
    if not event.media: return

    msg = await event.reply("🍃 در حال ذخیره در دیتابیس...")
    await generate_link_for_message(event.message, msg)

# --- هندلر دکمه‌ها ---
@client.on(events.CallbackQuery)
async def callback_handler(event):
    if event.sender_id != ADMIN_ID: return
    data = event.data.decode('utf-8')
    
    if data == "toggle_active":
        SETTINGS['is_active'] = not SETTINGS['is_active']
        await event.answer("انجام شد")
        
    elif data == "clear_all":
        if links_col is not None:
            await links_col.delete_many({})
            await event.answer("دیتابیس پاکسازی شد!", alert=True)
        else:
            await event.answer("دیتابیس وصل نیست")
        
    elif data.startswith("del_"):
        uid = data.split("_")[1]
        if links_col is not None:
            await links_col.delete_one({'unique_id': uid})
            await event.edit("🗑 حذف شد.")
            
    elif data.startswith("set_time_"):
        SETTINGS['expire_time'] = int(data.split("_")[2])
        await event.answer("زمان تنظیم شد")

# --- استریم و دانلود ---
async def stream_handler(unique_id, disposition):
    if links_col is None: return "Database Error", 500

    data = await links_col.find_one({'unique_id': unique_id})
    
    if not data:
        return "❌ Link Not Found", 404
    
    if time.time() > data['expire']:
        await links_col.delete_one({'unique_id': unique_id})
        return "⏳ Link Expired", 403

    try:
        msg = await client.get_messages(data['chat_id'], ids=data['msg_id'])
        if not msg or not msg.media:
            return "❌ File removed from Telegram", 404
    except:
        return "❌ Telegram Error", 500

    file_size = data['size']
    range_header = request.headers.get('Range')
    start, end = 0, file_size - 1
    status = 200

    if range_header:
        match = re.search(r'bytes=(\d+)-(\d*)', range_header)
        if match:
            start = int(match.group(1))
            if match.group(2): end = int(match.group(2))
            status = 206

    headers = {
        'Content-Type': data['mime'],
        'Content-Disposition': f'{disposition}; filename="{data["filename"]}"',
        'Accept-Ranges': 'bytes',
        'Content-Range': f'bytes {start}-{end}/{file_size}',
        'Content-Length': str(end - start + 1)
    }

    async def file_generator():
        async for chunk in client.iter_download(msg.media, offset=start, request_size=128*1024):
            yield chunk

    return Response(file_generator(), status=status, headers=headers)

@app.route('/dl/<unique_id>')
async def dl(unique_id): return await stream_handler(unique_id, 'attachment')
@app.route('/stream/<unique_id>')
async def st(unique_id): return await stream_handler(unique_id, 'inline')
@app.route('/')
async def home(): return "Bot Active 🍃"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8000)))
