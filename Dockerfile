FROM python:3.11-slim

# نصب LibreOffice (فقط بخش Writer برای کاهش حجم ایمیج) و ابزار فونت
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer \
    libreoffice-core \
    fonts-dejavu \
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# اگه پوشه fonts (شامل BNazanin.ttf و فونت‌های دیگه) تو ریپو باشه،
# به فونت‌های سیستم اضافه می‌شه تا هم PDF تنخواه هم تبدیل ورد درست نمایش داده بشن
RUN mkdir -p /usr/share/fonts/truetype/custom && \
    if [ -d /app/fonts ]; then cp /app/fonts/*.ttf /usr/share/fonts/truetype/custom/ 2>/dev/null || true; fi && \
    fc-cache -f -v

ENV PORT=10000
EXPOSE 10000

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000", "--timeout", "120"]
