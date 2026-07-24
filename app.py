"""
ربات تلگرام (نسخه Webhook) - با منوی ثابت (Reply Keyboard)
------------------------------------------------------------
قابلیت‌ها:
  1) 🖼 عکس به PDF
  2) 📝 متن به PDF
  3) 🧾 رسید کارتخوان به PDF
  4) 📩 ارتباط با ما (ارسال انتقاد/پیشنهاد به ادمین + دریافت اختیاری شماره تماس)
  5) 🎵 Remix Tm (محتوای محافظت‌شده با رمز)

Environment Variables لازم روی Render:
  BOT_TOKEN        -> توکن ربات از BotFather
  WEBHOOK_SECRET   -> رشته دلخواه برای امنیت مسیر webhook
  ADMIN_CHAT_ID    -> chat_id عددی اکانتی که باید پیام‌های «ارتباط با ما» بهش برسه
                      (برای گرفتنش: با همون اکانت به ربات /start بزن، بعد دستور /myid رو بزن)
"""

import os
import requests
import numpy as np
from flask import Flask, request, jsonify
from PIL import Image, ImageOps, ImageEnhance, ImageFilter, ImageDraw, ImageFont

# ------------------ تنظیمات ------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-this-secret")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "")  # chat_id عددی merfantm

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
FILE_URL = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
TEMP_DIR = "/tmp/temp_files"

os.makedirs(TEMP_DIR, exist_ok=True)

app = Flask(__name__)

# ------------------ رمز و محتوای Remix Tm ------------------
REMIX_PASSWORD = "koki"
# لینک مستقیم فایل صوتی یا file_id تلگرام رو اینجا اضافه کن
REMIX_TRACKS = [
    # {"title": "ریمیکس ۱", "url": "https://example.com/track1.mp3"},
]

# ------------------ متن دکمه‌های منو (برای تشخیص پیام‌ها) ------------------
MENU_PHOTO = "🖼 عکس به PDF"
MENU_TEXT = "📝 متن به PDF"
MENU_RECEIPT = "🧾 رسید کارتخوان به PDF"
MENU_CONTACT = "📩 ارتباط با ما"
MENU_REMIX = "🎵 Remix Tm"

DONE_BTN = "✅ تمام شد"
CANCEL_BTN = "❌ لغو و بازگشت به منو"
SHARE_CONTACT_BTN = "📱 اشتراک شماره تماس"
SKIP_CONTACT_BTN = "▶️ رد کردن و ادامه"

# ------------------ حافظه موقت هر کاربر ------------------
user_state = {}


def get_state(chat_id):
    return user_state.setdefault(chat_id, {
        "mode": None, "photos": [], "texts": [], "receipts": [],
        "feedback_phone": None,
    })


def reset_state(chat_id):
    user_state[chat_id] = {
        "mode": None, "photos": [], "texts": [], "receipts": [],
        "feedback_phone": None,
    }


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


def send_chat_action(chat_id, action="typing"):
    api_call("sendChatAction", chat_id=chat_id, action=action)


def send_document(chat_id, file_path, caption=""):
    url = f"{API_URL}/sendDocument"
    with open(file_path, "rb") as f:
        files = {"document": f}
        data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
        requests.post(url, data=data, files=files, timeout=60)


def send_audio_url(chat_id, audio_url, title=""):
    api_call("sendAudio", chat_id=chat_id, audio=audio_url, title=title)


def download_file(file_id, save_path):
    file_info = api_call("getFile", file_id=file_id)
    file_path = file_info["result"]["file_path"]
    file_url = f"{FILE_URL}/{file_path}"
    resp = requests.get(file_url, timeout=30)
    with open(save_path, "wb") as f:
        f.write(resp.content)


# ------------------ کیبوردهای ثابت (Reply Keyboard) ------------------

