import os
import time
import uuid
import re
import asyncio
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from quart import Quart, request, Response

# --- تنظیمات ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SESSION_STRING = os.environ.get("SESSION_STRING")

# ⚠️⚠️⚠️ آیدی عددی خودتان را اینجا جایگزین کنید ⚠️⚠️⚠️
# اگر این عدد با آیدی تلگرام شما یکی نباشد، ربات جواب نمیدهد
ADMIN_ID = 98097025  

BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")
SETTINGS = {'expire_time': 3600, 'is_active': True}
links_db = {}

# --- اتصال ---
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
        try:
            await client.connect()
        except:
            await client.start(bot_token=BOT_TOKEN)
    print(f"✅ Bot Connected! Listening for Admin ID: {ADMIN_ID}")

# --- عیب‌یاب (Logger) ---
# این بخش هر پیامی بیاید را در لاگ مینویسد تا بفهمیم مشکل کجاست
@client.on(events.NewMessage(incoming=True))
async def logger(event):
    if event.sender_id == ADMIN_ID:
        print(f"📩 پیام از ادمین دریافت شد: {event.text or 'File'}")
    else:
        print(f"⚠️ پیام از غریبه (ID: {event.sender_id}) نادیده گرفته شد.")

# --- دستور استارت ---
@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if event.sender_id == ADMIN_ID:
        await event.reply("👋 سلام! ربات آماده است. فایل بفرست.")
    else:
        # اگر آیدی اشتباه باشد این پیام می‌آید
        await event.reply(f"⛔️ شما ادمین نیستید.\nآیدی شما: `{event.sender_id}`\nآیدی تنظیم شده در ربات: `{ADMIN_ID}`")

# --- دریافت فایل ---
@client.on(events.NewMessage(incoming=True))
async def handle_file(event):
    if event.sender_id != ADMIN_ID: return
    if event.text and event.text.startswith('/'): return
    if not event.media: return

    try:
        msg = await event.reply("🔄 در حال پردازش...")
        unique_id = str(uuid.uuid4())[:8]
        expire_time = time.time() + SETTINGS['expire_time']
        
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
        
        txt = (f"✅ **لینک آماده شد!**\n📄 `{file_name}`\n\n📥 **دانلود:**\n`{dl_url}`")
        if can_stream:
            txt += f"\n\n▶️ **پخش آنلاین:**\n`{stream_url}`"
            
        await msg.edit(txt, buttons=[[Button.inline("❌ حذف", data=f"del_{unique_id}")]])
        print(f"✅ Link created for {unique_id}")

    except Exception as e:
        print(f"❌ Error: {e}")

# --- هندلر دکمه‌ها ---
@client.on(events.CallbackQuery)
async def callback_handler(event):
    if event.sender_id != ADMIN_ID: return
    data = event.data.decode('utf-8')
    if data.startswith("del_"):
        uid = data.split("_")[1]
        if uid in links_db: del links_db[uid]
        await event.edit("🗑 حذف شد.")

# --- استریم ---
async def stream_handler(unique_id, disposition):
    data = links_db.get(unique_id)
    if not data or time.time() > data['expire']: return "Link Expired", 404

    msg = data['msg']
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
        # کاهش حجم بافر برای جلوگیری از قفل شدن ربات هنگام استریم
        async for chunk in client.iter_download(msg.media, offset=start, request_size=128*1024):
            yield chunk

    return Response(file_generator(), status=status, headers=headers)

@app.route('/dl/<unique_id>')
async def dl(unique_id): return await stream_handler(unique_id, 'attachment')
@app.route('/stream/<unique_id>')
async def st(unique_id): return await stream_handler(unique_id, 'inline')
@app.route('/')
async def home(): return "Bot is Alive!"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8000)))
