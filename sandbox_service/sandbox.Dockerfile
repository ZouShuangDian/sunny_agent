FROM python:3.11-slim

# 预装常用运维/开发工具，避免 LLM 执行时因 command not found 受阻
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    git \
    jq \
    unzip \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# 保持容器常驻，等待 exec 注入命令
CMD ["sleep", "infinity"]
