FROM python:3.11-slim

# نصب LibreOffice کامل + پشتیبانی PDF import + ابزار فونت
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer \
    libreoffice-core \
    libreoffice-impress \
    libreoffice-draw \
    fonts-dejavu \
    fonts-liberation \
    fontconfig \
    # وابستگی‌های لازم برای PDF import در LibreOffice
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# کپی فونت‌های سفارشی (B Nazanin و بقیه)
RUN mkdir -p /usr/share/fonts/truetype/custom && \
    if [ -d /app/fonts ]; then \
        find /app/fonts -type f \( -iname "*.ttf" -o -iname "*.otf" \) -exec cp {} /usr/share/fonts/truetype/custom/ \; ; \
    fi && \
    fc-cache -f -v

# پوشه موقت برای LibreOffice profile (جلوگیری از تداخل)
RUN mkdir -p /tmp/lo_profiles && chmod 777 /tmp/lo_profiles

ENV PORT=10000
# بدون این متغیر LibreOffice در محیط headless ممکنه crash کنه
ENV HOME=/tmp

EXPOSE 10000

# timeout رو به 180 افزایش دادیم چون PDF→Word ممکنه کمی طول بکشه
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000", "--timeout", "180", "--workers", "1"]
