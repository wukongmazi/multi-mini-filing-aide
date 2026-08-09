# 魔搭创空间 Docker 部署：端口必须 7860
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PORT=7860

WORKDIR /app

# 后端依赖
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# 后端代码
COPY backend/ /app/backend/

# 前端静态资源（与 backend 同级，StaticFiles 以 /app 为根目录托管）
COPY index.html /app/index.html
COPY css/ /app/css/
COPY js/ /app/js/

WORKDIR /app/backend
EXPOSE 7860

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-7860}"]
