"""
Sandbox Service — 隔离的代码执行环境管理服务

每个 session 对应一个独立的 Docker 容器：
- Session 首次调用 /exec 时，自动创建容器
- 同一 session 的多次 /exec 共享同一容器（pip install 等状态保留）
- DELETE /session/{id} 销毁容器，清空所有状态
- TTL 超时（默认 30 分钟）自动回收容器

启动方式（挂载宿主机 Docker socket）：
  docker run -d \
    --name sandbox-service \
    -p 8020:8020 \
    -v /var/run/docker.sock:/var/run/docker.sock \
    sandbox-service
"""

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any

import docker
import structlog
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

log = structlog.get_logger()

SESSION_TTL_SECONDS = 30 * 60

import os
SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "python:3.11-slim")
SANDBOX_VOLUME_HOST = os.getenv("SANDBOX_VOLUME_HOST", "/Users/zoushuangdian/docker/volumes/sunny_agent")
SANDBOX_VOLUME_CONTAINER = os.getenv("SANDBOX_VOLUME_CONTAINER", "/mnt")

_docker_client: docker.DockerClient | None = None
_containers: dict[str, docker.models.containers.Container] = {}
_last_active: dict[str, float] = {}
_cleanup_task: asyncio.Task | None = None


async def _cleanup_loop() -> None:
    """后台 TTL 清理：每 5 分钟扫描一次过期 session"""
    while True:
        await asyncio.sleep(300)
        now = time.time()
        expired = [
            key for key, last in list(_last_active.items())
            if now - last > SESSION_TTL_SECONDS
        ]
        for key in expired:
            parts = key.split(":")
            if len(parts) == 2:
                user_id, session_id = parts
                log.info("沙箱 TTL 过期，自动销毁", session_id=session_id[:8], user_id=user_id)
            await _destroy_session(key)


async def _destroy_session(container_key: str) -> None:
    """销毁 session 对应的容器"""
    container = _containers.pop(container_key, None)
    _last_active.pop(container_key, None)
    if container:
        try:
            await asyncio.to_thread(container.kill)
        except Exception:
            pass
        try:
            await asyncio.to_thread(container.remove, force=True)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _docker_client, _cleanup_task
    _docker_client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
    _cleanup_task = asyncio.create_task(_cleanup_loop())
    log.info("Sandbox Service 启动", image=SANDBOX_IMAGE)
    yield
    _cleanup_task.cancel()
    for key in list(_containers.keys()):
        await _destroy_session(key)
    await asyncio.to_thread(_docker_client.close)
    log.info("Sandbox Service 关闭，所有容器已清理")


app = FastAPI(title="Sandbox Service", lifespan=lifespan)


class ExecRequest(BaseModel):
    session_id: str
    user_id: str
    command: str
    timeout: int = 30


class ExecResponse(BaseModel):
    stdout: str
    stderr: str
    returncode: int


async def _get_or_create_container(session_id: str, user_id: str) -> docker.models.containers.Container:
    """获取已有容器，或为新 session 创建一个"""
    container_key = f"{user_id}:{session_id}"
    
    if container_key not in _containers:
        log.info("创建沙箱容器", session_id=session_id[:8], user_id=user_id, image=SANDBOX_IMAGE)
        
        user_volume_path = f"{SANDBOX_VOLUME_HOST}/users/{user_id}"
        
        def _create_container():
            return _docker_client.containers.run(
                image=SANDBOX_IMAGE,
                command=["sleep", "infinity"],
                name=f"sandbox_{user_id}_{session_id[:8]}",
                mem_limit="512m",
                cpu_period=100000,
                cpu_quota=100000,
                network_mode="bridge",
                volumes={
                    f"{SANDBOX_VOLUME_HOST}/skills": {"bind": f"{SANDBOX_VOLUME_CONTAINER}/skills", "mode": "ro"},
                    f"{user_volume_path}/uploads": {"bind": f"{SANDBOX_VOLUME_CONTAINER}/uploads", "mode": "ro"},
                    f"{user_volume_path}/outputs/{session_id}": {"bind": f"{SANDBOX_VOLUME_CONTAINER}/outputs", "mode": "rw"},
                },
                detach=True,
            )
        
        container = await asyncio.to_thread(_create_container)
        _containers[container_key] = container

    _last_active[container_key] = time.time()
    return _containers[container_key]


@app.post("/exec", response_model=ExecResponse)
async def exec_command(req: ExecRequest) -> ExecResponse:
    """
    在 session 对应的沙箱容器中执行 bash 命令。
    同一 session 的多次调用共享同一容器，pip install 等状态跨调用保留。
    """
    try:
        container = await _get_or_create_container(req.session_id, req.user_id)

        def _exec_command():
            exec_result = container.exec_run(
                cmd=["bash", "-c", req.command],
                stream=False,
                demux=True,
            )
            stdout, stderr = exec_result.output
            return (
                stdout.decode("utf-8", errors="replace") if stdout else "",
                stderr.decode("utf-8", errors="replace") if stderr else "",
                exec_result.exit_code,
            )

        stdout, stderr, returncode = await asyncio.wait_for(
            asyncio.to_thread(_exec_command),
            timeout=req.timeout,
        )

        log.debug(
            "命令执行完成",
            session_id=req.session_id[:8],
            returncode=returncode,
            command_preview=req.command[:80],
        )

        return ExecResponse(stdout=stdout, stderr=stderr, returncode=returncode)

    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=408,
            detail=f"命令执行超时（>{req.timeout}s）",
        )
    except Exception as e:
        log.error("命令执行失败", session_id=req.session_id[:8], error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/session/{user_id}/{session_id}")
async def destroy_session(user_id: str, session_id: str):
    """销毁 session 对应的容器，清空所有安装的包和临时文件。"""
    container_key = f"{user_id}:{session_id}"
    await _destroy_session(container_key)
    log.info("沙箱容器已销毁", session_id=session_id[:8], user_id=user_id)
    return {"ok": True}


@app.get("/health")
async def health():
    """健康检查，返回当前活跃 session 数量"""
    sessions_list = []
    for key in _containers:
        parts = key.split(":")
        if len(parts) == 2:
            user_id, session_id = parts
            sessions_list.append({"user_id": user_id, "session_id": session_id[:8]})
    return {
        "status": "ok",
        "active_sessions": len(_containers),
        "sessions": sessions_list,
    }
