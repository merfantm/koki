"""
ربات تلگرام (نسخه Webhook) - آماده دیپلوی روی Render
------------------------------------------------------
تفاوت با نسخه Polling:
  - به‌جای حلقه بی‌نهایت getUpdates، یک وب‌سرور Flask داریم
    که تلگرام مستقیماً پیام‌ها رو بهش POST می‌کنه.
  - نیازی نیست گوشی یا کامپیوتر همیشه روشن باشه؛ روی سرور Render اجرا می‌شه.

تنظیمات لازم (به‌عنوان Environment Variable روی Render، نه داخل کد):
  BOT_TOKEN            -> توکن ربات از BotFather
  WEBHOOK_SECRET        -> یک رشته دلخواه و محرمانه برای امنیت بیشتر (اختیاری ولی پیشنهادی)

نحوه کارکرد deploy:
  1) این پروژه رو تو گیت‌هاب push کن
  2) روی Render یک Web Service جدید بساز و ریپو رو وصل کن
  3) Environment Variables رو ست کن (BOT_TOKEN و WEBHOOK_SECRET)
  4) Build Command:  pip install -r requirements.txt
     Start Command:  gunicorn app:app
  5) بعد از deploy موفق، خود برنامه به‌صورت خودکار در startup،
     webhook رو با آدرس عمومی Render (RENDER_EXTERNAL_URL) ثبت می‌کنه.
"""

import os
import requests
import numpy as np
from flask import Flask, request, jsonify
from PIL import Image, ImageOps, ImageEnhance, ImageFilter, ImageDraw, ImageFont

# ------------------ تنظیمات ------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-this-secret")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
FILE_URL = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
TEMP_DIR = "/tmp/temp_files"

os.makedirs(TEMP_DIR, exist_ok=True)

app = Flask(__name__)

# ------------------ حافظه موقت هر کاربر ------------------
# نکته مهم: چون این حافظه در RAM برنامه‌ست، اگه سرور ریست یا Sleep بشه
# (مثلاً در پلن رایگان Render بعد از بی‌فعالیتی)، این اطلاعات پاک می‌شه.
user_state = {}


def get_state(chat_id):
    return user_state.setdefault(chat_id, {"mode": None, "photos": [], "texts": [], "receipts": []})


def reset_state(chat_id):
    user_state[chat_id] = {"mode": None, "photos": [], "texts": [], "receipts": []}


# ------------------ توابع پایه ارتباط با API ------------------

def api_call(method: str, **params):
    url = f"{API_URL}/{method}"
    resp = requests.post(url, json=params, timeout=30)
    return resp.json()


def send_message(chat_id, text, keyboard=None):
    params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        params["reply_markup"] = keyboard
    api_call("sendMessage", **params)


def edit_message(chat_id, message_id, text, keyboard=None):
    params = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        params["reply_markup"] = keyboard
    api_call("editMessageText", **params)


def answer_callback(callback_id, text=""):
    api_call("answerCallbackQuery", callback_query_id=callback_id, text=text)


def send_chat_action(chat_id, action="typing"):
    api_call("sendChatAction", chat_id=chat_id, action=action)


def send_document(chat_id, file_path, caption=""):
    url = f"{API_URL}/sendDocument"
    with open(file_path, "rb") as f:
        files = {"document": f}
        data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
        requests.post(url, data=data, files=files, timeout=60)


def download_file(file_id, save_path):
    file_info = api_call("getFile", file_id=file_id)
    file_path = file_info["result"]["file_path"]
    file_url = f"{FILE_URL}/{file_path}"
    resp = requests.get(file_url, timeout=30)
    with open(save_path, "wb") as f:
        f.write(resp.content)


# ------------------ منوی اصلی (دکمه‌های شیشه‌ای) ------------------
FEATURES = [
    {"id": "photo_to_pdf", "title": "🖼  عکس به PDF"},
    {"id": "text_to_pdf", "title": "📝  متن به PDF"},
    {"id": "receipt_to_pdf", "title": "🧾  رسید کارتخوان به PDF"},
]


