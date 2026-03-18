"""
Spike 1: 验证 LiteLLM Langfuse Callback 与 acompletion(stream=True) + async generator 兼容性

运行前提:
  1. Langfuse 服务已启动 (docker compose -f infra/langfuse-compose.yml up -d)
  2. .env 中 LANGFUSE_* 配置正确

运行方式:
  poetry run python scripts/spikes/spike_litellm_callback.py

验证项:
  - litellm.acompletion(stream=True) + async for 消费是否正常
  - Langfuse 中是否生成完整的 Generation 记录（包含 model、total_tokens、cost）
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


async def main():
    import litellm

    # 配置 Langfuse callback
    litellm.success_callback = ["langfuse"]
    litellm.failure_callback = ["langfuse"]

    # 设置 Langfuse 环境变量（如果 .env 中未设置）
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", "pk-lf-sunny-dev")
    os.environ.setdefault("LANGFUSE_SECRET_KEY", "sk-lf-sunny-dev")
    os.environ.setdefault("LANGFUSE_HOST", "http://localhost:3000")

    print("=== Spike 1: LiteLLM Langfuse Callback ===")
    print(f"LANGFUSE_HOST: {os.environ.get('LANGFUSE_HOST')}")

    try:
        # 使用 stream=True 调用
        response = await litellm.acompletion(
            model=os.environ.get("LLM_DEFAULT_MODEL", "openai/deepseek-ai/DeepSeek-V3"),
            messages=[{"role": "user", "content": "Say hello in one word."}],
            stream=True,
            api_key=os.environ.get("LLM_API_KEY", ""),
            api_base=os.environ.get("LLM_API_BASE"),
        )

        full_response = ""
        async for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                full_response += chunk.choices[0].delta.content

        print(f"Response: {full_response}")
        print("✅ Stream consumption completed successfully")

        # 等待 Langfuse flush
        await asyncio.sleep(3)
        print("✅ Langfuse flush waited. Check Langfuse UI for Generation record.")

    except Exception as e:
        print(f"❌ Error: {e}")
        print("If LLM not available, that's OK. Key test is callback config doesn't crash.")


if __name__ == "__main__":
    asyncio.run(main())
