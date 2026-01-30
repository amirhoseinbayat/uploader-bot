import os
import time
import uuid
import re
import asyncio
import glob
# اضافه شدن کتابخانه yt-dlp
import yt_dlp 
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from quart import Quart, request, Response

# --- تنظیمات ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SESSION_STRING = os.environ.get("SESSION_STRING")

# ⚠️ آیدی عددی خودتان را وارد کنید
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
        try: await client.connect()
        except: await client.start(bot_token=BOT_TOKEN)
    print(f"✅ Bot Connected! Listening for Admin ID: {ADMIN_ID}")

# --- هندلر استارت ---
@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if event.sender_id == ADMIN_ID:
        buttons = [
            [Button.inline(f"وضعیت: {'✅ فعال' if SETTINGS['is_active'] else '❌'}", data="toggle_active")],
            [Button.inline("⏱ 1 ساعت", data="set_time_3600"), Button.inline("🗑 پاکسازی", data="clear_all")]
        ]
        await event.reply(
            "👋 **سلام قربان!**\n\n"
            "1️⃣ فایل بفرستید -> لینک مستقیم بگیرید.\n"
            "2️⃣ لینک یوتیوب بفرستید -> دانلود و تبدیل به لینک مستقیم.\n\n"
            "⚙️ **تنظیمات:**", 
            buttons=buttons
        )

# --- هندلر دانلود از یوتیوب (جدید) ---
@client.on(events.NewMessage(pattern=r'https?://.*(youtube\.com|youtu\.be).*'))
async def youtube_handler(event):
    if event.sender_id != ADMIN_ID: return
    if not SETTINGS['is_active']: return

    msg = await event.reply("📥 **لینک یوتیوب تشخیص داده شد!**\n⏳ در حال دانلود ویدیو روی سرور...")

    try:
        # تنظیمات دانلودر
        ydl_opts = {
            'format': 'best[ext=mp4]/best', # بهترین کیفیت MP4
            'outtmpl': f'downloads/%(id)s.%(ext)s', # مسیر ذخیره موقت
            'quiet': True,
            'no_warnings': True,
            # محدودیت حجم برای جلوگیری از هنگ کردن سرور رایگان (مثلا 100 مگ)
            'max_filesize': 100 * 1024 * 1024 
        }

        # دانلود ویدیو
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(event.text, download=True)
            file_path = ydl.prepare_filename(info)

        await msg.edit("📤 دانلود تمام شد. در حال آپلود به تلگرام...")

        # آپلود به تلگرام (به عنوان فایل)
        # نکته: وقتی فایل آپلود شود، هندلر handle_file خودکار آن را می‌گیرد و لینک می‌سازد!
        await client.send_file(
            ADMIN_ID, 
            file_path, 
            caption=f"🎥 **{info.get('title', 'YouTube Video')}**\n🔗 Source: {event.text}",
            supports_streaming=True
        )
        
        await msg.delete() # حذف پیام "در حال دانلود"
        
        # پاک کردن فایل از روی سرور برای خالی شدن فضا
        if os.path.exists(file_path):
            os.remove(file_path)

    except Exception as e:
        await msg.edit(f"❌ خطا در دانلود: {str(e)}")
        # پاکسازی در صورت خطا
        files = glob.glob('downloads/*')
        for f in files: os.remove(f)

# --- هندلر دریافت فایل و ساخت لینک ---
@client.on(events.NewMessage(incoming=True))
async def handle_file(event):
    if event.sender_id != ADMIN_ID: return
    # اگر لینک یوتیوب بود، نادیده بگیر (چون هندلر بالایی انجامش میده)
    if event.text and ("youtube.com" in event.text or "youtu.be" in event.text): return
    if event.text and event.text.startswith('/'): return
    if not event.media: return

    try:
        # ساخت لینک برای فایل (چه فایل ارسالی شما، چه فایل دانلود شده از یوتیوب)
        msg = await event.reply("🔄 ...")
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

    except Exception as e:
        print(f"Error: {e}")

# --- هندلر دکمه‌ها ---
@client.on(events.CallbackQuery)
async def callback_handler(event):
    if event.sender_id != ADMIN_ID: return
    data = event.data.decode('utf-8')
    if data == "toggle_active":
        SETTINGS['is_active'] = not SETTINGS['is_active']
        await event.answer("تغییر کرد")
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
    # ساخت پوشه دانلود برای یوتیوب
    if not os.path.exists('downloads'):
        os.makedirs('downloads')
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8000)))
