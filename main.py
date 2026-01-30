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

# --- 🧠 دریافت فرمت‌ها (با اولویت SnapVideo) ---

async def get_formats(target_url):
    formats_list = []
    
    async with aiohttp.ClientSession() as session:
        # 1. تلاش با Snap Video (کیفیت‌های بهتر)
        try:
            url = "https://snap-video3.p.rapidapi.com/download"
            payload = {"url": target_url}
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "x-rapidapi-host": "snap-video3.p.rapidapi.com",
                "x-rapidapi-key": RAPID_API_KEY
            }
            # این API گاهی نیاز به POST Form Data دارد
            async with session.post(url, data=payload, headers=headers, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # بررسی ساختار پاسخ
                    if isinstance(data, list):
                        for item in data:
                            if 'video' in item.get('quality', '').lower() or 'mp4' in item.get('format', ''):
                                formats_list.append({
                                    "quality": item.get('quality', 'Video'),
                                    "size": format_size(item.get('size')), 
                                    "url": item.get('url'),
                                    "source": "Snap"
                                })
                    elif isinstance(data, dict):
                         # گاهی ساختار متفاوت است
                         if 'link' in data:
                             formats_list.append({"quality": "Best", "size": "?", "url": data['link'], "source": "Snap"})
        except Exception as e:
            print(f"SnapAPI Error: {e}")

        # 2. اگر اولی خالی بود، تلاش با Youtube Quick DL
        if not formats_list:
            try:
                url = "https://youtube-quick-video-downloader.p.rapidapi.com/api/youtube/links"
                headers = {
                    "Content-Type": "application/json",
                    "x-rapidapi-host": "youtube-quick-video-downloader.p.rapidapi.com",
                    "x-rapidapi-key": RAPID_API_KEY
                }
                async with session.post(url, json={"url": target_url}, headers=headers, timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, list): # این API لیست برمیگرداند
                            for item in data:
                                if item.get('extension') == 'mp4':
                                    label = item.get('quality', {}).get('qualityLabel') if isinstance(item.get('quality'), dict) else item.get('quality')
                                    formats_list.append({
                                        "quality": label or "MP4",
                                        "size": format_size(item.get('contentLength')),
                                        "url": item.get('url'),
                                        "source": "QuickDL"
                                    })
            except Exception as e:
                print(f"QuickDL Error: {e}")

    # حذف تکراری‌ها و مرتب‌سازی
    unique_formats = []
    seen_urls = set()
    for f in formats_list:
        if f['url'] not in seen_urls:
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
    await event.reply("👋 **ربات آماده است!**\nلینک بفرستید.", buttons=buttons)

# --- 🎥 دریافت لینک و نمایش منو ---
@client.on(events.NewMessage(pattern=r'(?s).*https?://.*'))
async def url_handler(event):
    if event.sender_id != ADMIN_ID or not SETTINGS['is_active']: return
    if event.media and not isinstance(event.media, MessageMediaWebPage): return

    found_links = re.findall(r'https?://[^\s]+', event.text)
    if not found_links: return
    target_url = found_links[0]

    valid_domains = ['youtube', 'youtu.be', 'instagram', 'tiktok']
    if not any(d in target_url for d in valid_domains): return

    msg = await event.reply(f"🔍 **در حال آنالیز لینک...**\n`{target_url}`")
    
    formats = await get_formats(target_url)
    
    if not formats:
        await msg.edit("❌ کیفیت مناسبی یافت نشد (شاید محدودیت API).")
        return

    # ذخیره در حافظه موقت
    request_id = str(uuid.uuid4())[:8]
    PENDING_QUALITY_SELECTION[request_id] = formats
    
    buttons = []
    # فقط 4 کیفیت اول را نمایش بده (برای شلوغ نشدن)
    for index, fmt in enumerate(formats[:5]):
        text = f"🎬 {fmt['quality']} | {fmt['size']}"
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
            
            await event.edit(f"📥 **در حال دانلود {selected['quality']}...**\nسایز: {selected['size']}")
            del PENDING_QUALITY_SELECTION[req_id]
            
            # --- 🚀 بخش مهم: دانلود با جعل هویت مرورگر ---
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.youtube.com/"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(download_url, headers=headers, allow_redirects=True, timeout=60) as resp:
                    
                    if resp.status == 200:
                        content_type = resp.headers.get('Content-Type', '').lower()
                        
                        # بررسی اینکه آیا فایل واقعا ویدیو است
                        if 'video' in content_type or 'application/octet-stream' in content_type:
                            file_path = f"downloads/{uuid.uuid4()}.mp4"
                            with open(file_path, 'wb') as f:
                                f.write(await resp.read())
                            
                            await event.edit("📤 آپلود به تلگرام...")
                            uploaded = await client.send_file(
                                ADMIN_ID, 
                                file_path, 
                                caption=f"✅ {selected['quality']}", 
                                supports_streaming=True
                            )
                            if os.path.exists(file_path): os.remove(file_path)
                            await generate_link_for_message(uploaded, event.message)
                        else:
                            # اگر باز هم متن داد، متن ارور را بخوانیم
                            text_error = await resp.text()
                            print(f"DL Error Body: {text_error}")
                            await event.edit(f"❌ لینک مستقیم نیست.\nType: {content_type}\nمحتوا: {text_error[:100]}")
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
