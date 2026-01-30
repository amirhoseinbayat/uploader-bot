import os
import time
import uuid
import asyncio
from telethon import TelegramClient, events, Button
from quart import Quart, request, Response

# --- دریافت اطلاعات ---
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = 98097025

BASE_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")

# --- تنظیمات ---
# نکته مهم: اینجا فقط کلاینت را تعریف می‌کنیم ولی استارت نمی‌زنیم
client = TelegramClient('bot_session', API_ID, API_HASH)
app = Quart(__name__)
links_db = {}

# --- روشن شدن ربات همزمان با سرور ---
@app.before_serving
async def startup():
    print("🤖 Bot is starting...")
    await client.start(bot_token=BOT_TOKEN)
    print("✅ Bot connected!")

# --- هندلر پیام‌ها ---
@client.on(events.NewMessage(incoming=True))
async def handle_file(event):
    if event.sender_id != ADMIN_ID:
        return

    if not event.media:
        await event.reply("❌ لطفاً فقط فایل ارسال کنید.")
        return

    buttons = [
        [Button.inline("⏱ 10 دقیقه", data=f"time_600_{event.id}"),
         Button.inline("⏱ 30 دقیقه", data=f"time_1800_{event.id}")],
        [Button.inline("⏱ 60 دقیقه", data=f"time_3600_{event.id}"),
         Button.inline("⏱ 2 ساعت", data=f"time_7200_{event.id}")]
    ]
    try:
        await event.reply("⏳ زمان انقضای لینک را انتخاب کنید:", buttons=buttons)
    except Exception as e:
        print(f"Error sending buttons: {e}")

# --- هندلر دکمه‌ها ---
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
            
            # ذخیره در حافظه
            links_db[unique_id] = {
                'msg': original_msg,
                'expire': expire_time,
                'filename': original_msg.file.name or f"file_{unique_id}"
            }
            
            final_url = BASE_URL.rstrip('/') + f"/dl/{unique_id}"
            del_btn = [Button.inline("❌ حذف لینک", data=f"del_{unique_id}")]
            
            await event.edit(
                f"✅ **لینک مستقیم آماده است!**\n\n"
                f"📂 فایل: `{links_db[unique_id]['filename']}`\n"
                f"⏳ اعتبار: {seconds//60} دقیقه\n\n"
                f"🔗 لینک دانلود:\n`{final_url}`",
                buttons=del_btn
            )
        except Exception as e:
            print(f"Error: {e}")

    elif data.startswith("del_"):
        _, uid = data.split("_")
        if uid in links_db:
            del links_db[uid]
            await event.answer("لینک حذف شد.", alert=True)
            await event.edit("🗑 این لینک دستی حذف شد.")
        else:
            await event.answer("لینک قبلاً منقضی شده است.", alert=True)

# --- سیستم دانلود (استریم) ---
@app.route('/dl/<unique_id>')
async def download_file(unique_id):
    data = links_db.get(unique_id)
    
    if not data:
        return "❌ Error: Link not found or deleted (Bot Restarted).", 404
    
    if time.time() > data['expire']:
        del links_db[unique_id]
        return "⏳ Error: Link expired.", 403
        
    msg = data['msg']
    file_name = data['filename']
    file_size = msg.file.size

    headers = {
        'Content-Type': 'application/octet-stream',
        'Content-Disposition': f'attachment; filename="{file_name}"',
        'Content-Length': str(file_size)
    }

    async def file_generator():
        # استفاده از iter_download برای جلوگیری از پر شدن رم
        async for chunk in client.iter_download(msg.media):
            yield chunk

    return Response(file_generator(), headers=headers)

# --- اجرای برنامه ---
if __name__ == '__main__':
    PORT = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=PORT)
