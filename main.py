import os
import time
import uuid
import re
import asyncio
import aiohttp
import certifi
import glob
import json
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaWebPage
from quart import Quart, request, Response
from motor.motor_asyncio import AsyncIOMotorClient

# --- ⚙️ تنظیمات ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SESSION_STRING = os.environ.get("SESSION_STRING")
MONGO_URL = os.environ.get("MONGO_URL")

# 🔑 کلید RapidAPI شما
RAPID_API_KEY = os.environ.get("RAPID_API_KEY", "6ae492347amsh8ad1f4f1ac7ff53p172e9djsn08773036943b")

ADMIN_ID = 98097025

BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")
SETTINGS = {'expire_time': 3600, 'is_active': True}

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

@app.before_serving
async def startup():
    print("🤖 Bot Starting...")
    if not os.path.exists('downloads'): os.makedirs('downloads')
    if not SESSION_STRING: await client.start(bot_token=BOT_TOKEN)
    else:
        try: await client.connect()
        except: await client.start(bot_token=BOT_TOKEN)
    
    if mongo_client:
        try:
            await mongo_client.admin.command('ping')
            print("✅ MongoDB Connected!")
        except: print("⚠️ MongoDB Failed")

# --- 🔗 تابع ساخت لینک ---
async def generate_link_for_message(message, reply_to_msg):
    if links_col is None:
        await reply_to_msg.edit("❌ دیتابیس قطع است.")
        return

    try:
        unique_id = str(uuid.uuid4())[:8]
        expire_time = time.time() + SETTINGS['expire_time']
        
        file_name = "file"
        mime_type = "application/octet-stream"
        file_size = 0
        
        if hasattr(message, 'file') and message.file:
            if message.file.name: file_name = message.file.name
            else:
                ext = message.file.ext or ""
                file_name = f"downloaded_file{ext}"
            mime_type = message.file.mime_type
            file_size = message.file.size
        else: return

        can_stream = 'video' in mime_type or 'audio' in mime_type

        link_data = {
            'unique_id': unique_id,
            'chat_id': message.chat_id,
            'msg_id': message.id,
            'expire': expire_time,
            'filename': file_name,
            'mime': mime_type,
            'size': file_size,
            'views': 0
        }
        await links_col.insert_one(link_data)
        
        dl_url = f"{BASE_URL}/dl/{unique_id}"
        stream_url = f"{BASE_URL}/stream/{unique_id}"
        
        txt = (f"✅ **فایل آماده شد!**\n📄 `{file_name}`\n📦 حجم: {file_size // 1024 // 1024} MB\n\n📥 **دانلود:**\n`{dl_url}`")
        if can_stream: txt += f"\n\n▶️ **پخش آنلاین:**\n`{stream_url}`"
            
        await reply_to_msg.edit(txt, buttons=[[Button.inline("❌ حذف", data=f"del_{unique_id}")]])
        
    except Exception as e:
        await reply_to_msg.edit(f"❌ خطا: {e}")

# --- 👋 استارت ---
@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if event.sender_id != ADMIN_ID: return
    buttons = [
        [Button.inline(f"وضعیت: {'✅ فعال' if SETTINGS['is_active'] else '❌'}", data="toggle_active")],
        [Button.inline("⏱ 1 ساعت", data="set_time_3600"), Button.inline("🗑 پاکسازی DB", data="clear_all")]
    ]
    await event.reply("👋 **ربات (نسخه RapidAPI Multi-Engine) آماده است!**\nلینک بفرستید.", buttons=buttons)

# --- 🧠 توابع استخراج لینک از APIهای مختلف ---

