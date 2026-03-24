FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY weclaw/ ./weclaw/
COPY config/ ./config/

# 创建数据目录
RUN mkdir -p data

# 运行时环境变量（必须在运行时传入 OPENAI_API_KEY）
ENV PYTHONUNBUFFERED=1

# 默认终端模式
ENTRYPOINT ["python", "-m", "weclaw"]
