"""
飞书卡片状态更新示例

演示如何使用 CardStatusManager 实现分阶段状态更新：
1. 思考中 → 2.校验中 → 3.生成答案中 → 4.发送答案
"""

import asyncio
import time

import structlog

from app.feishu.card_status_manager import (
    CardStatusManager,
    CardStatus,
    get_card_status_manager,
    cleanup_card_status_manager,
)
from app.feishu.block_streaming import get_block_streaming_manager
from app.feishu.client import get_feishu_client

logger = structlog.get_logger()


async def example_basic_usage():
    """
    基础使用示例
    
    展示完整的状态流转流程
    """
    print("=" * 60)
    print("飞书卡片状态更新示例")
    print("=" * 60)
    
    # 模拟的用户信息
    open_id = "ou_xxx"
    chat_id = "oc_xxx"
    receive_id = open_id
    
    try:
        # 1. 获取管理器
        manager = await get_card_status_manager(
            open_id=open_id,
            chat_id=chat_id,
        )
        
        # 2. 开始会话（立即显示"⏳ 思考中..."）
        print("\n📱 创建卡片会话...")
        session = await manager.start_session(
            open_id=open_id,
            chat_id=chat_id,
            receive_id=receive_id,
        )
        print(f"   卡片 ID: {session.card_id}")
        print(f"   消息 ID: {session.message_id}")
        print(f"   初始状态：{session.status.value}")
        
        # 3. 模拟校验阶段（1 秒）
        print("\n🔍 进入校验阶段...")
        await manager.update_status(CardStatus.VALIDATING)
        await asyncio.sleep(1)  # 模拟校验耗时
        
        # 4. 模拟大模型调用（5 秒）
        print("\n🤖 调用大模型...")
        await manager.update_status(CardStatus.GENERATING)
        await asyncio.sleep(5)  # 模拟 LLM 调用
        
        # 5. 完成并发送答案
        print("\n✅ 发送最终答案...")
        final_answer = """
这是大模型生成的答案：

**问题分析：**
用户询问了关于飞书卡片状态更新的问题。

**解决方案：**
1. 使用 CardStatusManager 管理状态
2. 分阶段更新卡片内容
3. 最终通过普通消息发送答案

**代码示例：**
```python
manager = await get_card_status_manager(open_id, chat_id)
await manager.start_session(open_id, chat_id, receive_id)
await manager.update_status(CardStatus.VALIDATING)
answer = await call_llm()
await manager.complete(answer)
```

希望这能帮到你！😊
"""
        await manager.complete(final_answer.strip())
        
        print("\n✨ 完成！")
        
    except Exception as e:
        logger.error("Example failed", error=str(e))
        print(f"\n❌ 示例执行失败：{e}")
    
    finally:
        # 清理
        await cleanup_card_status_manager(open_id, chat_id)
        print("\n🧹 已清理会话")


async def example_error_handling():
    """
    错误处理示例
    
    展示如何在出错时更新卡片状态
    """
    print("\n" + "=" * 60)
    print("错误处理示例")
    print("=" * 60)
    
    open_id = "ou_error_test"
    chat_id = "oc_error_test"
    
    try:
        manager = await get_card_status_manager(
            open_id=open_id,
            chat_id=chat_id,
        )
        
        # 开始会话
        print("\n📱 创建卡片会话...")
        await manager.start_session(
            open_id=open_id,
            chat_id=chat_id,
            receive_id=open_id,
        )
        
        # 模拟校验失败
        print("\n🔍 校验中...")
        await manager.update_status(CardStatus.VALIDATING)
        await asyncio.sleep(1)
        
        # 模拟错误
        print("\n❌ 模拟错误...")
        await manager.set_error("数据库连接失败")
        
    except Exception as e:
        logger.error("Error example failed", error=str(e))
    
    finally:
        await cleanup_card_status_manager(open_id, chat_id)


async def example_custom_status_text():
    """
    自定义状态文本示例
    
    展示如何使用自定义的状态显示文本
    """
    print("\n" + "=" * 60)
    print("自定义状态文本示例")
    print("=" * 60)
    
    open_id = "ou_custom_test"
    chat_id = "oc_custom_test"
    
    try:
        manager = await get_card_status_manager(
            open_id=open_id,
            chat_id=chat_id,
        )
        
        # 开始会话
        await manager.start_session(
            open_id=open_id,
            chat_id=chat_id,
            receive_id=open_id,
            title="自定义状态示例",
        )
        
        # 使用自定义文本
        print("\n📊 使用自定义状态文本...")
        await manager.update_status(
            CardStatus.GENERATING,
            custom_text="📊 正在分析数据...",
        )
        await asyncio.sleep(2)
        
        await manager.update_status(
            CardStatus.GENERATING,
            custom_text="📈 生成图表中...",
        )
        await asyncio.sleep(2)
        
        # 完成
        await manager.complete("数据分析完成！图表已生成。")
        
    except Exception as e:
        logger.error("Custom text example failed", error=str(e))
    
    finally:
        await cleanup_card_status_manager(open_id, chat_id)


async def main():
    """运行所有示例"""
    try:
        # 基础使用示例
        await example_basic_usage()
        
        # 等待一下
        await asyncio.sleep(2)
        
        # 错误处理示例
        await example_error_handling()
        
        # 等待一下
        await asyncio.sleep(2)
        
        # 自定义文本示例
        await example_custom_status_text()
        
    except Exception as e:
        logger.error("All examples failed", error=str(e))
        print(f"❌ 所有示例执行失败：{e}")
    
    print("\n" + "=" * 60)
    print("所有示例执行完毕")
    print("=" * 60)


if __name__ == "__main__":
    # 注意：此示例需要配置飞书 app_id 和 app_secret
    # 运行前请确保：
    # 1. 在数据库 feishu_access_config 表中添加了应用配置
    # 2. 或者设置环境变量 FEISHU_APP_ID 和 FEISHU_APP_SECRET
    
    print("⚠️ 注意：此示例需要真实的飞书应用配置")
    print("   请先配置环境变量或在数据库中配置飞书应用信息")
    print()
    
    # asyncio.run(main())
