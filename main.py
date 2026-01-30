import os
import time
import uuid
import re
import asyncio
import glob
import certifi
import aiohttp
import random
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

# آیدی ادمین (ست شده برای شما)
ADMIN_ID = 98097025  

BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")
SETTINGS = {'expire_time': 3600, 'is_active': True}

# --- لیست طلایی سرورهای Cobalt (آپدیت 2025) ---
# ترکیبی از سرورهای اصلی و کامیونیتی برای تضمین دانلود
COBALT_INSTANCES = [
    "https://api.cobalt.tools",          # سرور اصلی (گاهی شلوغ)
    "https://cobalt.kwiatekmiki.pl",     # بسیار پایدار
    "https://cobalt.arms.da.ru",         # سرور روسیه (عالی برای دور زدن تحریم)
    "https://api.oxno.de",               # سرور آلمان
    "https://cobalt.154.gq",             # سرور عمومی قوی
    "https://cobalt.xy24.eu.org",        # سرور اروپا
    "https://cobalt.slpy.one",           # سرور جایگزین
    "https://cobalt.jimmyjo.eu",         # سرور جایگزین 2
    "https://cobalt.nao.lgbt",           # سرور آمریکا
    "https://cobalt.furtidev.me",        # سرور آسیا
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
    await event.reply("👋 **ربات آماده است!**\nلینک یوتیوب/اینستاگرام یا فایل بفرستید.", buttons=buttons)

# --- 🎥 دانلودر هوشمند (Mega Server List) ---
@client.on(events.NewMessage(pattern=r'(?s).*https?://.*'))
async def url_handler(event):
    if event.sender_id != ADMIN_ID or not SETTINGS['is_active']: return
    if event.media and not isinstance(event.media, MessageMediaWebPage): return

    # استخراج لینک
    found_links = re.findall(r'https?://[^\s]+', event.text)
    if not found_links: return
    target_url = found_links[0]

    valid_domains = ['youtube', 'youtu.be', 'instagram', 'tiktok', 'twitter', 'x.com', 'soundcloud', 'twitch']
    if not any(d in target_url for d in valid_domains): return

    msg = await event.reply(f"🚀 **درحال جستجوی سرور خلوت...**\n`{target_url}`")
    
    download_url = None
    working_server = ""
    
    # شافل کردن لیست سرورها برای توزیع بار (شانسی انتخاب میکنه که همزمان روی یک سرور فشار نیاد)
    server_list = COBALT_INSTANCES.copy()
    random.shuffle(server_list)

    async with aiohttp.ClientSession() as session:
        for api_base in server_list:
            try:
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
                payload = {
                    "url": target_url,
                    "vQuality": "720",
                    "filenamePattern": "basic",
                    "isAudioOnly": False
                }
                
                # تایم‌اوت کوتاه (۵ ثانیه) برای اینکه اگر سروری کند بود سریع ردش کنه
                async with session.post(f"{api_base}/api/json", json=payload, headers=headers, timeout=6) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        status = data.get('status')
                        
                        if status in ['stream', 'redirect']:
                            download_url = data.get('url')
                        elif status == 'picker':
                            download_url = data['picker'][0]['url']
                            
                        if download_url:
                            working_server = api_base
                            print(f"✅ Connected to: {api_base}")
                            break
            except Exception as e:
                print(f"⚠️ Server {api_base} failed: {e}")
                continue

    if not download_url:
        await msg.edit("❌ تمام سرورهای کمکی در حال حاضر شلوغ یا فیلتر هستند.\nلطفاً ۵ دقیقه دیگر تلاش کنید یا لینک دیگری بفرستید.")
        return

    await msg.edit(f"📥 سرور پیدا شد ({working_server.split('//')[1]})\nدر حال دانلود...")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(download_url) as resp:
                if resp.status == 200:
                    file_path = f"downloads/{uuid.uuid4()}.mp4"
                    with open(file_path, 'wb') as f:
                        f.write(await resp.read())
                    
                    await msg.edit("📤 در حال آپلود به تلگرام...")
                    uploaded = await client.send_file(
                        ADMIN_ID, 
                        file_path, 
                        caption=f"🎥 لینک اصلی: {target_url}\n⚡️ سرور: {working_server}", 
                        supports_streaming=True
                    )
                    
                    if os.path.exists(file_path): os.remove(file_path)
                    await generate_link_for_message(uploaded, msg)
                else:
                    await msg.edit("❌ لینک دانلود ساخته شد اما دانلود فایل شکست خورد.")
    except Exception as e:
        await msg.edit(f"❌ خطای نهایی: {str(e)}")
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

# --- 🔘 دکمه‌ها و استریم ---
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