async def try_api_1(session, target_url):
    # API 1: YouTube Quick Video Downloader
    print("🔄 Testing API 1: Quick Video Downloader...")
    url = "https://youtube-quick-video-downloader.p.rapidapi.com/api/youtube/links"
    payload = {"url": target_url}
    headers = {
        "Content-Type": "application/json",
        "x-rapidapi-host": "youtube-quick-video-downloader.p.rapidapi.com",
        "x-rapidapi-key": RAPID_API_KEY
    }
    try:
        async with session.post(url, json=payload, headers=headers, timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                # جستجو در پاسخ برای بهترین کیفیت
                if isinstance(data, list): # گاهی لیست برمیگرداند
                    for item in data:
                        if item.get('quality') == '720p' or item.get('extension') == 'mp4':
                            return item.get('url')
                elif isinstance(data, dict):
                     # ساختار احتمالی دیگر
                     if 'all_formats' in data:
                         for fmt in data['all_formats']:
                             if fmt.get('quality') == '720p':
                                 return fmt.get('url')
    except Exception as e:
        print(f"⚠️ API 1 Failed: {e}")
    return None

async def try_api_2(session, target_url):
    # API 2: Snap Video 3
    print("🔄 Testing API 2: Snap Video 3...")
    url = "https://snap-video3.p.rapidapi.com/download"
    # معمولا فرم دیتا میگیرند
    payload = {"url": target_url}
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "x-rapidapi-host": "snap-video3.p.rapidapi.com",
        "x-rapidapi-key": RAPID_API_KEY
    }
    try:
        async with session.post(url, data=payload, headers=headers, timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                # جستجوی لینک در پاسخ
                if 'link' in data: return data['link']
                if 'download_link' in data: return data['download_link']
                if 'url' in data: return data['url']
    except Exception as e:
        print(f"⚠️ API 2 Failed: {e}")
    return None

async def try_api_3(session, target_url):
    # API 3: YouTube Audio Video Download
    print("🔄 Testing API 3: Audio Video Download...")
    url = "https://youtube-audio-video-download.p.rapidapi.com/geturl"
    querystring = {"video_url": target_url}
    headers = {
        "x-rapidapi-host": "youtube-audio-video-download.p.rapidapi.com",
        "x-rapidapi-key": RAPID_API_KEY
    }
    try:
        async with session.get(url, headers=headers, params=querystring, timeout=15) as resp:
            if resp.status == 200:
                data = await resp.json()
                # این API معمولا مستقیم url میدهد یا status
                if 'url' in data: return data['url']
                if 'download_url' in data: return data['download_url']
    except Exception as e:
        print(f"⚠️ API 3 Failed: {e}")
    return None

# --- 🎥 هندلر اصلی دانلود ---
@client.on(events.NewMessage(pattern=r'(?s).*https?://.*'))
async def url_handler(event):
    if event.sender_id != ADMIN_ID or not SETTINGS['is_active']: return
    if event.media and not isinstance(event.media, MessageMediaWebPage): return

    found_links = re.findall(r'https?://[^\s]+', event.text)
    if not found_links: return
    target_url = found_links[0]

    valid_domains = ['youtube', 'youtu.be', 'instagram', 'tiktok']
    if not any(d in target_url for d in valid_domains): return

    msg = await event.reply(f"🚀 **در حال پردازش با چند موتور...**\n`{target_url}`")
    
    download_url = None
    used_api = ""

    async with aiohttp.ClientSession() as session:
        # 1️⃣ تلاش اول
        if not download_url:
            download_url = await try_api_1(session, target_url)
            if download_url: used_api = "QuickDownloader"

        # 2️⃣ تلاش دوم (اگر اولی نشد)
        if not download_url:
            download_url = await try_api_2(session, target_url)
            if download_url: used_api = "SnapVideo"

        # 3️⃣ تلاش سوم (اگر دومی نشد)
        if not download_url:
            download_url = await try_api_3(session, target_url)
            if download_url: used_api = "AudioVideoDL"

        if not download_url:
            await msg.edit("❌ تمام APIها شکست خوردند. ممکن است لینک خراب باشد یا سهمیه API تمام شده باشد.")
            return

        await msg.edit(f"📥 لینک استخراج شد! ({used_api})\nدر حال دانلود به سرور...")

        # دانلود فایل نهایی
        try:
            async with session.get(download_url) as resp:
                if resp.status == 200:
                    file_path = f"downloads/{uuid.uuid4()}.mp4"
                    with open(file_path, 'wb') as f:
                        f.write(await resp.read())
                    
                    await msg.edit("📤 در حال آپلود...")
                    uploaded = await client.send_file(
                        ADMIN_ID, 
                        file_path, 
                        caption=f"🎥 لینک اصلی: {target_url}\n✨ موتور: {used_api}", 
                        supports_streaming=True
                    )
                    
                    if os.path.exists(file_path): os.remove(file_path)
                    await generate_link_for_message(uploaded, msg)
                else:
                    await msg.edit(f"❌ لینک مستقیم شد ولی دانلود نشد (Error {resp.status})")
        except Exception as e:
             await msg.edit(f"❌ خطا در دانلود نهایی: {str(e)}")
             if os.path.exists('downloads'):
                for f in glob.glob('downloads/*'): os.remove(f)

# --- 📁 هندلر فایل ---
@client.on(events.NewMessage(incoming=True))
async def handle_file(event):
    if event.sender_id != ADMIN_ID: return
    if re.search(r'https?://', event.text): return 
    if event.text and event.text.startswith('/'): return
    if isinstance(event.media, MessageMediaWebPage): return
    if not event.media: return

    msg = await event.reply("🍃 در حال پردازش فایل...")
    await generate_link_for_message(event.message, msg)

# --- 🔘 دکمه‌ها ---
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
            await event.answer("پاک شد", alert=True)
    elif data.startswith("del_"):
        uid = data.split("_")[1]
        if links_col is not None:
            await links_col.delete_one({'unique_id': uid})
            await event.edit("حذف شد")
    elif data.startswith("set_time_"):
        SETTINGS['expire_time'] = int(data.split("_")[2])
        await event.answer("زمان تنظیم شد")

async def stream_handler(unique_id, disposition):
    if links_col is None: return "DB Error", 500
    data = await links_col.find_one({'unique_id': unique_id})
    if not data: return "Link Not Found", 404
    
    if time.time() > data['expire']:
        await links_col.delete_one({'unique_id': unique_id})
        return "Expired", 403

    await links_col.update_one({'unique_id': unique_id}, {'$inc': {'views': 1}})

    try:
        msg = await client.get_messages(data['chat_id'], ids=data['msg_id'])
        if not msg or not msg.media: return "File Removed", 404
    except: return "TG Error", 500

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
async def home(): return "Bot Ready 🚀"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8000)))
