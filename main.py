import os
import time
import uuid
import re
import asyncio
import aiohttp
import certifi
import glob
import json
from urllib.parse import quote
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

# کلید RapidAPI شما
RAPID_API_KEY = os.environ.get("RAPID_API_KEY", "6ae492347amsh8ad1f4f1ac7ff53p172e9djsn08773036943b")

ADMIN_ID = 98097025

BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")
SETTINGS = {'expire_time': 3600, 'is_active': True}

# حافظه موقت انتخاب کیفیت
PENDING_QUALITY_SELECTION = {}

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

# --- 🛠 توابع کمکی ---

def format_size(bytes_size):
    if not bytes_size: return "نامشخص"
    try:
        mb = int(bytes_size) / (1024 * 1024)
        return f"{mb:.1f}MB"
    except: return "نامشخص"

def extract_video_id(url):
    video_id = None
    if "youtu.be" in url:
        video_id = url.split("/")[-1].split("?")[0]
    elif "v=" in url:
        video_id = url.split("v=")[1].split("&")[0]
    elif "shorts" in url:
        video_id = url.split("shorts/")[1].split("?")[0]
    return video_id

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

# --- 🧠 موتورهای جدید جستجوی فرمت (Multi-Engine) ---

async def get_formats(target_url):
    formats_list = []
    video_id = extract_video_id(target_url)
    
    async with aiohttp.ClientSession() as session:
        
        # 1️⃣ Engine 1: Youtube Video Stream Download (Snippet 4)
        if video_id:
            try:
                print(f"🔄 Trying Engine 1 (Stream DL) for ID: {video_id}...")
                url = f"https://youtube-video-stream-download.p.rapidapi.com/api/v1/Youtube/getAllDetails/{video_id}"
                headers = {
                    "x-rapidapi-key": RAPID_API_KEY,
                    "x-rapidapi-host": "youtube-video-stream-download.p.rapidapi.com"
                }
                async with session.get(url, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # جستجو در ساختار پاسخ (معمولاً formats یا streamingData)
                        streams = data.get('formats', []) + data.get('adaptiveFormats', [])
                        if not streams and 'streamingData' in data:
                             streams = data['streamingData'].get('formats', [])
                        
                        for fmt in streams:
                            # فیلتر کردن ویدیوهای mp4 صدادار
                            if 'mp4' in fmt.get('mimeType', '') and fmt.get('audioQuality'):
                                formats_list.append({
                                    "quality": fmt.get('qualityLabel', 'Unknown'),
                                    "size": format_size(fmt.get('contentLength')),
                                    "url": fmt.get('url'),
                                    "source": "StreamDL"
                                })
            except Exception as e:
                print(f"⚠️ Engine 1 Failed: {e}")

        # 2️⃣ Engine 2: Youtube Quick Video Downloader (Snippet 2)
        if not formats_list:
            try:
                print("🔄 Trying Engine 2 (Quick DL)...")
                url = "https://youtube-quick-video-downloader.p.rapidapi.com/api/youtube/links"
                headers = {
                    "Content-Type": "application/json",
                    "x-rapidapi-host": "youtube-quick-video-downloader.p.rapidapi.com",
                    "x-rapidapi-key": RAPID_API_KEY
                }
                async with session.post(url, json={"url": target_url}, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # این API گاهی لیست برمیگرداند
                        items = data if isinstance(data, list) else data.get('all_formats', [])
                        for item in items:
                            if item.get('extension') == 'mp4' or 'mp4' in item.get('format', ''):
                                formats_list.append({
                                    "quality": item.get('quality', 'Video'),
                                    "size": format_size(item.get('contentLength')),
                                    "url": item.get('url'),
                                    "source": "QuickDL"
                                })
            except Exception as e:
                print(f"⚠️ Engine 2 Failed: {e}")

        # 3️⃣ Engine 3: Youtube Video MP3 Downloader (Snippet 5)
        if not formats_list:
            try:
                print("🔄 Trying Engine 3 (MP3/Video DL)...")
                encoded_url = quote(target_url)
                url = f"https://youtube-video-mp3-downloader-api.p.rapidapi.com/download?url={encoded_url}"
                headers = {
                    "x-rapidapi-key": RAPID_API_KEY,
                    "x-rapidapi-host": "youtube-video-mp3-downloader-api.p.rapidapi.com"
                }
                async with session.get(url, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if 'url' in data:
                             formats_list.append({
                                 "quality": data.get('quality', 'HD'),
                                 "size": "Unknown",
                                 "url": data['url'],
                                 "source": "MP3DL"
                             })
                        elif 'link' in data:
                             formats_list.append({
                                 "quality": "HD", "size": "?", "url": data['link'], "source": "MP3DL"
                             })
            except Exception as e:
                print(f"⚠️ Engine 3 Failed: {e}")

        # 4️⃣ Engine 4: All Video Downloader 3 (Snippet 3)
        if not formats_list:
            try:
                print("🔄 Trying Engine 4 (All Video)...")
                url = "https://all-video-downloader3.p.rapidapi.com/all"
                payload = {"url": target_url}
                headers = {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "x-rapidapi-host": "all-video-downloader3.p.rapidapi.com",
                    "x-rapidapi-key": RAPID_API_KEY
                }
                async with session.post(url, data=payload, headers=headers, timeout=12) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # جستجو برای لینک در کلیدهای مختلف
                        found_link = data.get('url') or data.get('link') or data.get('download_link')
                        if found_link:
                            formats_list.append({
                                "quality": "Best",
                                "size": "Unknown",
                                "url": found_link,
                                "source": "AllDL"
                            })
            except Exception as e:
                print(f"⚠️ Engine 4 Failed: {e}")

    # حذف تکراری‌ها و مرتب‌سازی
    unique_formats = []
    seen_urls = set()
    for f in formats_list:
        if f['url'] and f['url'] not in seen_urls:
            seen_urls.add(f['url'])
            unique_formats.append(f)
            
    return unique_formats

# --- 👋 استارت ---
@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if event.sender_id != ADMIN_ID: return
    buttons = [
        [Button.inline(f"وضعیت: {'✅ فعال' if SETTINGS['is_active'] else '❌'}", data="toggle_active")],
        [Button.inline("🗑 پاکسازی DB", data="clear_all")]
    ]
    await event.reply("👋 **ربات آماده است!**\nلینک بفرستید (پشتیبانی از 4 موتور RapidAPI).", buttons=buttons)

# --- 🎥 دریافت لینک و نمایش منو ---
@client.on(events.NewMessage(pattern=r'(?s).*https?://.*'))
async def url_handler(event):
    if event.sender_id != ADMIN_ID or not SETTINGS['is_active']: return
    if event.media and not isinstance(event.media, MessageMediaWebPage): return

    found_links = re.findall(r'https?://[^\s]+', event.text)
    if not found_links: return
    target_url = found_links[0]

    valid_domains = ['youtube', 'youtu.be', 'instagram', 'tiktok', 'soundcloud']
    if not any(d in target_url for d in valid_domains): return

    msg = await event.reply(f"🔍 **در حال استخراج کیفیت‌ها (موتور ۴ مرحله‌ای)...**\n`{target_url}`")
    
    formats = await get_formats(target_url)
    
    if not formats:
        await msg.edit("❌ تمام ۴ موتور جستجو شکست خوردند. لینک را بررسی کنید.")
        return

    request_id = str(uuid.uuid4())[:8]
    PENDING_QUALITY_SELECTION[request_id] = formats
    
    buttons = []
    # نمایش حداکثر 6 کیفیت
    for index, fmt in enumerate(formats[:6]):
        text = f"🎬 {fmt['quality']} | {fmt['size']} ({fmt['source']})"
        buttons.append([Button.inline(text, data=f"dl_{request_id}_{index}")])
    
    buttons.append([Button.inline("❌ لغو", data=f"cancel_{request_id}")])

    await msg.edit("🎞 **کیفیت مورد نظر را انتخاب کنید:**", buttons=buttons)

# --- 🔘 دانلود نهایی (با هدر مرورگر) ---
@client.on(events.CallbackQuery)
async def callback_handler(event):
    if event.sender_id != ADMIN_ID: return
    data = event.data.decode('utf-8')
    
    if data.startswith("dl_"):
        try:
            _, req_id, idx = data.split("_")
            idx = int(idx)
            
            if req_id not in PENDING_QUALITY_SELECTION:
                await event.answer("⚠️ منقضی شده.", alert=True)
                return
                
            selected = PENDING_QUALITY_SELECTION[req_id][idx]
            download_url = selected['url']
            
            await event.edit(f"📥 **دانلود {selected['quality']} از {selected['source']}...**\nحجم: {selected['size']}")
            del PENDING_QUALITY_SELECTION[req_id]
            
            # --- 🚀 دانلود با جعل هویت (حیاتی) ---
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.youtube.com/"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(download_url, headers=headers, allow_redirects=True, timeout=120) as resp:
                    
                    if resp.status == 200:
                        content_type = resp.headers.get('Content-Type', '').lower()
                        
                        if 'video' in content_type or 'application/octet-stream' in content_type:
                            file_path = f"downloads/{uuid.uuid4()}.mp4"
                            with open(file_path, 'wb') as f:
                                f.write(await resp.read())
                            
                            await event.edit("📤 آپلود به تلگرام...")
                            uploaded = await client.send_file(
                                ADMIN_ID, 
                                file_path, 
                                caption=f"✅ {selected['quality']} ({selected['source']})", 
                                supports_streaming=True
                            )
                            if os.path.exists(file_path): os.remove(file_path)
                            await generate_link_for_message(uploaded, event.message)
                        else:
                            # اگر باز هم فایل خراب بود
                            text_error = await resp.text()
                            await event.edit(f"❌ فایل خراب است.\nType: {content_type}")
                    else:
                        await event.edit(f"❌ خطای دانلود: {resp.status}")

        except Exception as e:
            await event.edit(f"❌ خطا: {str(e)}")

    elif data.startswith("cancel_"):
        req_id = data.split("_")[1]
        if req_id in PENDING_QUALITY_SELECTION: del PENDING_QUALITY_SELECTION[req_id]
        await event.edit("❌ لغو شد.")

    # --- بقیه دکمه‌ها ---
    elif data == "toggle_active":
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

# --- استریم ---
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
