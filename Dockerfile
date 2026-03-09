FROM registry.vestack.sunnyoptical.cn/baseimages/python:3.11-slim

# 设置时区为中国时区
RUN apt-get update && \
    apt-get install -y wget tzdata && \
    rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 设置工作目录
WORKDIR /app

# 安装 poetry，配置不创建虚拟环境（直接使用系统 Python）
RUN pip install --no-cache-dir poetry && \
    poetry config virtualenvs.create false && \
    poetry config virtualenvs.in-project false

# 复制项目代码
COPY . .

# 安装依赖到系统环境
RUN poetry install

# 添加启动脚本权限
RUN chmod +x start.sh

# 启动处理器服务
CMD ["./start.sh"]

