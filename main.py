import os
import time
import uuid
import asyncio
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from quart import Quart, request, Response

# --- دریافت اطلاعات ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SESSION_STRING = os.environ.get("SESSION_STRING")

# ⚠️⚠️⚠️ مهم: آیدی عددی خودتان را اینجا جایگزین کنید ⚠️⚠️⚠️
# آیدی خود را از ربات @userinfobot بگیرید و به جای عدد زیر بگذارید
ADMIN_ID = 98097025  

BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")
SETTINGS = {'expire_time': 3600, 'is_active': True}
links_db = {}

# --- اتصال کلاینت ---
# تنظیمات اتصال بهینه برای سرعت بالا
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
        except Exception as e:
            print(f"Connection Error: {e}")
            # اگر سشن ارور داد، تلاش برای لاگین با توکن
            await client.start(bot_token=BOT_TOKEN)
    print("✅ Bot Connected!")

# --- دستور استارت ---
@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    if event.sender_id == ADMIN_ID:
        await event.reply(
            "👋 **سلام ادمین عزیز!**\n\n"
            "من آماده‌ام. هر فایلی (عکس، فیلم، آهنگ) بفرستی، لینک مستقیمش رو بهت میدم.\n"
            "برای تنظیمات بزن روی: /admin"
        )
    else:
        await event.reply("❌ شما دسترسی به این ربات را ندارید.")

# --- پنل مدیریت ---
@client.on(events.NewMessage(pattern='/admin'))
async def admin_panel(event):
    if event.sender_id != ADMIN_ID:
        return
        
    buttons = [
        [Button.inline(f"وضعیت: {'✅ فعال' if SETTINGS['is_active'] else '❌ غیرفعال'}", data="toggle_active")],
        [Button.inline("⏱ ۳۰ دقیقه", data="set_time_1800"), Button.inline("⏱ ۱ ساعت", data="set_time_3600")],
        [Button.inline("⏱ ۲ ساعت", data="set_time_7200"), Button.inline("🗑 حذف همه لینک‌ها", data="clear_all")]
    ]
    await event.reply("🛠 **پنل مدیریت ربات**", buttons=buttons)

# --- دریافت فایل (اصلاح شده) ---
@client.on(events.NewMessage(incoming=True))
async def handle_file(event):
    # 👇👇👇 این خط جدید رو اضافه کن 👇👇👇
    print(f"📩 پیام جدید از طرف: {event.sender_id}") 
    
    # ... بقیه کدها ...
    if event.sender_id != ADMIN_ID:
        return

    # اگر دستور متنی است نادیده بگیر
    if event.text and event.text.startswith('/'):
        return

    if not event.media:
        return

    if not SETTINGS['is_active']:
        await event.reply("❌ ربات غیرفعال است.")
        return

    try:
        msg = await event.reply("🚀 در حال آماده‌سازی لینک...")
        
        unique_id = str(uuid.uuid4())[:8]
        expire_time = time.time() + SETTINGS['expire_time']
        
        file_name = "Unknown"
        mime_type = "application/octet-stream"
        can_stream = False
        
        # استخراج نام و نوع فایل
        if hasattr(event.media, 'document'):
            mime_type = event.media.document.mime_type
            for attr in event.media.document.attributes:
                if hasattr(attr, 'file_name'):
                    file_name = attr.file_name
                    break
            if 'video' in mime_type or 'audio' in mime_type:
                can_stream = True
        elif hasattr(event.media, 'photo'):
             file_name = f"photo_{unique_id}.jpg"
             mime_type = "image/jpeg"

        links_db[unique_id] = {
            'msg': event.message,
            'expire': expire_time,
            'filename': file_name,
            'mime': mime_type
        }
        
        dl_link = f"{BASE_URL}/dl/{unique_id}"
        stream_link = f"{BASE_URL}/stream/{unique_id}"
        
        txt = (f"✅ **لینک آماده شد!**\n\n"
               f"📂 نام فایل: `{file_name}`\n"
               f"⏳ انقضا: {SETTINGS['expire_time']//60} دقیقه\n\n"
               f"📥 **لینک دانلود پرسرعت:**\n`{dl_link}`")
        
        if can_stream:
            txt += f"\n\n▶️ **لینک پخش آنلاین:**\n`{stream_link}`"
            
        await msg.edit(txt, buttons=[[Button.inline("❌ حذف لینک", data=f"del_{unique_id}")]])

    except Exception as e:
        print(f"Error: {e}")
        await event.reply("خطایی رخ داد.")

# --- هندلر دکمه‌ها ---
@client.on(events.CallbackQuery)
async def callback_handler(event):
    if event.sender_id != ADMIN_ID:
        return
        
    data = event.data.decode('utf-8')
    if data == "toggle_active":
        SETTINGS['is_active'] = not SETTINGS['is_active']
        await event.answer("وضعیت تغییر کرد!")
        await admin_panel(event) # رفرش پنل
    elif data.startswith("set_time_"):
        SETTINGS['expire_time'] = int(data.split("_")[2])
        await event.answer("زمان ذخیره شد!")
    elif data == "clear_all":
        links_db.clear()
        await event.answer("همه لینک‌ها پاک شدند.")
    elif data.startswith("del_"):
        uid = data.split("_")[1]
        if uid in links_db:
            del links_db[uid]
            await event.edit("🗑 لینک حذف شد.")
        else:
            await event.answer("لینک قبلاً حذف شده.")

# --- سیستم دانلود پرسرعت ---
async def serve_file(unique_id, disposition):
    data = links_db.get(unique_id)
    if not data or time.time() > data['expire']:
        return "❌ Link Expired or Invalid", 404
        
    msg = data['msg']
    file_size = msg.file.size if hasattr(msg, 'file') else 0
    
    headers = {
        'Content-Type': data['mime'],
        'Content-Disposition': f'{disposition}; filename="{data["filename"]}"',
        'Content-Length': str(file_size),
        'Accept-Ranges': 'bytes'
    }

    async def file_generator():
        # درخواست بسته‌های 512 کیلوبایتی برای حداکثر سرعت
        chunk_size = 512 * 1024 
        async for chunk in client.iter_download(msg.media, request_size=chunk_size):
            yield chunk

    return Response(file_generator(), headers=headers)

@app.route('/dl/<unique_id>')
async def dl(unique_id): return await serve_file(unique_id, 'attachment')

@app.route('/stream/<unique_id>')
async def st(unique_id): return await serve_file(unique_id, 'inline')

@app.route('/')
async def home(): return "🚀 Server is Running!"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8000)))
