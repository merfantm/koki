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
import html
import base64
import json
import requests
import numpy as np
from flask import Flask, request, jsonify
from PIL import Image, ImageOps, ImageEnhance, ImageFilter

# ------------------ تنظیمات ------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-this-secret").strip()
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "").strip()

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.environ.get("GITHUB_REPO", "").strip()          # مثلاً: username/repo-name
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main").strip()
GITHUB_TRACKS_PATH = "tracks.json"
GITHUB_USERS_PATH = "users.json"

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
FILE_URL = f"https://api.telegram.org/file/bot{BOT_TOKEN}"
TEMP_DIR = "/tmp/temp_files"

os.makedirs(TEMP_DIR, exist_ok=True)

app = Flask(__name__)

# ------------------ رمز Remix Tm ------------------
# لیست آهنگ‌ها دیگه اینجا نوشته نمی‌شه؛ خودکار از یک فایل تو گیت‌هابت (tracks.json)
# خونده و نوشته می‌شه. برای اضافه‌کردن آهنگ، کافیه فایل صوتی رو با کپشن
# "/addtrack عنوان آهنگ" مستقیم به ربات (با اکانت ادمین) بفرستی.
REMIX_PASSWORD = "koki"

# ------------------ متن دکمه‌های منو (برای تشخیص پیام‌ها) ------------------
MENU_PHOTO = "🖼 عکس به PDF"
MENU_RECEIPT = "🧾 رسید کارتخوان به PDF"
MENU_CONTACT = "🎁 دریافت اکانت رایگان"
MENU_REMIX = "🎵 Remix Tm"

DONE_BTN = "✅ تمام شد"
CANCEL_BTN = "❌ لغو و بازگشت به منو"
SHARE_CONTACT_BTN = "دریافت اکانت"
SKIP_CONTACT_BTN = "▶️ رد کردن و ادامه"

# ------------------ حافظه موقت هر کاربر ------------------
user_state = {}


def get_state(chat_id):
    return user_state.setdefault(chat_id, {
        "mode": None, "photos": [], "receipts": [],
        "feedback_phone": None,
    })