def main_menu_keyboard():
    return {
        "keyboard": [
            [{"text": MENU_PHOTO}, {"text": MENU_TEXT}],
            [{"text": MENU_RECEIPT}],
            [{"text": MENU_CONTACT}, {"text": MENU_REMIX}],
        ],
        "resize_keyboard": True,
    }


def collecting_keyboard():
    return {
        "keyboard": [[{"text": DONE_BTN}], [{"text": CANCEL_BTN}]],
        "resize_keyboard": True,
    }


def cancel_only_keyboard():
    return {"keyboard": [[{"text": CANCEL_BTN}]], "resize_keyboard": True}


def contact_request_keyboard():
    return {
        "keyboard": [
            [{"text": SHARE_CONTACT_BTN, "request_contact": True}],
            [{"text": SKIP_CONTACT_BTN}],
            [{"text": CANCEL_BTN}],
        ],
        "resize_keyboard": True,
    }


def send_main_menu(chat_id, text="✨ یه گزینه رو از منو انتخاب کن:"):
    send_message(chat_id, text, keyboard=main_menu_keyboard())


WELCOME_TEXT = (
    "👋 <b>سلام!</b>\n\n"
    "از منوی پایین صفحه یکی از گزینه‌ها رو انتخاب کن:\n"
    f"{MENU_PHOTO}\n{MENU_TEXT}\n{MENU_RECEIPT}\n{MENU_CONTACT}\n{MENU_REMIX}"
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
    send_message(chat_id, "📸 عکس‌هاتو یکی‌یکی بفرست.\nهر وقت تموم شد، «✅ تمام شد» رو بزن.", keyboard=collecting_keyboard())


def handle_photo_message_simple(chat_id, message):
    state = get_state(chat_id)
    photo = message["photo"][-1]
    idx = len(state["photos"])
    save_path = os.path.join(TEMP_DIR, f"{chat_id}_{idx}.jpg")
    download_file(photo["file_id"], save_path)
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
    line_height = 30
    lines_per_page = max(1, (height - 100) // line_height)
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
    send_message(chat_id, "✍️ متنی که می‌خوای به PDF تبدیل بشه رو بفرست.\nوقتی تموم شد، «✅ تمام شد» رو بزن.", keyboard=collecting_keyboard())


def handle_text_for_pdf(chat_id, text):
    get_state(chat_id)["texts"].append(text)


def finish_text_to_pdf(chat_id):
    state = get_state(chat_id)
    texts = state["texts"]
    if not texts:
        send_message(chat_id, "⚠️ هنوز متنی نفرستادی!")
        return
    send_chat_action(chat_id, "upload_document")
    send_message(chat_id, "⏳ در حال ساخت PDF...")
    output_path = os.path.join(TEMP_DIR, f"{chat_id}_text_output.pdf")
    if convert_text_to_pdf("\n".join(texts), output_path):
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
    normalized = np.clip((arr / bg_arr) * 255.0, 0, 255).astype(np.uint8)
    result = Image.fromarray(normalized)
    result = ImageOps.autocontrast(result, cutoff=1)
    result = ImageEnhance.Contrast(result).enhance(1.15)
    result = ImageEnhance.Sharpness(result).enhance(1.6)
    result = ImageEnhance.Color(result).enhance(1.05)
    return result


def place_on_a5(img: Image.Image, dpi=200) -> Image.Image:
    page_w = int(148 / 25.4 * dpi)
    page_h = int(210 / 25.4 * dpi)
    canvas = Image.new("RGB", (page_w, page_h), "white")
    margin = int(0.06 * page_w)
    max_w, max_h = page_w - 2 * margin, page_h - 2 * margin
    ratio = min(max_w / img.width, max_h / img.height)
    new_w, new_h = int(img.width * ratio), int(img.height * ratio)
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    canvas.paste(resized, ((page_w - new_w) // 2, (page_h - new_h) // 2))
    return canvas


def start_receipt_to_pdf(chat_id):
    state = get_state(chat_id)
    state["mode"] = "receipt_to_pdf"
    state["receipts"] = []
    send_message(
        chat_id,
        "🧾 عکس رسیدهای کارتخوان رو یکی‌یکی بفرست.\nهر رسید خودکار اصلاح می‌شه و وسط A5 قرار می‌گیره.\nوقتی تموم شد، «✅ تمام شد» رو بزن.",
        keyboard=collecting_keyboard(),
    )


def handle_receipt_photo(chat_id, message):
    state = get_state(chat_id)
    photo = message["photo"][-1]
    idx = len(state["receipts"])
    raw_path = os.path.join(TEMP_DIR, f"{chat_id}_receipt_raw_{idx}.jpg")
    download_file(photo["file_id"], raw_path)
    page = place_on_a5(enhance_receipt(Image.open(raw_path)))
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


# ------------------ قابلیت ۴: ارتباط با ما ------------------

def start_contact_us(chat_id):
    state = get_state(chat_id)
    state["mode"] = "feedback_awaiting_contact"
    state["feedback_phone"] = None
    send_message(
        chat_id,
        "📩 خوشحال می‌شیم نظرت رو بشنویم!\n\n"
        "اگه مایلی، شماره تماستو هم به اشتراک بذار (کاملاً اختیاریه):",
        keyboard=contact_request_keyboard(),
    )


def handle_contact_shared(chat_id, message):
    state = get_state(chat_id)
    contact = message.get("contact")
    if contact:
        state["feedback_phone"] = contact.get("phone_number")
    state["mode"] = "feedback_awaiting_message"
    send_message(chat_id, "✅ شماره ثبت شد.\n\nحالا انتقاد، پیشنهاد یا پیامت رو بنویس:", keyboard=cancel_only_keyboard())


def handle_contact_skip(chat_id):
    state = get_state(chat_id)
    state["feedback_phone"] = None
    state["mode"] = "feedback_awaiting_message"
    send_message(chat_id, "باشه، بدون شماره ادامه می‌دیم.\n\nانتقاد، پیشنهاد یا پیامت رو بنویس:", keyboard=cancel_only_keyboard())


def handle_feedback_message(chat_id, message):
    state = get_state(chat_id)
    text = message.get("text", "")
    from_user = message.get("from", {})

    user_id = from_user.get("id", "نامشخص")
    username = from_user.get("username")
    username_display = f"@{username}" if username else "(بدون یوزرنیم)"
    first_name = from_user.get("first_name", "")
    phone = state.get("feedback_phone") or "به اشتراک گذاشته نشده"

    if ADMIN_CHAT_ID:
        report = (
            "📩 <b>پیام جدید از بخش ارتباط با ما</b>\n\n"
            f"👤 نام: {first_name}\n"
            f"🔗 یوزرنیم: {username_display}\n"
            f"🆔 آیدی عددی: <code>{user_id}</code>\n"
            f"📱 شماره تماس: {phone}\n\n"
            f"💬 پیام:\n{text}"
        )
        send_message(ADMIN_CHAT_ID, report)
    else:
        print("⚠️ ADMIN_CHAT_ID تنظیم نشده؛ پیام کاربر فقط اینجا لاگ شد:", text)

    reset_state(chat_id)
    send_main_menu(chat_id, "🙏 ممنون از پیامت! به دستمون رسید.")


# ------------------ قابلیت ۵: Remix Tm (محافظت‌شده با رمز) ------------------

def start_remix(chat_id):
    state = get_state(chat_id)
    state["mode"] = "awaiting_remix_password"
    send_message(chat_id, "🔒 برای دیدن ریمیکس‌ها، رمز رو وارد کن:", keyboard=cancel_only_keyboard())


def handle_remix_password(chat_id, text):
    if text.strip() == REMIX_PASSWORD:
        if not REMIX_TRACKS:
            send_message(chat_id, "🎵 رمز درست بود، ولی هنوز آهنگی اضافه نشده. بعداً سر بزن!")
        else:
            send_message(chat_id, "✅ رمز درست بود! در حال ارسال ریمیکس‌ها...")
            for track in REMIX_TRACKS:
                send_audio_url(chat_id, track["url"], title=track.get("title", ""))
        reset_state(chat_id)
        send_main_menu(chat_id)
    else:
        send_message(chat_id, "❌ رمز اشتباهه. دوباره امتحان کن یا لغو کن:", keyboard=cancel_only_keyboard())
        # state["mode"] رو تغییر نمی‌دیم تا بتونه دوباره تلاش کنه


# ------------------ مسیریابی پیام‌ها ------------------

def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "")
    state = get_state(chat_id)
    mode = state["mode"]

    if text == "/start":
        reset_state(chat_id)
        send_message(chat_id, WELCOME_TEXT, keyboard=main_menu_keyboard())
        return

    if text == "/myid":
        send_message(chat_id, f"🆔 chat_id تو: <code>{chat_id}</code>")
        return

    if text == CANCEL_BTN:
        reset_state(chat_id)
        send_main_menu(chat_id, "❌ لغو شد.")
        return

    if text == DONE_BTN:
        if mode == "photo_to_pdf":
            finish_photo_to_pdf(chat_id)
        elif mode == "text_to_pdf":
            finish_text_to_pdf(chat_id)
        elif mode == "receipt_to_pdf":
            finish_receipt_to_pdf(chat_id)
        else:
            send_main_menu(chat_id, "چیزی برای پردازش نیست.")
        return

    # انتخاب از منوی اصلی (فقط وقتی حالت خاصی فعال نیست)
    if mode is None:
        if text == MENU_PHOTO:
            start_photo_to_pdf(chat_id); return
        if text == MENU_TEXT:
            start_text_to_pdf(chat_id); return
        if text == MENU_RECEIPT:
            start_receipt_to_pdf(chat_id); return
        if text == MENU_CONTACT:
            start_contact_us(chat_id); return
        if text == MENU_REMIX:
            start_remix(chat_id); return

    # حالت‌های در حال جمع‌آوری عکس
    if "photo" in message:
        if mode == "photo_to_pdf":
            handle_photo_message_simple(chat_id, message)
        elif mode == "receipt_to_pdf":
            handle_receipt_photo(chat_id, message)
        else:
            send_message(chat_id, "لطفاً اول از منو یه گزینه انتخاب کن 👇")
            send_main_menu(chat_id)
        return

    # اشتراک‌گذاری مخاطب (شماره تلفن)
    if "contact" in message and mode == "feedback_awaiting_contact":
        handle_contact_shared(chat_id, message)
        return

    if text == SKIP_CONTACT_BTN and mode == "feedback_awaiting_contact":
        handle_contact_skip(chat_id)
        return

    if mode == "feedback_awaiting_message" and text:
        handle_feedback_message(chat_id, message)
        return

    if mode == "awaiting_remix_password" and text:
        handle_remix_password(chat_id, text)
        return

    if mode == "text_to_pdf" and text:
        handle_text_for_pdf(chat_id, text)
        return

    send_main_menu(chat_id, "🤔 متوجه نشدم. یکی از گزینه‌های منو رو انتخاب کن:")


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
    except Exception as e:
        print(f"خطا در پردازش آپدیت: {e}")
    return jsonify({"ok": True})


def setup_webhook():
    external_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not external_url:
        print("⚠️ RENDER_EXTERNAL_URL تنظیم نشده؛ webhook خودکار ثبت نمی‌شه.")
        return
    webhook_url = f"{external_url}/webhook/{WEBHOOK_SECRET}"
    result = api_call("setWebhook", url=webhook_url)
    print("نتیجه ثبت webhook:", result)


setup_webhook()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
