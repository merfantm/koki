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

# اگه پوشه fonts (شامل فونت‌های سفارشی) تو ریپو باشه، به فونت‌های سیستم اضافه می‌شه
# از find با -iname استفاده می‌کنیم تا حروف بزرگ/کوچک پسوند (.ttf یا .TTF) فرقی نداشته باشه
RUN mkdir -p /usr/share/fonts/truetype/custom && \
    if [ -d /app/fonts ]; then \
        find /app/fonts -type f \( -iname "*.ttf" -o -iname "*.otf" \) -exec cp {} /usr/share/fonts/truetype/custom/ \; ; \
    fi && \
    fc-cache -f -v

ENV PORT=10000
EXPOSE 10000

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000", "--timeout", "120"]
