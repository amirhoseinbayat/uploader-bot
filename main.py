import os
import time
import uuid
import re
import asyncio
import glob
import certifi
import aiohttp
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

# ⚠️ آیدی عددی ادمین
ADMIN_ID = 98097025  

BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")
SETTINGS = {'expire_time': 3600, 'is_active': True}

# --- لیست سرورهای Cobalt (آپدیت شده و قوی‌تر) ---
COBALT_INSTANCES = [
    "https://api.cobalt.tools",          # اصلی
    "https://cobalt.kwiatekmiki.pl",     # بک‌آپ ۱
    "https://cobalt.ducks.party",        # بک‌آپ ۲
    "https://cobalt.154.gq",             # بک‌آپ ۳
    "https://cobalt.xy24.eu.org",        # بک‌آپ ۴
]

# --- 🍃 اتصال به دیتابیس ---
mongo_client = None
links_col = None

if MONGO_URL:
    try:
        mongo_client = AsyncIOMotorClient(MONGO_URL, tls=True, tlsAllowInvalidCertificates=True)
        db = mongo_client['uploader_bot']
        links_col = db['links']
    except Exception as e:
        print(f"❌ DB Error: {e}")

# --- 🤖 اتصال به تلگرام ---
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
        except: print("⚠️ MongoDB Connection Failed")

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
    await event.reply("👋 **ربات آماده است!**\nلینک یا فایل بفرستید.", buttons=buttons)

# --- 🎥 دانلودر هوشمند (Cobalt) ---
# تغییر مهم: پترن Regex طوری شد که لینک وسط متن را هم پیدا کند
@client.on(events.NewMessage(pattern=r'(?s).*https?://.*'))
async def url_handler(event):
    if event.sender_id != ADMIN_ID or not SETTINGS['is_active']: return
    # اگر پیام فایل دارد ولی وب پیج نیست (یعنی فایل واقعی است)، نادیده بگیر
    if event.media and not isinstance(event.media, MessageMediaWebPage): return

    # استخراج لینک از متن پیام
    found_links = re.findall(r'https?://[^\s]+', event.text)
    if not found_links: return
    target_url = found_links[0] # اولین لینک پیدا شده

    # فقط لینک‌های معتبر (یوتیوب، اینستا، تیک‌تاک و...)
    valid_domains = ['youtube', 'youtu.be', 'instagram', 'tiktok', 'twitter', 'x.com']
    if not any(d in target_url for d in valid_domains): return

    msg = await event.reply(f"🔎 **لینک یافت شد:**\n`{target_url}`\n⏳ در حال تست سرورهای دانلود...")
    
    download_url = None
    
    async with aiohttp.ClientSession() as session:
        for api_base in COBALT_INSTANCES:
            try:
                headers = {"Accept": "application/json", "Content-Type": "application/json"}
                # تنظیمات ساده‌تر برای شانس موفقیت بیشتر
                payload = {"url": target_url} 
                
                async with session.post(f"{api_base}/api/json", json=payload, headers=headers, timeout=20) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        status = data.get('status')
                        
                        if status in ['stream', 'redirect']:
                            download_url = data.get('url')
                        elif status == 'picker':
                            download_url = data['picker'][0]['url']
                            
                        if download_url:
                            print(f"✅ Download success from: {api_base}")
                            break # موفق شدیم!
            except:
                continue # سرور خراب بود، بعدی رو تست کن

    if not download_url:
        await msg.edit("❌ تمام سرورهای کمکی شلوغ هستند. لطفاً ۱ دقیقه دیگر تلاش کنید.")
        return

    await msg.edit("📥 لینک مستقیم شد! در حال انتقال به تلگرام...")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(download_url) as resp:
                if resp.status == 200:
                    file_path = f"downloads/{uuid.uuid4()}.mp4"
                    with open(file_path, 'wb') as f:
                        f.write(await resp.read())
                    
                    await msg.edit("📤 در حال آپلود...")
                    uploaded = await client.send_file(
                        ADMIN_ID, 
                        file_path, 
                        caption=f"🎥 لینک اصلی: {target_url}", 
                        supports_streaming=True
                    )
                    
                    if os.path.exists(file_path): os.remove(file_path)
                    await generate_link_for_message(uploaded, msg)
                else:
                    await msg.edit("❌ خطا در دانلود فایل نهایی.")
    except Exception as e:
        await msg.edit(f"❌ خطا: {str(e)}")
        if os.path.exists('downloads'):
            for f in glob.glob('downloads/*'): os.remove(f)

# --- 📁 هندلر فایل (بقیه فایل‌ها) ---
@client.on(events.NewMessage(incoming=True))
async def handle_file(event):
    if event.sender_id != ADMIN_ID: return
    # اگر پیام لینک داشت، هندلر قبلی انجامش میده، پس اینجا کاری نکن
    if re.search(r'https?://', event.text): return 
    if event.text and event.text.startswith('/'): return
    if isinstance(event.media, MessageMediaWebPage): return
    if not event.media: return

    msg = await event.reply("🍃 در حال پردازش فایل...")
    await generate_link_for_message(event.message, msg)

# --- 🔘 دکمه‌ها و استریم (بدون تغییر) ---
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
