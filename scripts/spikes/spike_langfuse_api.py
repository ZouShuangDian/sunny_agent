"""
Spike 3: 验证 Langfuse v3 /api/public/metrics/daily 和 /api/public/metrics/usage 端点可用性

运行前提:
  Langfuse 服务已启动且有 Trace 数据

运行方式:
  poetry run python scripts/spikes/spike_langfuse_api.py

验证项:
  - GET /api/public/metrics/daily 是否存在且返回预期格式
  - GET /api/public/metrics/usage 是否存在且返回预期格式
  - 如不存在，GET /api/public/traces 是否包含 token/cost 字段
"""

import asyncio
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


async def main():
    import httpx

    host = os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "pk-lf-sunny-dev")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "sk-lf-sunny-dev")

    # Basic Auth: public_key:secret_key
    auth_str = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    headers = {"Authorization": f"Basic {auth_str}"}

    print("=== Spike 3: Langfuse v3 API Availability ===\n")
    print(f"Host: {host}")

    async with httpx.AsyncClient(timeout=10) as client:
        # Test health first
        try:
            resp = await client.get(f"{host}/api/public/health")
            print(f"Health: {resp.status_code} → {resp.json()}")
        except Exception as e:
            print(f"❌ Health check failed: {e}")
            print("Ensure Langfuse is running.")
            return

        # Test /api/public/metrics/daily
        print("\n--- /api/public/metrics/daily ---")
        try:
            resp = await client.get(f"{host}/api/public/metrics/daily", headers=headers)
            print(f"Status: {resp.status_code}")
            if resp.status_code == 200:
                print(f"✅ Available: {resp.json()}")
            else:
                print(f"⚠️ Not available (status {resp.status_code}): {resp.text[:200]}")
        except Exception as e:
            print(f"❌ Error: {e}")

        # Test /api/public/metrics/usage
        print("\n--- /api/public/metrics/usage ---")
        try:
            resp = await client.get(f"{host}/api/public/metrics/usage", headers=headers)
            print(f"Status: {resp.status_code}")
            if resp.status_code == 200:
                print(f"✅ Available: {resp.json()}")
            else:
                print(f"⚠️ Not available (status {resp.status_code}): {resp.text[:200]}")
        except Exception as e:
            print(f"❌ Error: {e}")

        # Fallback: Test /api/public/traces
        print("\n--- /api/public/traces (fallback) ---")
        try:
            resp = await client.get(
                f"{host}/api/public/traces",
                headers=headers,
                params={"limit": 1},
            )
            print(f"Status: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                traces = data.get("data", [])
                if traces:
                    trace = traces[0]
                    print(f"✅ Trace fields: {list(trace.keys())}")
                    has_tokens = any(k for k in trace.keys() if "token" in k.lower())
                    has_cost = any(k for k in trace.keys() if "cost" in k.lower())
                    print(f"  Has token fields: {has_tokens}")
                    print(f"  Has cost fields: {has_cost}")
                else:
                    print("  No traces found. Send a chat message first.")
            else:
                print(f"❌ Failed: {resp.text[:200]}")
        except Exception as e:
            print(f"❌ Error: {e}")

    print("\n=== Spike 3 Complete ===")


if __name__ == "__main__":
    asyncio.run(main())