def main_menu_keyboard():
    buttons = [[{"text": f["title"], "callback_data": f["id"]}] for f in FEATURES]
    return {"inline_keyboard": buttons}


def collecting_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "✅  تمام شد، بساز!", "callback_data": "done"}],
            [{"text": "❌  لغو و بازگشت به منو", "callback_data": "cancel"}],
        ]
    }


def send_main_menu(chat_id, text="✨ یه گزینه رو انتخاب کن:"):
    send_message(chat_id, text, keyboard=main_menu_keyboard())


WELCOME_TEXT = (
    "👋 <b>سلام!</b>\n\n"
    "من دستیار تبدیل فایل تو هستم. می‌تونم:\n"
    "🖼 چند تا عکس رو تبدیل به یک فایل PDF کنم\n"
    "📝 متن رو به PDF تبدیل کنم\n"
    "🧾 رسیدهای کارتخوان رو خودکار مرتب و آماده چاپ کنم\n\n"
    "از منوی زیر شروع کن 👇"
)


# ------------------ قابلیت ۱: عکس به PDF ------------------

def convert_images_to_pdf(image_paths, output_path):
    images = [Image.open(p).convert("RGB") for p in image_paths]
    if not images:
        return False
    images[0].save(output_path, save_all=True, append_images=images[1:])
    return True


def start_photo_to_pdf(chat_id):
    state = get_state(chat_id)
    state["mode"] = "photo_to_pdf"
    state["photos"] = []
    send_message(
        chat_id,
        "📸 عکس‌هاتو یکی‌یکی بفرست.\nهر چند تا که خواستی بفرست، بعد دکمه «✅ تمام شد» رو بزن.",
        keyboard=collecting_keyboard(),
    )


def handle_photo_message_simple(chat_id, message):
    state = get_state(chat_id)
    photo = message["photo"][-1]
    file_id = photo["file_id"]
    idx = len(state["photos"])
    save_path = os.path.join(TEMP_DIR, f"{chat_id}_{idx}.jpg")
    download_file(file_id, save_path)
    state["photos"].append(save_path)


def finish_photo_to_pdf(chat_id):
    state = get_state(chat_id)
    photos = state["photos"]
    if not photos:
        send_message(chat_id, "⚠️ هنوز عکسی نفرستادی!")
        return

    send_chat_action(chat_id, "upload_document")
    send_message(chat_id, f"⏳ در حال ساخت PDF از <b>{len(photos)}</b> عکس...")
    output_path = os.path.join(TEMP_DIR, f"{chat_id}_output.pdf")

    if convert_images_to_pdf(photos, output_path):
        send_document(chat_id, output_path, caption="✅ <b>فایل PDF شما آماده است!</b>")
    else:
        send_message(chat_id, "❌ مشکلی پیش اومد، دوباره امتحان کن.")

    for p in photos:
        if os.path.exists(p):
            os.remove(p)
    if os.path.exists(output_path):
        os.remove(output_path)

    reset_state(chat_id)
    send_main_menu(chat_id, "🎉 تموم شد! کار دیگه‌ای هم مونده؟")


# ------------------ قابلیت ۲: متن به PDF ------------------

