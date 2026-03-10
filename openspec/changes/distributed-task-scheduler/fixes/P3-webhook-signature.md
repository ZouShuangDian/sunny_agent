# P3 修复：Webhook 签名强制化

## 问题
当前设计 Webhook 签名是"可选"，缺少默认强制。

## 修复后：强制签名验证

```python
# app/scheduler/webhook.py

import hmac
import hashlib
import json
from datetime import datetime, timedelta
from typing import Optional
import httpx
import structlog

logger = structlog.get_logger()

class WebhookSigner:
    """Webhook 签名生成和验证"""
    
    @staticmethod
    def generate_signature(payload: str, secret: str, timestamp: str) -> str:
        """
        生成 HMAC-SHA256 签名
        
        格式: t={timestamp},v1={signature}
        """
        signed_payload = f"{timestamp}.{payload}"
        signature = hmac.new(
            secret.encode('utf-8'),
            signed_payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    @staticmethod
    def verify_signature(payload: str, signature_header: str, secret: str) -> bool:
        """验证签名"""
        try:
            # 解析 header: "t=1234567890,v1=abc123..."
            parts = signature_header.split(',')
            timestamp = None
            signature = None
            
            for part in parts:
                key, value = part.split('=')
                if key == 't':
                    timestamp = value
                elif key == 'v1':
                    signature = value
            
            if not timestamp or not signature:
                return False
            
            # 检查时间戳（防重放攻击，5 分钟窗口）
            ts = int(timestamp)
            now = int(datetime.utcnow().timestamp())
            if abs(now - ts) > 300:  # 5 分钟
                logger.warning("Webhook timestamp too old", timestamp=timestamp)
                return False
            
            # 验证签名
            expected = WebhookSigner.generate_signature(payload, secret, timestamp)
            return hmac.compare_digest(signature, expected)
            
        except Exception as e:
            logger.error("Failed to verify webhook signature", error=str(e))
            return False


class WebhookService:
    """Webhook 发送服务"""
    
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
    
    async def send_webhook(
        self,
        url: str,
        payload: dict,
        secret: str,  # 🔴 强制要求
        max_retries: int = 3
    ) -> bool:
        """
        发送带签名的 Webhook
        
        Args:
            url: Webhook URL
            payload: 消息体
            secret: 签名密钥（强制）
            max_retries: 最大重试次数
        """
        if not secret:
            raise ValueError("Webhook secret is required for signature")
        
        # 准备 payload
        payload_str = json.dumps(payload, separators=(',', ':'))
        timestamp = str(int(datetime.utcnow().timestamp()))
        
        # 生成签名
        signature = WebhookSigner.generate_signature(payload_str, secret, timestamp)
        
        headers = {
            'Content-Type': 'application/json',
            'X-Webhook-Signature': f"t={timestamp},v1={signature}",
            'X-Webhook-Timestamp': timestamp,
            'X-Webhook-Id': payload.get('execution', {}).get('id', 'unknown')
        }
        
        # 重试发送
        for attempt in range(max_retries):
            try:
                response = await self.client.post(
                    url,
                    content=payload_str,
                    headers=headers
                )
                
                if response.status_code < 500:
                    # 2xx/4xx 表示请求已到达，不再重试
                    if 200 <= response.status_code < 300:
                        logger.info("Webhook sent successfully", 
                                  url=url, 
                                  attempt=attempt + 1)
                        return True
                    else:
                        logger.warning("Webhook returned error", 
                                     url=url, 
                                     status=response.status_code,
                                     response=response.text[:200])
                        return False
                
                # 5xx 错误，继续重试
                logger.warning("Webhook server error, will retry", 
                             url=url, 
                             status=response.status_code,
                             attempt=attempt + 1)
                
            except Exception as e:
                logger.error("Webhook send failed", 
                           url=url, 
                           error=str(e),
                           attempt=attempt + 1)
            
            # 指数退避重试
            if attempt < max_retries - 1:
                delay = (2 ** attempt) * 5  # 5s, 10s, 20s
                await asyncio.sleep(delay)
        
        logger.error("Webhook failed after all retries", url=url)
        return False
    
    async def close(self):
        await self.client.aclose()


# Webhook Payload 格式（带签名）
"""
POST {webhook_url}
Content-Type: application/json
X-Webhook-Signature: t=1705312800,v1=a1b2c3d4e5f6...
X-Webhook-Timestamp: 1705312800
X-Webhook-Id: execution-uuid

{
    "event": "task.failed",
    "timestamp": "2024-01-15T09:00:00Z",
    "task": {
        "id": "task-uuid",
        "name": "每日报表",
        "cron_expression": "0 9 * * *"
    },
    "execution": {
        "id": "execution-uuid",
        "scheduled_time": "2024-01-15T09:00:00Z",
        "failed_at": "2024-01-15T09:05:00Z",
        "retry_count": 3,
        "max_retries": 3
    },
    "error": {
        "type": "TimeoutError",
        "message": "Chat API timeout after 300s"
    }
}
"""

# 使用示例
async def on_task_failed(execution_id: str, task_id: str):
    """任务失败时发送 Webhook"""
    
    # 查询任务和 Webhook 配置
    task = await get_task(task_id)
    
    if not task.webhook_url:
        logger.info("No webhook configured, skipping", task_id=task_id)
        return
    
    # 🔴 强制检查 secret
    if not task.webhook_secret:
        logger.error("Webhook secret is required but not set", task_id=task_id)
        raise ValueError(f"Task {task_id} has webhook_url but no webhook_secret")
    
    # 构建 payload
    payload = {
        "event": "task.failed",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "task": {
            "id": str(task.id),
            "name": task.name,
            "cron_expression": task.cron_expression
        },
        "execution": {
            "id": execution_id,
            "scheduled_time": execution.scheduled_time.isoformat(),
            "failed_at": datetime.utcnow().isoformat() + "Z",
            "retry_count": execution.retry_count,
            "max_retries": execution.max_retries
        },
        "error": {
            "type": type(error).__name__,
            "message": str(error)
        }
    }
    
    # 发送（强制签名）
    service = WebhookService()
    try:
        success = await service.send_webhook(
            url=task.webhook_url,
            payload=payload,
            secret=task.webhook_secret,  # 🔴 强制传入
            max_retries=3
        )
        
        if success:
            await update_webhook_status(execution_id, "sent")
        else:
            await update_webhook_status(execution_id, "failed")
            
    finally:
        await service.close()


# 接收方验证示例（Python Flask）
from flask import Flask, request

app = Flask(__name__)
WEBHOOK_SECRET = "your-secret-key"

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    signature_header = request.headers.get('X-Webhook-Signature')
    payload = request.get_data(as_text=True)
    
    if not WebhookSigner.verify_signature(payload, signature_header, WEBHOOK_SECRET):
        return "Invalid signature", 401
    
    data = request.get_json()
    # 处理 webhook...
    return "OK", 200
```

## API 更新

```python
# 创建任务时强制要求 webhook_secret（如果提供了 webhook_url）

class CreateTaskRequest(BaseModel):
    name: str
    cron_expression: str
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    
    @validator('webhook_secret')
    def webhook_secret_required_if_url(cls, v, values):
        if values.get('webhook_url') and not v:
            raise ValueError('webhook_secret is required when webhook_url is provided')
        return v
```

## 安全最佳实践

1. **强制 HTTPS**: Webhook URL 必须是 https://
2. **签名验证**: 接收方必须验证签名
3. **时间戳检查**: 防重放攻击（5 分钟窗口）
4. **IP 白名单**: 生产环境限制 Webhook 源 IP
5. **密钥轮换**: 支持定期更换 webhook_secret
