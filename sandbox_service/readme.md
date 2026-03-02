cd /Users/zoushuangdian/docker/compose/sandbox_service

# 1. 构建沙箱运行时镜像（只需执行一次）
docker build -f sandbox.Dockerfile -t sunny-sandbox:latest .

# 2. 启动 sandbox-service
docker-compose up -d

# 3. 验证服务是否启动成功
docker ps
curl http://localhost:8020/health