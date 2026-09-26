FROM python:3.12-slim

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# SQLite 数据文件目录（Compose 中挂载卷持久化）
ENV DATABASE_PATH=/data/app.db
RUN mkdir -p /data

EXPOSE 8000

# 启动时自动完成建表（见 app/main.py 的 lifespan）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