def convert_text_to_pdf(text, output_path):
    lines = text.split("\n")
    width, height = 1240, 1754
    pages = []
    font = ImageFont.load_default()

    y_per_page = height - 100
    line_height = 30
    lines_per_page = max(1, y_per_page // line_height)

    for i in range(0, len(lines), lines_per_page):
        page_lines = lines[i:i + lines_per_page]
        img = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(img)
        y = 50
        for line in page_lines:
            draw.text((50, y), line, fill="black", font=font)
            y += line_height
        pages.append(img)

    if not pages:
        return False
    pages[0].save(output_path, save_all=True, append_images=pages[1:])
    return True


def start_text_to_pdf(chat_id):
    state = get_state(chat_id)
    state["mode"] = "text_to_pdf"
    state["texts"] = []
    send_message(
        chat_id,
        "✍️ متنی که می‌خوای به PDF تبدیل بشه رو بفرست.\nوقتی تموم شد، دکمه «✅ تمام شد» رو بزن.",
        keyboard=collecting_keyboard(),
    )


def handle_text_for_pdf(chat_id, text):
    state = get_state(chat_id)
    state["texts"].append(text)


def finish_text_to_pdf(chat_id):
    state = get_state(chat_id)
    texts = state["texts"]
    if not texts:
        send_message(chat_id, "⚠️ هنوز متنی نفرستادی!")
        return

    send_chat_action(chat_id, "upload_document")
    send_message(chat_id, "⏳ در حال ساخت PDF...")
    output_path = os.path.join(TEMP_DIR, f"{chat_id}_text_output.pdf")
    full_text = "\n".join(texts)

    if convert_text_to_pdf(full_text, output_path):
        send_document(chat_id, output_path, caption="✅ <b>فایل PDF شما آماده است!</b>")
    else:
        send_message(chat_id, "❌ مشکلی پیش اومد، دوباره امتحان کن.")

    if os.path.exists(output_path):
        os.remove(output_path)

    reset_state(chat_id)
    send_main_menu(chat_id, "🎉 تموم شد! کار دیگه‌ای هم مونده؟")


# ------------------ قابلیت ۳: رسید کارتخوان به PDF ------------------

def enhance_receipt(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    arr = np.array(img).astype(np.float32)

    background = img.filter(ImageFilter.GaussianBlur(radius=25))
    bg_arr = np.array(background).astype(np.float32) + 1e-5

    normalized = (arr / bg_arr) * 255.0
    normalized = np.clip(normalized, 0, 255).astype(np.uint8)

    result = Image.fromarray(normalized)
    result = ImageOps.autocontrast(result, cutoff=1)
    result = ImageEnhance.Contrast(result).enhance(1.15)
    result = ImageEnhance.Sharpness(result).enhance(1.6)
    result = ImageEnhance.Color(result).enhance(1.05)

    return result


def place_on_a5(img: Image.Image, dpi=200) -> Image.Image:
    a5_w_mm, a5_h_mm = 148, 210
    page_w = int(a5_w_mm / 25.4 * dpi)
    page_h = int(a5_h_mm / 25.4 * dpi)

    canvas = Image.new("RGB", (page_w, page_h), "white")

    margin = int(0.06 * page_w)
    max_w = page_w - 2 * margin
    max_h = page_h - 2 * margin

    ratio = min(max_w / img.width, max_h / img.height)
    new_w = int(img.width * ratio)
    new_h = int(img.height * ratio)
    resized = img.resize((new_w, new_h), Image.LANCZOS)

    x = (page_w - new_w) // 2
    y = (page_h - new_h) // 2
    canvas.paste(resized, (x, y))
    return canvas


def start_receipt_to_pdf(chat_id):
    state = get_state(chat_id)
    state["mode"] = "receipt_to_pdf"
    state["receipts"] = []
    send_message(
        chat_id,
        "🧾 عکس رسیدهای کارتخوان رو یکی‌یکی بفرست.\n"
        "هر رسید خودکار ✨ رنگ/سایه‌ش اصلاح می‌شه و وسط برگه A5 قرار می‌گیره.\n"
        "وقتی تموم شد، دکمه «✅ تمام شد» رو بزن تا همه رو یکجا PDF کنم.",
        keyboard=collecting_keyboard(),
    )


def handle_receipt_photo(chat_id, message):
    state = get_state(chat_id)
    photo = message["photo"][-1]
    file_id = photo["file_id"]
    idx = len(state["receipts"])
    raw_path = os.path.join(TEMP_DIR, f"{chat_id}_receipt_raw_{idx}.jpg")
    download_file(file_id, raw_path)

    img = Image.open(raw_path)
    enhanced = enhance_receipt(img)
    page = place_on_a5(enhanced)

    processed_path = os.path.join(TEMP_DIR, f"{chat_id}_receipt_page_{idx}.jpg")
    page.save(processed_path, quality=95)

    state["receipts"].append(processed_path)
    os.remove(raw_path)


def finish_receipt_to_pdf(chat_id):
    state = get_state(chat_id)
    pages = state["receipts"]
    if not pages:
        send_message(chat_id, "⚠️ هنوز رسیدی نفرستادی!")
        return

    send_chat_action(chat_id, "upload_document")
    send_message(chat_id, f"⏳ در حال ساخت PDF نهایی از <b>{len(pages)}</b> رسید...")
    output_path = os.path.join(TEMP_DIR, f"{chat_id}_receipts_output.pdf")

    images = [Image.open(p).convert("RGB") for p in pages]
    images[0].save(output_path, save_all=True, append_images=images[1:])

    send_document(chat_id, output_path, caption="✅ <b>فایل PDF رسیدها آماده چاپ است!</b> 🖨")

    for p in pages:
        if os.path.exists(p):
            os.remove(p)
    if os.path.exists(output_path):
        os.remove(output_path)

    reset_state(chat_id)
    send_main_menu(chat_id, "🎉 تموم شد! کار دیگه‌ای هم مونده؟")


# ------------------ مسیریابی پیام‌ها ------------------

def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "")
    state = get_state(chat_id)

    if text == "/start":
        reset_state(chat_id)
        send_message(chat_id, WELCOME_TEXT, keyboard=main_menu_keyboard())
        return

    if text == "/reset":
        reset_state(chat_id)
        send_main_menu(chat_id, "🔄 همه چی پاک شد. از منو انتخاب کن:")
        return

    if "photo" in message:
        if state["mode"] == "photo_to_pdf":
            handle_photo_message_simple(chat_id, message)
        elif state["mode"] == "receipt_to_pdf":
            handle_receipt_photo(chat_id, message)
        else:
            send_message(chat_id, "لطفاً اول از منو یه گزینه انتخاب کن 👇")
            send_main_menu(chat_id)
        return

    if text and state["mode"] == "text_to_pdf":
        handle_text_for_pdf(chat_id, text)
        return

    send_main_menu(chat_id, "🤔 متوجه نشدم. یکی از گزینه‌ها رو انتخاب کن:")


def handle_callback(callback_query):
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    data = callback_query["data"]
    callback_id = callback_query["id"]
    state = get_state(chat_id)

    answer_callback(callback_id)

    if data == "cancel":
        reset_state(chat_id)
        edit_message(chat_id, message_id, "❌ لغو شد. یه گزینه انتخاب کن:", keyboard=main_menu_keyboard())
        return

    if data == "done":
        if state["mode"] == "photo_to_pdf":
            finish_photo_to_pdf(chat_id)
        elif state["mode"] == "text_to_pdf":
            finish_text_to_pdf(chat_id)
        elif state["mode"] == "receipt_to_pdf":
            finish_receipt_to_pdf(chat_id)
        else:
            send_main_menu(chat_id, "چیزی برای پردازش نیست. یه گزینه انتخاب کن:")
        return

    if data == "photo_to_pdf":
        start_photo_to_pdf(chat_id)
        return

    if data == "text_to_pdf":
        start_text_to_pdf(chat_id)
        return

    if data == "receipt_to_pdf":
        start_receipt_to_pdf(chat_id)
        return


# ------------------ مسیرهای Flask (Webhook) ------------------

@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "ok", "message": "ربات در حال اجراست ✅"})


@app.route(f"/webhook/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}

    try:
        if "message" in update:
            handle_message(update["message"])
        elif "callback_query" in update:
            handle_callback(update["callback_query"])
    except Exception as e:
        print(f"خطا در پردازش آپدیت: {e}")

    return jsonify({"ok": True})


def setup_webhook():
    """ثبت خودکار آدرس webhook روی تلگرام، با استفاده از آدرس عمومی Render"""
    external_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not external_url:
        print("⚠️ RENDER_EXTERNAL_URL تنظیم نشده؛ webhook خودکار ثبت نمی‌شه.")
        return

    webhook_url = f"{external_url}/webhook/{WEBHOOK_SECRET}"
    result = api_call("setWebhook", url=webhook_url)
    print("نتیجه ثبت webhook:", result)


# این بخش وقتی gunicorn ماژول رو import می‌کنه هم اجرا می‌شه
setup_webhook()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
