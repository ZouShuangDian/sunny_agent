"""
测试 LLM 模型实际上下文窗口大小。

策略：用二分法逐步增加 prompt token 数，找到能正常返回的最大值。
运行：poetry run python scripts/test_context_limit.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / "app" / ".env")

import litellm

# 从环境变量读取配置
API_KEY = os.getenv("LLM_API_KEY")
API_BASE = os.getenv("LLM_API_BASE")
MODEL = os.getenv("LLM_DEFAULT_MODEL", "openai/deepseek-ai/DeepSeek-V3")

print(f"模型: {MODEL}")
print(f"API Base: {API_BASE}")
print(f"API Key: {API_KEY[:8]}..." if API_KEY else "API Key: 未设置")
print()

# 用重复文本填充到目标 token 数（中文约 1 字 ≈ 1-2 token，英文 1 word ≈ 1 token）
# 这里用英文单词更可预测：约 4 字符 = 1 token
FILLER_WORD = "hello "  # 约 1.5 token per repetition


def make_prompt(target_tokens: int) -> str:
    """生成大约 target_tokens 个 token 的 prompt"""
    # 粗估：每 6 字符约 1.5 token → 每 token 约 4 字符
    char_count = target_tokens * 4
    repeat_count = char_count // len(FILLER_WORD)
    filler = FILLER_WORD * repeat_count
    return f"请回复'OK'。以下是填充内容，忽略即可：\n{filler}"


def test_tokens(target_tokens: int) -> tuple[bool, dict]:
    """测试指定 token 数是否能正常返回"""
    prompt = make_prompt(target_tokens)
    try:
        response = litellm.completion(
            model=MODEL,
            api_key=API_KEY,
            api_base=API_BASE,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            timeout=30,
        )
        usage = response.usage
        return True, {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        }
    except Exception as e:
        return False, {"error": str(e)[:200]}


# 二分法测试
print("=" * 60)
print("  二分法测试上下文窗口大小")
print("=" * 60)

# 先测几个关键节点
checkpoints = [8_000, 32_000, 64_000, 96_000, 128_000, 160_000]

print("\n--- 阶段 1：关键节点探测 ---")
max_success = 0
min_fail = None

for target in checkpoints:
    ok, info = test_tokens(target)
    status = "✅" if ok else "❌"
    actual = info.get("prompt_tokens", "?")
    print(f"  {status} 目标 {target:>8,} tokens | 实际 prompt_tokens={actual} | {info}")
    if ok:
        max_success = max(max_success, info["prompt_tokens"])
    else:
        if min_fail is None:
            min_fail = target
        break

if min_fail is None:
    print(f"\n所有检查点都通过！上下文窗口 >= {checkpoints[-1]:,} tokens")
else:
    # 二分法精确定位
    print(f"\n--- 阶段 2：二分法精确定位 ({max_success:,} ~ {min_fail:,}) ---")
    low = max_success
    high = min_fail

    for i in range(8):  # 最多 8 轮二分
        mid = (low + high) // 2
        if mid == low or mid == high:
            break
        ok, info = test_tokens(mid)
        status = "✅" if ok else "❌"
        actual = info.get("prompt_tokens", "?")
        print(f"  {status} 目标 {mid:>8,} tokens | 实际 prompt_tokens={actual} | {info}")
        if ok:
            low = info["prompt_tokens"]
        else:
            high = mid

    print(f"\n{'=' * 60}")
    print(f"  结论：上下文窗口上限约 {low:,} ~ {high:,} prompt tokens")
    print(f"  当前配置 MODEL_CONTEXT_LIMIT = 98,304")
    print(f"{'=' * 60}")
