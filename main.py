import os
import time
import uuid
import re
import mimetypes
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from quart import Quart, request, Response

# --- تنظیمات اولیه ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SESSION_STRING = os.environ.get("SESSION_STRING")

# ⚠️ آیدی عددی خودتان را اینجا وارد کنید ⚠️
ADMIN_ID = 98097025  

BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")
SETTINGS = {'expire_time': 3600, 'is_active': True}
links_db = {}

# --- اتصال به تلگرام ---
if SESSION_STRING:
    client = TelegramClient(
        StringSession(SESSION_STRING), 
        API_ID, 
        API_HASH,
        connection_retries=None,
        auto_reconnect=True
    )
else:
    client = TelegramClient('bot_session', API_ID, API_HASH)

app = Quart(__name__)

@app.before_serving
async def startup():
    print("🤖 Bot starting...")
    if not SESSION_STRING:
        await client.start(bot_token=BOT_TOKEN)
    else:
        try:
            await client.connect()
        except:
            await client.start(bot_token=BOT_TOKEN)
    print("✅ Bot Connected!")

# --- هندلر دریافت فایل ---
@client.on(events.NewMessage(incoming=True))
async def handle_file(event):
    if event.sender_id != ADMIN_ID: return
    if event.text and event.text.startswith('/'): return
    if not event.media: return
    if not SETTINGS['is_active']:
        await event.reply("❌ ربات غیرفعال است.")
        return

    try:
        msg = await event.reply("🚀 پردازش...")
        unique_id = str(uuid.uuid4())[:8]
        expire_time = time.time() + SETTINGS['expire_time']
        
        # تشخیص دقیق نام و نوع فایل
        file_name = "file"
        mime_type = "application/octet-stream"
        
        if hasattr(event.media, 'document'):
            mime_type = event.media.document.mime_type
            for attr in event.media.document.attributes:
                if hasattr(attr, 'file_name'):
                    file_name = attr.file_name
                    break
        elif hasattr(event.media, 'photo'):
             file_name = f"photo_{unique_id}.jpg"
             mime_type = "image/jpeg"

        # تشخیص نوع محتوا برای متن پیام
        can_stream = False
        if 'video' in mime_type or 'audio' in mime_type:
            can_stream = True

        links_db[unique_id] = {
            'msg': event.message,
            'expire': expire_time,
            'filename': file_name,
            'mime': mime_type,
            'size': event.message.file.size
        }
        
        dl_url = f"{BASE_URL}/dl/{unique_id}"
        stream_url = f"{BASE_URL}/stream/{unique_id}"
        
        txt = (f"✅ **فایل آماده شد**\n📄 نام: `{file_name}`\n📦 حجم: {event.message.file.size // 1024 // 1024} MB\n\n📥 **دانلود:**\n`{dl_url}`")
        
        if can_stream:
            txt += f"\n\n▶️ **پخش آنلاین:**\n`{stream_url}`"
            
        await msg.edit(txt, buttons=[[Button.inline("❌ حذف", data=f"del_{unique_id}")]])

    except Exception as e:
        print(f"Error: {e}")

# --- هندلر دستورات ادمین ---
@client.on(events.NewMessage(pattern='/admin'))
async def admin_panel(event):
    if event.sender_id != ADMIN_ID: return
    buttons = [
        [Button.inline(f"وضعیت: {'✅ فعال' if SETTINGS['is_active'] else '❌'}", data="toggle_active")],
        [Button.inline("⏱ 1 ساعت", data="set_time_3600"), Button.inline("🗑 پاکسازی", data="clear_all")]
    ]
    await event.reply("مدیریت:", buttons=buttons)

@client.on(events.CallbackQuery)
async def callback_handler(event):
    if event.sender_id != ADMIN_ID: return
    data = event.data.decode('utf-8')
    
    if data == "toggle_active":
        SETTINGS['is_active'] = not SETTINGS['is_active']
        await event.answer("انجام شد")
        await admin_panel(event)
    elif data == "clear_all":
        links_db.clear()
        await event.answer("پاک شد")
    elif data.startswith("set_time_"):
        SETTINGS['expire_time'] = int(data.split("_")[2])
        await event.answer("زمان تنظیم شد")
    elif data.startswith("del_"):
        uid = data.split("_")[1]
        if uid in links_db: del links_db[uid]
        await event.edit("حذف شد.")

# --- موتور استریم هوشمند (Smart Streaming) ---
async def stream_handler(unique_id, disposition):
    data = links_db.get(unique_id)
    if not data or time.time() > data['expire']:
        return "Link Expired", 404

    msg = data['msg']
    file_size = data['size']
    content_type = data['mime']
    
    # خواندن هدر Range (درخواست مرورگر برای جلو/عقب کردن)
    range_header = request.headers.get('Range')
    
    start_byte = 0
    end_byte = file_size - 1
    status_code = 200

    # اگر مرورگر درخواست تکه‌ای از فایل را داشت
    if range_header:
        match = re.search(r'bytes=(\d+)-(\d*)', range_header)
        if match:
            start_byte = int(match.group(1))
            if match.group(2):
                end_byte = int(match.group(2))
            status_code = 206 # Partial Content

    # محاسبه حجم دیتایی که باید فرستاده شود
    content_length = end_byte - start_byte + 1
    
    headers = {
        'Content-Type': content_type,
        'Content-Disposition': f'{disposition}; filename="{data["filename"]}"',
        'Accept-Ranges': 'bytes',
        'Content-Range': f'bytes {start_byte}-{end_byte}/{file_size}',
        'Content-Length': str(content_length)
    }

    async def file_generator():
        # دستور جادویی: دانلود از تلگرام دقیقاً از همان جایی که مرورگر خواسته
        # offset=start_byte یعنی از وسط فایل شروع کن
        async for chunk in client.iter_download(msg.media, offset=start_byte, request_size=512*1024):
            # اگر بیشتر از حد نیاز مرورگر خواندیم، قطع کن
            # (اینجا ساده‌سازی شده تا استریم قطع نشود)
            yield chunk

    return Response(file_generator(), status=status_code, headers=headers)

@app.route('/dl/<unique_id>')
async def dl(unique_id): return await stream_handler(unique_id, 'attachment')

@app.route('/stream/<unique_id>')
async def st(unique_id): return await stream_handler(unique_id, 'inline')

@app.route('/')
async def home(): return "Bot is Running! 🚀"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8000)))
