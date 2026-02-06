import os
import time
import uuid
import re
import asyncio
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaWebPage
from quart import Quart, request, Response
from motor.motor_asyncio import AsyncIOMotorClient
import hypercorn.asyncio
from hypercorn.config import Config

# --- ⚙️ تنظیمات ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SESSION_STRING = os.environ.get("SESSION_STRING")
MONGO_URL = os.environ.get("MONGO_URL")
ADMIN_ID = 98097025
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")

# حافظه موقت برای نگه داشتن فایل تا زمان انتخاب تایمر
# ساختار: {request_id: {msg_object, reply_msg_object}}
PENDING_FILES = {}

# --- 🍃 اتصال دیتابیس ---
mongo_client = None
links_col = None

if MONGO_URL:
    try:
        mongo_client = AsyncIOMotorClient(MONGO_URL, tls=True, tlsAllowInvalidCertificates=True)
        db = mongo_client['uploader_bot']
        links_col = db['links']
    except Exception as e:
        print(f"❌ DB Error: {e}")

# --- 🤖 اتصال تلگرام ---
if SESSION_STRING:
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
else:
    client = TelegramClient('bot_session', API_ID, API_HASH)

app = Quart(__name__)

# --- شروع به کار ---
@app.before_serving
async def startup():
    print("🤖 Bot Starting...")
    if not client.is_connected():
        if not SESSION_STRING: await client.start(bot_token=BOT_TOKEN)
        else:
            try: await client.connect()
            except: await client.start(bot_token=BOT_TOKEN)
    
    if mongo_client:
        try:
            await mongo_client.admin.command('ping')
            print("✅ MongoDB Connected!")
        except: print("⚠️ MongoDB Failed")

# --- 💾 تابع نهایی ذخیره در دیتابیس (بعد از انتخاب زمان) ---
async def save_file_to_db(req_id, minutes):
    if req_id not in PENDING_FILES: return
    
    user_msg = PENDING_FILES[req_id]['msg']
    bot_reply = PENDING_FILES[req_id]['reply']
    
    # حذف از حافظه موقت
    del PENDING_FILES[req_id]

    if links_col is None:
        await bot_reply.edit("❌ دیتابیس قطع است.")
        return

    try:
        unique_id = str(uuid.uuid4())[:8]
        # محاسبه زمان انقضا بر اساس انتخاب کاربر
        expire_time = time.time() + (minutes * 60)
        
        file_name = "file"
        mime_type = "application/octet-stream"
        file_size = 0
        
        if hasattr(user_msg, 'file') and user_msg.file:
            if user_msg.file.name: file_name = user_msg.file.name
            else:
                ext = user_msg.file.ext or ""
                file_name = f"downloaded_file{ext}"
            mime_type = user_msg.file.mime_type
            file_size = user_msg.file.size
        else: return

        can_stream = 'video' in mime_type or 'audio' in mime_type

        link_data = {
            'unique_id': unique_id,
            'chat_id': user_msg.chat_id,
            'msg_id': user_msg.id,
            'expire': expire_time,
            'filename': file_name,
            'mime': mime_type,
            'size': file_size,
            'views': 0
        }
        await links_col.insert_one(link_data)
        
        dl_url = f"{BASE_URL}/dl/{unique_id}"
        stream_url = f"{BASE_URL}/stream/{unique_id}"
        
        # فرمت زمان برای نمایش
        hours = minutes // 60
        mins = minutes % 60
        time_str = f"{hours} ساعت" if hours > 0 else f"{mins} دقیقه"
        if mins > 0 and hours > 0: time_str += f" و {mins} دقیقه"

        txt = (f"✅ **لینک مستقیم ساخته شد!**\n"
               f"⏳ اعتبار: {time_str}\n"
               f"📄 `{file_name}`\n"
               f"📦 حجم: {file_size // 1024 // 1024} MB\n\n"
               f"📥 **دانلود:**\n`{dl_url}`")
        
        if can_stream: txt += f"\n\n▶️ **پخش آنلاین:**\n`{stream_url}`"
            
        await bot_reply.edit(txt, buttons=[[Button.inline("❌ حذف لینک", data=f"del_{unique_id}")]])
        
    except Exception as e:
        await bot_reply.edit(f"❌ خطا: {e}")

# --- 👋 استارت ---
@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if event.sender_id != ADMIN_ID: return
    buttons = [
        [Button.inline("🗑 پاکسازی کل دیتابیس", data="clear_all")]
    ]
    await event.reply("👋 **ربات آپلودر پیشرفته آماده است!**\nفایل بفرستید -> زمان را انتخاب کنید -> لینک بگیرید.", buttons=buttons)