def reset_state(chat_id):
    user_state[chat_id] = {
        "mode": None, "photos": [], "receipts": [],
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


def send_audio_url(chat_id, audio_ref, title=""):
    """audio_ref می‌تونه یک URL مستقیم یا یک file_id تلگرام باشه"""
    api_call("sendAudio", chat_id=chat_id, audio=audio_ref, title=title)


def download_file(file_id, save_path):
    file_info = api_call("getFile", file_id=file_id)
    file_path = file_info["result"]["file_path"]
    file_url = f"{FILE_URL}/{file_path}"
    resp = requests.get(file_url, timeout=30)
    with open(save_path, "wb") as f:
        f.write(resp.content)


# ------------------ ذخیره‌سازی دائمی لیست آهنگ‌ها (روی گیت‌هاب) ------------------

def is_admin(chat_id):
    return bool(ADMIN_CHAT_ID) and str(chat_id).strip() == ADMIN_CHAT_ID


def github_headers():
    return {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}


def github_get_json(path):
    """برمی‌گردونه: (محتوای JSON به‌صورت لیست/دیکشنری, sha فعلی فایل یا None)"""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("⚠️ GITHUB_TOKEN یا GITHUB_REPO تنظیم نشده.")
        return [], None

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    resp = requests.get(url, headers=github_headers(), params={"ref": GITHUB_BRANCH}, timeout=20)

    if resp.status_code == 404:
        return [], None
    if resp.status_code != 200:
        print(f"خطا در دریافت {path} از گیت‌هاب:", resp.status_code, resp.text)
        return [], None

    data = resp.json()
    try:
        content = base64.b64decode(data["content"]).decode("utf-8")
        parsed = json.loads(content)
    except Exception as e:
        print(f"خطا در خوندن محتوای {path}:", e)
        parsed = []
    return parsed, data.get("sha")


def github_save_json(path, data_obj, sha, commit_message):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("⚠️ GITHUB_TOKEN یا GITHUB_REPO تنظیم نشده؛ ذخیره‌سازی انجام نشد.")
        return False

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    content_str = json.dumps(data_obj, ensure_ascii=False, indent=2)
    payload = {
        "message": commit_message,
        "content": base64.b64encode(content_str.encode("utf-8")).decode("utf-8"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(url, headers=github_headers(), json=payload, timeout=20)
    if resp.status_code not in (200, 201):
        print(f"خطا در ذخیره {path}:", resp.status_code, resp.text)
        return False
    return True


def github_get_tracks():
    return github_get_json(GITHUB_TRACKS_PATH)


def github_save_tracks(tracks, sha):
    return github_save_json(GITHUB_TRACKS_PATH, tracks, sha, "به‌روزرسانی لیست ریمیکس‌ها")


def github_get_users():
    return github_get_json(GITHUB_USERS_PATH)


def github_save_users(users, sha):
    return github_save_json(GITHUB_USERS_PATH, users, sha, "به‌روزرسانی لیست کاربران")


known_user_ids = set()  # کش موقت در حافظه، برای جلوگیری از درخواست تکراری به گیت‌هاب


def register_user(chat_id, from_user):
    """کاربری که پیام فرستاده رو (اگه قبلاً ثبت نشده) به لیست دائمی اضافه می‌کنه"""
    if str(chat_id) in known_user_ids:
        return  # قبلاً تو همین اجرا چک شده، نیازی به درخواست گیت‌هاب نیست

    users, sha = github_get_users()
    for u in users:
        if str(u.get("chat_id")) == str(chat_id):
            known_user_ids.add(str(chat_id))
            return  # قبلاً ثبت شده

    users.append({
        "chat_id": chat_id,
        "username": from_user.get("username"),
        "first_name": from_user.get("first_name"),
    })
    if github_save_users(users, sha):
        known_user_ids.add(str(chat_id))


def handle_add_track(chat_id, message):
    audio = message["audio"]
    caption = message.get("caption", "") or ""
    title = caption.replace("/addtrack", "", 1).strip()
    if not title:
        title = audio.get("title") or audio.get("file_name") or "بدون عنوان"

    tracks, sha = github_get_tracks()
    tracks.append({"title": title, "file_id": audio["file_id"]})
    ok = github_save_tracks(tracks, sha)

    if ok:
        send_message(chat_id, f"✅ آهنگ «{html.escape(title)}» اضافه شد.\nتعداد کل آهنگ‌ها: {len(tracks)}")
    else:
        send_message(chat_id, "❌ ذخیره‌سازی دائمی انجام نشد. تنظیمات GITHUB_TOKEN / GITHUB_REPO رو چک کن.")


def handle_list_tracks(chat_id):
    tracks, _ = github_get_tracks()
    if not tracks:
        send_message(chat_id, "لیست ریمیکس‌ها فعلاً خالیه.")
        return
    lines = [f"{i+1}. {t.get('title', 'بدون عنوان')}" for i, t in enumerate(tracks)]
    send_message(chat_id, "🎵 <b>لیست ریمیکس‌ها:</b>\n" + "\n".join(lines))


def handle_remove_track(chat_id, text):
    parts = text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        send_message(chat_id, "فرمت درست: /removetrack شماره\nمثلاً: /removetrack 2\n(شماره رو از /listtracks بگیر)")
        return
    idx = int(parts[1]) - 1
    tracks, sha = github_get_tracks()
    if idx < 0 or idx >= len(tracks):
        send_message(chat_id, "همچین شماره‌ای تو لیست نیست.")
        return
    removed = tracks.pop(idx)
    ok = github_save_tracks(tracks, sha)
    if ok:
        send_message(chat_id, f"🗑 «{html.escape(removed.get('title',''))}» حذف شد.")
    else:
        send_message(chat_id, "❌ مشکلی تو ذخیره‌سازی پیش اومد.")


# ------------------ ارسال پیام همگانی (Broadcast) ------------------

def handle_broadcast(chat_id, text):
    message_text = text.replace("/broadcast", "", 1).strip()
    if not message_text:
        send_message(chat_id, "فرمت درست:\n/broadcast متن پیامت\n\nمثال:\n/broadcast سلام! امروز یه ریمیکس جدید اضافه شد 🎵")
        return

    users, _ = github_get_users()
    if not users:
        send_message(chat_id, "هنوز هیچ کاربری تو لیست نیست.")
        return

    send_message(chat_id, f"⏳ در حال ارسال پیام به {len(users)} کاربر...")

    success, failed = 0, 0
    for u in users:
        result = api_call("sendMessage", chat_id=u["chat_id"], text=message_text)
        if result.get("ok"):
            success += 1
        else:
            failed += 1

    send_message(chat_id, f"✅ ارسال تموم شد.\nموفق: {success}\nناموفق: {failed}")


def handle_users_count(chat_id):
    users, _ = github_get_users()
    send_message(chat_id, f"👥 تعداد کاربران ثبت‌شده: {len(users)}")


# ------------------ کیبوردهای ثابت (Reply Keyboard) ------------------

def main_menu_keyboard():
    return {
        "keyboard": [
            [{"text": MENU_PHOTO}, {"text": MENU_RECEIPT}],
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
            [{"text": CANCEL_BTN}],
        ],
        "resize_keyboard": True,
    }


def send_main_menu(chat_id, text="✨ یه گزینه رو از منو انتخاب کن:"):
    send_message(chat_id, text, keyboard=main_menu_keyboard())


WELCOME_TEXT = (
    "👋 <b>سلام!</b>\n\n"
    "از منوی پایین صفحه یکی از گزینه‌ها رو انتخاب کن:\n"
    f"{MENU_PHOTO}\n{MENU_RECEIPT}\n{MENU_CONTACT}\n{MENU_REMIX}"
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


# ------------------ قابلیت ۲: رسید کارتخوان به PDF ------------------

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


# ------------------ قابلیت ۴: ثبت نام و دریافت جایزه ------------------

def start_contact_us(chat_id):
    state = get_state(chat_id)
    state["mode"] = "feedback_awaiting_contact"
    state["feedback_phone"] = None
    send_message(
        chat_id,
        "🎁 <b>دریافت اکانت رایگان</b>\n\n"
        "جهت دریافت اکانت گزینه دریافت امانت را انتخاب کنید",
        keyboard=contact_request_keyboard(),
    )


def handle_contact_shared(chat_id, message):
    state = get_state(chat_id)
    contact = message.get("contact")
    from_user = message.get("from", {})

    phone = contact.get("phone_number") if contact else None
    user_id = from_user.get("id", "نامشخص")
    username = from_user.get("username")
    username_display = f"@{username}" if username else "(بدون یوزرنیم)"
    first_name = from_user.get("first_name", "")

    if ADMIN_CHAT_ID:
        report = (
            "🎁 <b>درخواست جدید دریافت اکانت رایگان</b>\n\n"
            f"👤 نام: {html.escape(str(first_name))}\n"
            f"🔗 یوزرنیم: {html.escape(username_display)}\n"
            f"🆔 آیدی عددی: <code>{user_id}</code>\n"
            f"📱 شماره تماس: {html.escape(str(phone or 'ثبت نشد'))}"
        )
        result = api_call("sendMessage", chat_id=ADMIN_CHAT_ID, text=report, parse_mode="HTML")
        print("نتیجه ارسال درخواست به ادمین:", result)
        if not result.get("ok"):
            print("❌ ارسال به ادمین ناموفق بود. ADMIN_CHAT_ID فعلی:", repr(ADMIN_CHAT_ID))
    else:
        print("⚠️ ADMIN_CHAT_ID تنظیم نشده؛ اطلاعات درخواست فقط همینجا لاگ شد.")

    reset_state(chat_id)
    send_main_menu(chat_id, "🎉 درخواستت ثبت شد! به‌زودی برای هماهنگی باهات تماس می‌گیریم.")


# ------------------ قابلیت ۵: Remix Tm (محافظت‌شده با رمز) ------------------

def start_remix(chat_id):
    state = get_state(chat_id)
    state["mode"] = "awaiting_remix_password"
    send_message(chat_id, "🔒 برای دیدن ریمیکس‌ها، رمز رو وارد کن:", keyboard=cancel_only_keyboard())


def handle_remix_password(chat_id, text):
    if text.strip() == REMIX_PASSWORD:
        tracks, _ = github_get_tracks()
        if not tracks:
            send_message(chat_id, "🎵 رمز درست بود، ولی هنوز آهنگی اضافه نشده. بعداً سر بزن!")
        else:
            send_message(chat_id, "✅ رمز درست بود! در حال ارسال ریمیکس‌ها...")
            for track in tracks:
                send_audio_url(chat_id, track["file_id"], title=track.get("title", ""))
        reset_state(chat_id)
        send_main_menu(chat_id)
    else:
        send_message(chat_id, "❌ رمز اشتباهه. دوباره امتحان کن یا لغو کن:", keyboard=cancel_only_keyboard())
        # state["mode"] رو تغییر نمی‌دیم تا بتونه دوباره تلاش کنه


# ------------------ مسیریابی پیام‌ها ------------------

def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "")
    from_user = message.get("from", {})
    state = get_state(chat_id)
    mode = state["mode"]

    # ثبت خودکار هر کاربری که هر نوع پیامی بفرسته (نه فقط /start)
    # این باعث می‌شه کاربرای قدیمی هم به محض اولین تعامل دوباره، ثبت بشن
    register_user(chat_id, from_user)

    if text == "/start":
        reset_state(chat_id)
        send_message(chat_id, WELCOME_TEXT, keyboard=main_menu_keyboard())
        return

    if text == "/myid":
        send_message(chat_id, f"🆔 chat_id تو: <code>{chat_id}</code>")
        return

    # --- دستورات ادمین برای مدیریت آهنگ‌های Remix Tm ---
    if "audio" in message and is_admin(chat_id) and message.get("caption", "").startswith("/addtrack"):
        handle_add_track(chat_id, message)
        return

    if text == "/listtracks" and is_admin(chat_id):
        handle_list_tracks(chat_id)
        return

    if text.startswith("/removetrack") and is_admin(chat_id):
        handle_remove_track(chat_id, text)
        return

    # --- دستورات ادمین برای پیام همگانی ---
    if text.startswith("/broadcast") and is_admin(chat_id):
        handle_broadcast(chat_id, text)
        return

    if text == "/users" and is_admin(chat_id):
        handle_users_count(chat_id)
        return

    if text == CANCEL_BTN:
        reset_state(chat_id)
        send_main_menu(chat_id, "❌ لغو شد.")
        return

    if text == DONE_BTN:
        if mode == "photo_to_pdf":
            finish_photo_to_pdf(chat_id)
        elif mode == "receipt_to_pdf":
            finish_receipt_to_pdf(chat_id)
        else:
            send_main_menu(chat_id, "چیزی برای پردازش نیست.")
        return

    # انتخاب از منوی اصلی (فقط وقتی حالت خاصی فعال نیست)
    if mode is None:
        if text == MENU_PHOTO:
            start_photo_to_pdf(chat_id); return
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

    # اشتراک‌گذاری مخاطب (شماره تلفن) برای ثبت‌نام
    if "contact" in message and mode == "feedback_awaiting_contact":
        handle_contact_shared(chat_id, message)
        return

    if mode == "awaiting_remix_password" and text:
        handle_remix_password(chat_id, text)
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
