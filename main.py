import os
import time
import uuid
import asyncio
from telethon import TelegramClient, events
from quart import Quart, request, Response

# --- دریافت اطلاعات از Render ---
API_ID = int(os.environ.get("343876"))
API_HASH = os.environ.get("f066819b9fde48c2622bb53101f25caf")
BOT_TOKEN = os.environ.get("180657140:AAGkIOYKg2iqoH7Ca142qFU1D2GwMt6IH10")
ADMIN_ID = 98097025  # آیدی عددی شما ثابت است

# اگر آدرس سایت هنوز مشخص نیست، پیش‌فرض لوکال‌هاست باشد
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")

# --- راه‌اندازی ---
client = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
app = Quart(__name__)

# حافظه موقت لینک‌ها
links_db = {}

@client.on(events.NewMessage(incoming=True))
async def handle_file(event):
    if event.sender_id != ADMIN_ID:
        return

    if not event.media:
        await event.reply("❌ لطفاً فقط فایل ارسال کنید.")
        return

    # منوی انتخاب زمان
    buttons = [
        [events.NewMessage.Button.inline("⏱ 10 دقیقه", data=f"time_600_{event.id}"),
         events.NewMessage.Button.inline("⏱ 30 دقیقه", data=f"time_1800_{event.id}")],
        [events.NewMessage.Button.inline("⏱ 60 دقیقه", data=f"time_3600_{event.id}"),
         events.NewMessage.Button.inline("⏱ 2 ساعت", data=f"time_7200_{event.id}")]
    ]
    await event.reply("⏳ زمان انقضای لینک را انتخاب کنید:", buttons=buttons)

@client.on(events.CallbackQuery)
async def callback_handler(event):
    data = event.data.decode('utf-8')
    
    if data.startswith("time_"):
        try:
            _, seconds, msg_id = data.split("_")
            seconds = int(seconds)
            original_msg = await client.get_messages(event.chat_id, ids=int(msg_id))
            
            if not original_msg or not original_msg.media:
                await event.answer("فایل پیدا نشد!", alert=True)
                return

            unique_id = str(uuid.uuid4())[:8]
            expire_time = time.time() + seconds
            
            links_db[unique_id] = {
                'msg': original_msg,
                'expire': expire_time,
                'filename': original_msg.file.name or f"file_{unique_id}"
            }
            
            # اصلاح آدرس لینک (حذف اسلش اضافه در صورت وجود)
            final_url = BASE_URL.rstrip('/') + f"/dl/{unique_id}"
            
            del_btn = [events.NewMessage.Button.inline("❌ حذف لینک", data=f"del_{unique_id}")]
            
            await event.edit(
                f"✅ **لینک مستقیم آماده است!**\n\n"
                f"📂 نام فایل: `{links_db[unique_id]['filename']}`\n"
                f"⏳ اعتبار: {seconds//60} دقیقه\n\n"
                f"🔗 لینک دانلود:\n`{final_url}`",
                buttons=del_btn
            )
        except Exception as e:
            await event.reply(f"Error: {str(e)}")

    elif data.startswith("del_"):
        _, uid = data.split("_")
        if uid in links_db:
            del links_db[uid]
            await event.answer("لینک حذف شد.", alert=True)
            await event.edit("🗑 این لینک دستی حذف شد.")
        else:
            await event.answer("لینک قبلاً حذف شده است.", alert=True)

@app.route('/dl/<unique_id>')
async def download_file(unique_id):
    data = links_db.get(unique_id)
    
    if not data:
        return "❌ Error: Link not found or deleted.", 404
    
    if time.time() > data['expire']:
        del links_db[unique_id]
        return "⏳ Error: Link expired.", 403
        
    msg = data['msg']
    file_name = data['filename']

    # هدرهای لازم برای دانلود منیجرها
    headers = {
        'Content-Type': 'application/octet-stream',
        'Content-Disposition': f'attachment; filename="{file_name}"',
        'Content-Length': str(msg.file.size)
    }

    async def file_generator():
        # دانلود و استریم همزمان (بدون ذخیره در دیسک)
        async for chunk in client.download_file(msg.media, file=bytes):
            yield chunk

    return Response(file_generator(), headers=headers)

# اجرای سرور روی پورتی که Render می‌دهد
PORT = int(os.environ.get("PORT", 8000))
loop = asyncio.get_event_loop()
app.run(loop=loop, host="0.0.0.0", port=PORT)
