# 飞书卡片状态更新功能

通过飞书流式卡片 API 实现机器人工作状态实时展示，提升用户交互体验。

## 功能说明

### 状态流转

```
收到消息 → ⏳ 思考中 → 🔍 校验中 → 🤖 生成答案中 → ✅ 发送答案
```

### 状态说明

| 状态 | 显示文本 | 说明 |
|------|---------|------|
| THINKING | ⏳ 思考中... | 收到消息后立即显示 |
| VALIDATING | 🔍 校验中... | 校验用户输入和上下文 |
| GENERATING | 🤖 生成答案中... | 调用大模型生成答案 |
| COMPLETED | ✅ 已完成 | 关闭流式模式，发送答案 |
| ERROR | ❌ 出错了 | 发生错误时显示 |

## 快速开始

### 1. 基础使用

```python
from app.feishu.card_status_manager import (
    CardStatusManager,
    CardStatus,
    get_card_status_manager,
    cleanup_card_status_manager,
)

async def handle_message(open_id: str, chat_id: str, receive_id: str):
    # 获取管理器
    manager = await get_card_status_manager(
        open_id=open_id,
        chat_id=chat_id,
    )
    
    try:
        # 1. 开始会话（立即显示"⏳ 思考中..."）
        await manager.start_session(
            open_id=open_id,
            chat_id=chat_id,
            receive_id=receive_id,
        )
        
        # 2. 校验阶段（1 秒）
        await manager.update_status(CardStatus.VALIDATING)
        await asyncio.sleep(1)
        
        # 3. 调用大模型（5 秒）
        await manager.update_status(CardStatus.GENERATING)
        answer = await call_llm()  # 你的大模型调用
        
        # 4. 完成并发送答案
        await manager.complete(answer)
        
    except Exception as e:
        await manager.set_error(str(e))
    
    finally:
        await cleanup_card_status_manager(open_id, chat_id)
```

### 2. 自定义状态文本

```python
# 使用自定义文本代替默认状态
await manager.update_status(
    CardStatus.GENERATING,
    custom_text="📊 正在分析数据...",
)

await manager.update_status(
    CardStatus.GENERATING,
    custom_text="📈 生成图表中...",
)
```

### 3. 错误处理

```python
try:
    # 处理逻辑
    await process_user_request()
except Exception as e:
    # 显示错误状态
    await manager.set_error(f"处理失败：{str(e)}")
```

## 完整示例

### 消息处理流程

```python
import asyncio
from app.feishu.card_status_manager import (
    CardStatus,
    get_card_status_manager,
    cleanup_card_status_manager,
)
from app.llm.client import call_llm  # 假设的大模型客户端

async def handle_user_message(
    open_id: str,
    chat_id: str,
    receive_id: str,
    user_message: str,
):
    """处理用户消息的完整流程"""
    
    manager = await get_card_status_manager(
        open_id=open_id,
        chat_id=chat_id,
    )
    
    try:
        # Step 1: 创建卡片（显示"⏳ 思考中..."）
        session = await manager.start_session(
            open_id=open_id,
            chat_id=chat_id,
            receive_id=receive_id,
            title="Sunny Agent",  # 可选：卡片标题
        )
        
        logger.info("Received message", message=user_message)
        
        # Step 2: 校验阶段
        await manager.update_status(CardStatus.VALIDATING)
        
        # 校验逻辑（示例）
        await validate_user_input(user_message)
        await asyncio.sleep(0.5)  # 模拟校验耗时
        
        # Step 3: 调用大模型
        await manager.update_status(CardStatus.GENERATING)
        
        # 调用大模型 API
        answer = await call_llm(
            prompt=user_message,
            context=build_context(open_id, chat_id),
        )
        
        # Step 4: 发送答案
        await manager.complete(
            final_answer=answer,
            send_as_message=True,  # True: 作为普通消息发送
        )
        
        logger.info("Message handled successfully",
                   card_id=session.card_id,
                   answer_length=len(answer))
        
    except Exception as e:
        logger.error("Failed to handle message", error=str(e))
        await manager.set_error(f"处理失败：{str(e)}")
    
    finally:
        await cleanup_card_status_manager(open_id, chat_id)
```

