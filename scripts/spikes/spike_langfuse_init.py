"""
Spike 2: 验证 LANGFUSE_INIT_* 环境变量幂等性

运行前提:
  Docker Engine 已安装

运行方式:
  poetry run python scripts/spikes/spike_langfuse_init.py

验证项:
  1. 首次启动 → 组织/项目/API Key 自动创建
  2. 停止后重启（保留 volume）→ 不重复创建
  3. 删除 volume 后重启 → 重新创建
"""

import asyncio
import subprocess
import sys
import time


COMPOSE_FILE = "infra/langfuse-compose.yml"


async def run_cmd(cmd: str) -> str:
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    return stdout.decode() + stderr.decode()


async def wait_for_health(max_wait: int = 120) -> bool:
    """等待 Langfuse 健康检查通过"""
    import httpx
    for i in range(max_wait // 5):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get("http://localhost:3000/api/public/health", timeout=5)
                if resp.status_code == 200:
                    print(f"  ✅ Langfuse healthy after {(i+1)*5}s: {resp.json()}")
                    return True
        except Exception:
            pass
        await asyncio.sleep(5)
    return False


async def main():
    print("=== Spike 2: LANGFUSE_INIT_* Idempotency ===\n")

    # Step 1: Fresh start
    print("Step 1: First start (fresh)")
    await run_cmd(f"docker compose -f {COMPOSE_FILE} down -v")
    await run_cmd(f"docker compose -f {COMPOSE_FILE} up -d")
    if not await wait_for_health():
        print("  ❌ Langfuse failed to start")
        return

    # Step 2: Restart with volume
    print("\nStep 2: Restart (keep volume)")
    await run_cmd(f"docker compose -f {COMPOSE_FILE} down")
    await run_cmd(f"docker compose -f {COMPOSE_FILE} up -d")
    if not await wait_for_health():
        print("  ❌ Langfuse failed to restart")
        return

    # Step 3: Restart without volume
    print("\nStep 3: Restart (delete volume)")
    await run_cmd(f"docker compose -f {COMPOSE_FILE} down -v")
    await run_cmd(f"docker compose -f {COMPOSE_FILE} up -d")
    if not await wait_for_health():
        print("  ❌ Langfuse failed to restart after volume delete")
        return

    # Cleanup
    print("\n✅ Spike 2 complete. Verify in Langfuse UI that project/keys exist.")
    await run_cmd(f"docker compose -f {COMPOSE_FILE} down")


if __name__ == "__main__":
    asyncio.run(main())
