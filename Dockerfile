# กลับไปใช้ single-stage แต่ optimize cache layers
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (รวมกันเพื่อลด layers)
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    && pip install --upgrade pip \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy requirements FIRST สำหรับ Docker layer caching
COPY requirements.txt .

# Install Python dependencies ในคำสั่งเดียว
RUN pip install --no-cache-dir "huggingface_hub[hf_xet]" \
    && pip install --no-cache-dir -r requirements.txt

# Copy source code LAST (จะไม่ invalidate cache layers ด้านบน)
COPY . .

# Default command (generic since docker-compose will override)
CMD ["python", "--version"]