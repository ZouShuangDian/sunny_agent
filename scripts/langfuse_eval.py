"""
Langfuse Experiment 评估脚本模板

使用方式:
  1. 在 Langfuse UI 中创建 Dataset，添加测试用例（input + expected_output）
  2. 配置环境变量（LANGFUSE_*, SUNNY_AGENT_URL, SUNNY_AGENT_TOKEN）
  3. 运行: poetry run python scripts/langfuse_eval.py

流程:
  - 从 Langfuse Dataset 读取测试用例
  - 调用 SunnyAgent /api/chat 接口
  - 使用 LLM-as-a-Judge 评估结果质量
  - 将评分记录到 Langfuse Experiment
"""

import asyncio
import os

import httpx
from langfuse import Langfuse


async def run_evaluation(
    dataset_name: str = "default",
    sunny_agent_url: str | None = None,
    sunny_agent_token: str | None = None,
) -> dict:
    """
    运行评估流程。

    Args:
        dataset_name: Langfuse Dataset 名称
        sunny_agent_url: SunnyAgent API 地址
        sunny_agent_token: SunnyAgent Bearer Token

    Returns:
        dict with evaluation results summary
    """
    url = sunny_agent_url or os.environ.get("SUNNY_AGENT_URL", "http://localhost:8000")
    token = sunny_agent_token or os.environ.get("SUNNY_AGENT_TOKEN", "")

    # 初始化 Langfuse 客户端
    langfuse = Langfuse()

    # 获取 Dataset
    dataset = langfuse.get_dataset(dataset_name)
    items = dataset.items

    results = []
    async with httpx.AsyncClient(timeout=120) as client:
        for item in items:
            user_input = item.input if isinstance(item.input, str) else str(item.input)

            # 调用 SunnyAgent /api/chat
            try:
                response = await client.post(
                    f"{url}/api/chat",
                    json={"message": user_input},
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                data = response.json()
                actual_output = data.get("data", {}).get("reply", "")
            except Exception as e:
                actual_output = f"ERROR: {e}"

            # 记录到 Langfuse Experiment
            trace = langfuse.trace(
                name="eval_run",
                metadata={"dataset": dataset_name, "item_id": str(item.id)},
            )
            trace.generation(
                name="chat_response",
                input=user_input,
                output=actual_output,
            )

            # TODO: 添加 LLM-as-a-Judge 评分
            # score = await _judge(user_input, item.expected_output, actual_output)
            # trace.score(name="quality", value=score)

            results.append(
                {
                    "input": user_input,
                    "expected": item.expected_output,
                    "actual": actual_output,
                }
            )

    langfuse.flush()

    return {
        "dataset": dataset_name,
        "total_items": len(items),
        "results": results,
    }


if __name__ == "__main__":
    import sys

    ds_name = sys.argv[1] if len(sys.argv) > 1 else "default"
    result = asyncio.run(run_evaluation(dataset_name=ds_name))
    print(f"Evaluation complete: {result['total_items']} items processed")
