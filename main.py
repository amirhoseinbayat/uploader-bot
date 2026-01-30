import os
import time
import uuid
import re
import asyncio
import aiohttp
import certifi
import glob
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

# 🔑 کلید اختصاصی شما (از RapidAPI)
# اگر در Render متغیر RAPID_API_KEY را نسازید، از این کلید پیش‌فرض استفاده می‌کند
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
    await event.reply("👋 **ربات (نسخه RapidAPI) آماده است!**\nلینک بفرستید.", buttons=buttons)

# --- 🎥 دانلودر RapidAPI (YT API) ---
@client.on(events.NewMessage(pattern=r'(?s).*https?://.*'))
async def url_handler(event):
    if event.sender_id != ADMIN_ID or not SETTINGS['is_active']: return
    if event.media and not isinstance(event.media, MessageMediaWebPage): return

    # استخراج لینک
    found_links = re.findall(r'https?://[^\s]+', event.text)
    if not found_links: return
    target_url = found_links[0]

    valid_domains = ['youtube', 'youtu.be']
    if not any(d in target_url for d in valid_domains): return

    msg = await event.reply(f"🚀 **دریافت از RapidAPI (YT API)...**\n`{target_url}`")
    
    download_url = None
    
    # 1. استخراج ID ویدیو از لینک
    video_id = None
    if "youtu.be" in target_url:
        video_id = target_url.split("/")[-1].split("?")[0]
    elif "v=" in target_url:
        video_id = target_url.split("v=")[1].split("&")[0]
    elif "shorts" in target_url:
        video_id = target_url.split("shorts/")[1].split("?")[0]
        
    if not video_id:
        await msg.edit("❌ نتوانستم ID ویدیو را پیدا کنم.")
        return

    # 2. تنظیمات درخواست API (طبق کدی که فرستادید)
    api_url = "https://yt-api.p.rapidapi.com/dl"
    querystring = {"id": video_id}
    
    headers = {
        "x-rapidapi-key": RAPID_API_KEY,
        "x-rapidapi-host": "yt-api.p.rapidapi.com"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers=headers, params=querystring) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    # 3. پیدا کردن بهترین لینک دانلود از پاسخ JSON
                    # ساختار معمول این API: لیستی از فرمت‌ها برمی‌گرداند
                    # ما دنبال اولین لینکی هستیم که ویدیو باشد
                    
                    # تلاش اول: جستجو در دیکشنری اصلی
                    if 'link' in data:
                         download_url = data['link']
                    elif 'url' in data:
                         download_url = data['url']
                    # تلاش دوم: جستجو در لیست فرمت‌ها (formats/adaptiveFormats)
                    elif 'formats' in data:
                        for fmt in data['formats']:
                            # اولویت با کیفیت 720 یا mp4 دارای صدا
                            if fmt.get('url'):
                                download_url = fmt['url']
                                # اگر 720 پیدا شد، همینو بردار و برو
                                if '720' in str(fmt.get('qualityLabel', '')):
                                    break
                    
                    if not download_url:
                         # چاپ ساختار برای دیباگ در لاگ Render اگر لینک پیدا نشد
                        print(f"API Response Structure: {data}")
                        
                else:
                    error_text = await resp.text()
                    print(f"API Error: {resp.status} - {error_text}")
                    await msg.edit(f"❌ خطای API: {resp.status}")
                    return

        if not download_url:
            await msg.edit("❌ لینک دانلود توسط API پیدا نشد.")
            return

        await msg.edit(f"📥 لینک استخراج شد!\nدر حال دانلود...")

        # 4. دانلود فایل نهایی
        async with aiohttp.ClientSession() as session:
            async with session.get(download_url) as resp:
                if resp.status == 200:
                    file_path = f"downloads/{uuid.uuid4()}.mp4"
                    with open(file_path, 'wb') as f:
                        f.write(await resp.read())
                    
                    await msg.edit("📤 آپلود به تلگرام...")
                    uploaded = await client.send_file(
                        ADMIN_ID, 
                        file_path, 
                        caption=f"🎥 لینک اصلی: {target_url}\n✨ سرویس: YT API", 
                        supports_streaming=True
                    )
                    
                    if os.path.exists(file_path): os.remove(file_path)
                    await generate_link_for_message(uploaded, msg)
                else:
                    await msg.edit("❌ لینک مستقیم شد ولی فایل دانلود نشد (شاید لینک منقضی شده).")

    except Exception as e:
        await msg.edit(f"❌ خطا: {str(e)}")
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