## API 参考

### CardStatusManager

#### `start_session()`

开始卡片会话，创建并发送初始卡片。

```python
async def start_session(
    open_id: str,
    chat_id: str,
    receive_id: str,
    receive_id_type: str = "open_id",
    title: str = None,  # 卡片标题
) -> CardSession
```

**返回**: `CardSession` 对象

#### `update_status()`

更新卡片状态。

```python
async def update_status(
    status: CardStatus,
    custom_text: str = None,  # 自定义显示文本
) -> bool
```

**返回**: 是否更新成功

#### `complete()`

完成卡片会话，发送最终答案。

```python
async def complete(
    final_answer: str,
    send_as_message: bool = True,  # 是否作为普通消息发送
) -> bool
```

**返回**: 是否完成成功

#### `set_error()`

设置错误状态。

```python
async def set_error(
    error_message: str,
) -> bool
```

**返回**: 是否设置成功

#### `get_session()`

获取当前会话信息。

```python
def get_session() -> Optional[CardSession]
```

### CardStatus 枚举

```python
class CardStatus(Enum):
    THINKING = "thinking"      # ⏳ 思考中...
    VALIDATING = "validating"  # 🔍 校验中...
    GENERATING = "generating"  # 🤖 生成答案中...
    COMPLETED = "completed"    # ✅ 已完成
    ERROR = "error"            # ❌ 出错了
```

### CardSession 数据类

```python
@dataclass
class CardSession:
    card_id: str              # 卡片 ID
    message_id: str           # 消息 ID
    status: CardStatus        # 当前状态
    open_id: str              # 用户 ID
    chat_id: str              # 会话 ID
    receive_id: str           # 接收者 ID
    created_at: float         # 创建时间戳
    updated_at: float         # 更新时间戳
    sequence: int             # 更新序列号
    error_message: str | None # 错误信息
```

## 配置

### 环境变量

无需额外配置，复用现有的飞书应用配置。

### 数据库配置

确保 `feishu_access_config` 表中有应用配置：

```sql
INSERT INTO feishu_access_config (
    app_id, 
    app_secret, 
    is_active
) VALUES (
    'cli_xxx', 
    'secret_xxx', 
    true
);
```

## 最佳实践

### 1. 及时清理会话

```python
try:
    # 处理逻辑
    await process()
finally:
    await cleanup_card_status_manager(open_id, chat_id)
```

### 2. 合理的状态更新频率

- 避免频繁更新（建议间隔 > 500ms）
- 使用 `custom_text` 提供有意义的进度信息

### 3. 错误处理

```python
try:
    await process()
except Exception as e:
    await manager.set_error(f"具体错误：{str(e)}")
```

### 4. 日志记录

```python
logger.info("Card status updated",
           card_id=session.card_id,
           status=status.value)
```

## 测试

### 运行单元测试

```bash
pytest tests/feishu/test_card_status.py -v
```

### 运行示例

```bash
python examples/feishu_card_status_example.py
```

## 故障排查

### 卡片不更新

1. 检查 `card_id` 是否正确
2. 检查飞书应用权限（卡片套件权限）
3. 查看日志中的错误信息

### 状态更新顺序混乱

- 使用 `sequence` 参数确保顺序
- 避免并发更新同一卡片

### 最终答案未发送

- 检查 `send_as_message` 参数
- 确认消息发送权限

## 相关文档

- [飞书卡片套件 API](https://open.feishu.cn/document/cardkit-v1/feishu-card-resource-overview)
- [流式卡片更新](https://open.feishu.cn/document/cardkit-v1/streaming-card)
- [消息回复 API](https://open.feishu.cn/document/server-docs/im-v1/message/reply)