# --- 📁 هندلر دریافت فایل (نمایش منوی زمان) ---
@client.on(events.NewMessage(incoming=True))
async def handle_file(event):
    if event.sender_id != ADMIN_ID: return
    if event.text and event.text.startswith('/'): return
    if isinstance(event.media, MessageMediaWebPage): return
    if not event.media: return

    msg = await event.reply("⏳ در حال بررسی فایل...")
    
    # ساخت شناسه موقت
    req_id = str(uuid.uuid4())[:8]
    PENDING_FILES[req_id] = {'msg': event.message, 'reply': msg}

    # دکمه‌های انتخاب زمان
    buttons = [
        [Button.inline("⏱ 30 دقیقه", data=f"time_{req_id}_30"), Button.inline("⏱ 1 ساعت", data=f"time_{req_id}_60")],
        [Button.inline("⏱ 3 ساعت", data=f"time_{req_id}_180"), Button.inline("⏱ 12 ساعت", data=f"time_{req_id}_720")],
        [Button.inline("⏱ 24 ساعت", data=f"time_{req_id}_1440"), Button.inline("❌ لغو", data=f"cancel_{req_id}")]
    ]

    await msg.edit("🕒 **این لینک تا چه زمانی فعال باشد؟**\nلطفاً یک گزینه را انتخاب کنید:", buttons=buttons)

# --- 🔘 دکمه‌ها ---
@client.on(events.CallbackQuery)
async def callback_handler(event):
    if event.sender_id != ADMIN_ID: return
    data = event.data.decode('utf-8')
    
    # انتخاب زمان
    if data.startswith("time_"):
        parts = data.split("_")
        req_id = parts[1]
        minutes = int(parts[2])
        
        if req_id in PENDING_FILES:
            await event.answer(f"تنظیم شد: {minutes} دقیقه")
            await save_file_to_db(req_id, minutes)
        else:
            await event.answer("⚠️ این درخواست منقضی شده است.", alert=True)
            await event.delete()

    # لغو عملیات
    elif data.startswith("cancel_"):
        req_id = data.split("_")[1]
        if req_id in PENDING_FILES: del PENDING_FILES[req_id]
        await event.delete()

    # حذف لینک تکی
    elif data.startswith("del_"):
        uid = data.split("_")[1]
        if links_col is not None:
            await links_col.delete_one({'unique_id': uid})
            await event.edit("🗑 لینک با موفقیت حذف شد.")

    # پاکسازی کل دیتابیس
    elif data == "clear_all":
        if links_col is not None:
            await links_col.delete_many({})
            await event.answer("دیتابیس کامل خالی شد!", alert=True)

# --- 🚀 هندلر استریم توربو (افزایش سرعت) ---
async def stream_handler(unique_id, disposition):
    if links_col is None: return "DB Error", 500
    
    if not client.is_connected():
        try: await client.connect()
        except: pass

    data = await links_col.find_one({'unique_id': unique_id})
    if not data: return "Link Not Found", 404
    
    if time.time() > data['expire']:
        await links_col.delete_one({'unique_id': unique_id})
        return "Link Expired", 403

    await links_col.update_one({'unique_id': unique_id}, {'$inc': {'views': 1}})

    try:
        msg = await client.get_messages(data['chat_id'], ids=data['msg_id'])
        if not msg or not msg.media: return "File Removed from Telegram", 404
    except: return "Telegram API Error", 500

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

    content_length = end - start + 1
    headers = {
        'Content-Type': data['mime'],
        'Content-Disposition': f'{disposition}; filename="{data["filename"]}"',
        'Accept-Ranges': 'bytes',
        'Content-Range': f'bytes {start}-{end}/{file_size}',
        'Content-Length': str(content_length),
    }

    async def file_generator():
        bytes_remaining = content_length
        # 🚀 افزایش حجم چانک به ۱ مگابایت برای سرعت بالا
        CHUNK_SIZE = 1024 * 1024 
        
        async for chunk in client.iter_download(msg.media, offset=start, request_size=CHUNK_SIZE):
            if bytes_remaining <= 0: break
            chunk_len = len(chunk)
            if bytes_remaining >= chunk_len:
                yield chunk
                bytes_remaining -= chunk_len
            else:
                yield chunk[:bytes_remaining]
                bytes_remaining = 0
                break

    return Response(file_generator(), status=status, headers=headers)

@app.route('/dl/<unique_id>')
async def dl(unique_id): return await stream_handler(unique_id, 'attachment')
@app.route('/stream/<unique_id>')
async def st(unique_id): return await stream_handler(unique_id, 'inline')
@app.route('/')
async def home(): return "Turbo Stream Bot Active 🚀"

if __name__ == '__main__':
    config = Config()
    config.bind = [f"0.0.0.0:{int(os.environ.get('PORT', 8000))}"]
    asyncio.run(hypercorn.asyncio.serve(app, config))
