import os
import time
import uuid
import re
import asyncio
import glob
import yt_dlp
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaWebPage # ایمپورت مهم برای رفع باگ
from quart import Quart, request, Response

# --- تنظیمات ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SESSION_STRING = os.environ.get("SESSION_STRING")
ADMIN_ID = 98097025  # ⚠️ آیدی عددی خودتان را چک کنید

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

# --- تابع کمکی: ساخت لینک برای هر پیامی ---
async def generate_link_for_message(message, reply_to_msg):
    try:
        unique_id = str(uuid.uuid4())[:8]
        expire_time = time.time() + SETTINGS['expire_time']
        
        file_name = "file"
        mime_type = "application/octet-stream"
        
        # استخراج اطلاعات فایل
        if hasattr(message, 'file'):
            if message.file.name:
                file_name = message.file.name
            else:
                ext = message.file.ext or ""
                file_name = f"downloaded_file{ext}"
            mime_type = message.file.mime_type
            file_size = message.file.size
        else:
            return # اگر فایل نبود، بیخیال شو

        can_stream = False
        if 'video' in mime_type or 'audio' in mime_type:
            can_stream = True

        links_db[unique_id] = {
            'msg': message,
            'expire': expire_time,
            'filename': file_name,
            'mime': mime_type,
            'size': file_size
        }
        
        dl_url = f"{BASE_URL}/dl/{unique_id}"
        stream_url = f"{BASE_URL}/stream/{unique_id}"
        
        txt = (f"✅ **فایل آماده شد!**\n📄 `{file_name}`\n📦 حجم: {file_size // 1024 // 1024} MB\n\n📥 **دانلود:**\n`{dl_url}`")
        if can_stream:
            txt += f"\n\n▶️ **پخش آنلاین:**\n`{stream_url}`"
            
        await reply_to_msg.edit(txt, buttons=[[Button.inline("❌ حذف", data=f"del_{unique_id}")]])
        
    except Exception as e:
        print(f"Error generating link: {e}")
        await reply_to_msg.edit(f"❌ خطا در ساخت لینک: {e}")

# --- هندلر استارت ---
@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if event.sender_id == ADMIN_ID:
        buttons = [
            [Button.inline(f"وضعیت: {'✅ فعال' if SETTINGS['is_active'] else '❌'}", data="toggle_active")],
            [Button.inline("⏱ 1 ساعت", data="set_time_3600"), Button.inline("🗑 پاکسازی", data="clear_all")]
        ]
        await event.reply("👋 **سلام قربان!**\nفایل یا لینک یوتیوب بفرستید.", buttons=buttons)

# --- هندلر دانلود یوتیوب ---
@client.on(events.NewMessage(pattern=r'https?://.*(youtube\.com|youtu\.be).*'))
async def youtube_handler(event):
    if event.sender_id != ADMIN_ID: return
    if not SETTINGS['is_active']: return

    # جلوگیری از تداخل: اگر پیام فایل دارد، بگذار هندلر فایل انجام دهد (مگر اینکه لینک در کپشن باشد)
    if event.media and not isinstance(event.media, MessageMediaWebPage):
        return

    msg = await event.reply("📥 **لینک یوتیوب یافت شد!**\n⏳ در حال دانلود روی سرور...")

    try:
        ydl_opts = {
            'format': 'best[ext=mp4]/best',
            'outtmpl': f'downloads/%(id)s.%(ext)s',
            'quiet': True, 'no_warnings': True,
            'max_filesize': 200 * 1024 * 1024 # محدودیت 200 مگابایت
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(event.text, download=True)
            file_path = ydl.prepare_filename(info)

        await msg.edit("📤 دانلود شد! در حال آپلود به تلگرام...")

        # آپلود فایل و دریافت آبجکت پیام
        uploaded_msg = await client.send_file(
            ADMIN_ID,
            file_path,
            caption=f"🎥 **{info.get('title', 'Video')}**",
            supports_streaming=True
        )
        
        # حذف فایل از سرور
        if os.path.exists(file_path):
            os.remove(file_path)

        # ساخت لینک مستقیم برای همین فایل آپلود شده
        await generate_link_for_message(uploaded_msg, msg)

    except Exception as e:
        await msg.edit(f"❌ خطا: {str(e)}")
        # پاکسازی
        files = glob.glob('downloads/*')
        for f in files: os.remove(f)

# --- هندلر فایل معمولی ---
@client.on(events.NewMessage(incoming=True))
async def handle_file(event):
    if event.sender_id != ADMIN_ID: return
    if event.text and event.text.startswith('/'): return
    
    # 🔴 فیکس مهم: اگر پیام فقط پیش‌نمایش لینک است، نادیده بگیر
    if isinstance(event.media, MessageMediaWebPage):
        return
        
    if not event.media: return

    # اگر پیام از طرف هندلر یوتیوب آمده (یعنی کپشن دارد و مال خودمان است)، نادیده بگیر تا دوبار لینک ندهد
    # (البته هندلر یوتیوب خودش لینک میسازد، پس اینجا مشکلی نیست)

    msg = await event.reply("🔄 ...")
    await generate_link_for_message(event.message, msg)

# --- بقیه توابع (دکمه‌ها و استریم) ---
@client.on(events.CallbackQuery)
async def callback_handler(event):
    if event.sender_id != ADMIN_ID: return
    data = event.data.decode('utf-8')
    if data == "toggle_active":
        SETTINGS['is_active'] = not SETTINGS['is_active']
        await event.answer("انجام شد")
    elif data == "clear_all":
        links_db.clear()
        await event.answer("پاک شد")
    elif data.startswith("del_"):
        uid = data.split("_")[1]
        if uid in links_db: del links_db[uid]
        await event.edit("حذف شد.")

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
    if not os.path.exists('downloads'): os.makedirs('downloads')
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8000)))
