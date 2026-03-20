# 飞书 ARQ 集成架构流程图

> 用于领导汇报的架构设计演示材料

---

## 一、整体架构全景图

```mermaid
graph TB
    subgraph "飞书平台"
        A[飞书用户] -->|发送消息| B[飞书机器人]
        B -->|推送事件| C[飞书开放平台]
    end

    subgraph "消息接收层"
        C -->|HTTPS POST| D[feishu-webhook<br/>FastAPI服务]
        D -->|验签解密| E[Security Layer]
        E -->|防重放检查| F[Replay Protection]
        F -->|LPUSH| G[Redis List<br/>feishu:webhook:queue]
    end

    subgraph "消息处理层"
        G -->|BRPOPLPUSH| H[message_transfer_loop<br/>长驻任务]
        H -->|幂等检查| I{已处理?}
        I -->|否| J[ARQ Queue<br/>arq:feishu:queue]
        I -->|是| K[跳过重复]
        J -->|消费| L[process_feishu_message<br/>ARQ Task]
    end

    subgraph "业务逻辑层"
        L -->|访问控制| M[AccessController]
        L -->|用户解析| N[UserResolver]
        L -->|消息防抖| O[DebounceManager]
        L -->|媒体下载| P[MediaDownloader]
        L -->|流式回复| Q[BlockStreaming]
    end

    subgraph "AI处理层"
        Q -->|调用| R[Agent Pipeline]
        R -->|生成回复| S[LLM]
        S -->|Token流| Q
    end

    subgraph "存储层"
        T[(PostgreSQL)]
        U[(Redis)]
        V[本地存储]
    end

    subgraph "回复层"
        Q -->|流式卡片| W[FeishuClient]
        W -->|实时更新| C
        C -->|显示| A
    end

    M -.->|读取配置| T
    N -.->|查询映射| T
    O -.->|状态存储| U
    P -.->|保存文件| V
    P -.->|元数据| T
    L -.->|审计日志| T

    style D fill:#e1f5fe
    style H fill:#fff3e0
    style L fill:#e8f5e9
    style R fill:#fce4ec
    style Q fill:#f3e5f5
```

---

## 二、消息流转时序图

```mermaid
sequenceDiagram
    participant User as 飞书用户
    participant Feishu as 飞书开放平台
    participant Webhook as feishu-webhook
    participant Redis as Redis
    participant Transfer as transfer_loop
    participant Worker as Feishu Worker
    participant ARQ as ARQ Queue
    participant Task as process_message
    participant FeishuClient as FeishuClient
    participant LLM as LLM

    User->>Feishu: 1. 发送消息
    Feishu->>Webhook: 2. 事件推送(含签名)
    
    rect rgb(225, 245, 254)
        Note over Webhook: 接收层处理
        Webhook->>Webhook: 2.1 签名验证
        Webhook->>Webhook: 2.2 解密 payload
        Webhook->>Webhook: 2.3 防重放检查
        Webhook->>Redis: 2.4 LPUSH 到队列
    end

    rect rgb(255, 243, 224)
        Note over Transfer: 消息转移层
        Transfer->>Redis: 3. BRPOPLPUSH 原子操作
        Transfer->>Transfer: 4. 幂等检查
        Transfer->>ARQ: 5. Enqueue 任务
    end

    rect rgb(232, 245, 233)
        Note over Worker,Task: 飞书Worker处理
        Worker->>ARQ: 6. 消费任务
        Worker->>Task: 7. 执行处理
        
        Task->>Task: 8. 访问控制检查
        Task->>Task: 9. 用户身份解析
        
        rect rgb(243, 229, 245)
            Note over Task: 防抖处理
            Task->>Redis: 10. 检查防抖状态
            Task->>Redis: 11. 重置定时器
            Task->>Task: 12. 合并消息
        end
        
        Task->>FeishuClient: 13. 创建流式卡片
        Task->>LLM: 14. 调用AI生成
        
        loop 实时流式更新
            LLM-->>Task: Token
            Task->>FeishuClient: 更新卡片
            FeishuClient->>Feishu: API调用
            Feishu->>User: 打字机效果
        end
        
        Task->>FeishuClient: 15. 关闭卡片
    end
```

---

## 三、双队列架构对比

```mermaid
graph LR
    subgraph "方案对比"
        direction TB
        
        subgraph "传统方案<br/>（不推荐）"
            A1[定时任务] -->|共享队列| B1[Worker]
            A2[飞书消息] -->|共享队列| B1
            B1 -->|资源竞争| C1[性能瓶颈]
        end
        
        subgraph "我们的方案<br/>（独立队列）"
            D1[定时任务] -->|arq:queue| E1[Cron Worker]
            D2[飞书消息] -->|arq:feishu:queue| E2[Feishu Worker]
            E1 -.->|互不干扰| E2
            E2 -.->|独立扩缩容| F1[弹性架构]
        end
    end

    style C1 fill:#ffcdd2
    style F1 fill:#c8e6c9
```

---

## 四、安全控制流程

```mermaid
graph TD
    A[飞书请求] --> B{签名验证}
    B -->|失败| C[返回401]
    B -->|成功| D{加密消息?}
    
    D -->|是| E[AES解密]
    D -->|否| F[解析Payload]
    E --> F
    
    F --> G{Event ID检查}
    G -->|重复| H[静默成功]
    G -->|新事件| I{时间窗口}
    
    I -->|超时| J[返回400]
    I -->|有效| K[生成幂等Key]
    
    K --> L{字段完整性检查}
    L -->|缺失关键字段| M[隔离队列]
    L -->|完整| N[进入处理队列]
    
    M --> O[告警通知]
    O --> P[人工处理]

    style C fill:#ffcdd2
    style J fill:#ffcdd2
    style M fill:#fff9c4
    style N fill:#c8e6c9
```

---

## 五、防抖机制流程

```mermaid
graph TB
    subgraph "Time Debounce<br/>时间防抖"
        A[新消息到达] --> B{检查会话状态}
        B -->|idle| C[创建定时器<br/>2秒]
        B -->|buffering| D[重置定时器<br/>累加消息]
        C --> E{定时器到期?}
        D --> E
        E -->|是| F[批量处理]
        E -->|否| G[继续等待]
    end

    subgraph "No-Text Debounce<br/>无文本防抖"
        H[媒体消息] --> I{包含文本?}
        I -->|否| J[启动No-Text定时器<br/>3秒]
        I -->|是| K[立即触发处理]
        J --> L{收到文本消息?}
        L -->|是| M[合并媒体+文本]
        L -->|否| N{超时?}
        N -->|是| O[仅处理媒体]
    end

    style F fill:#c8e6c9
    style K fill:#c8e6c9
    style M fill:#c8e6c9
```

---

## 六、BlockStreaming 流式回复

```mermaid
graph LR
    A[AI生成] -->|Token流| B{字符累积}
    
    B -->|达到min_chars| C{段落边界?}
    B -->|idle超时| C
    B -->|达到max_chars| D[强制Flush]
    
    C -->|是| E[立即Flush]
    C -->|否| B
    
    E --> F{第一块?}
    D --> F
    
    F -->|是| G[创建流式卡片]
    F -->|否| H{超长文本?}
    
    H -->|是| I[分块发送]
    H -->|否| G
    
    G --> J[实时更新卡片]
    I --> K[后续块普通消息]
    
    J --> L[打字机效果]
    K --> L

    style G fill:#e1f5fe
    style J fill:#e8f5e9
```

---

## 七、数据存储架构

```mermaid
graph TB
    subgraph "热存储 - Redis"
        A1[feishu:webhook:queue<br/>消息中转队列]
        A2[arq:feishu:queue<br/>ARQ任务队列]
        A3[feishu:buffer:*<br/>防抖缓冲区]
        A4[feishu:state:*<br/>会话状态]
        A5[feishu:lock:*<br/>分布式锁]
        A6[feishu:token<br/>Token缓存]
    end

    subgraph "冷存储 - PostgreSQL"
        B1[feishu_access_config<br/>访问控制配置]
        B2[feishu_group_config<br/>群组配置]
        B3[feishu_user_bindings<br/>用户绑定]
        B4[feishu_media_files<br/>媒体元数据]
        B5[feishu_message_logs<br/>审计日志]
        B6[feishu_chat_session_mapping<br/>会话映射]
    end

    subgraph "文件存储"
        C1[uploads/feishu_media/<br/>媒体文件]
    end

    style A1 fill:#fff3e0
    style A2 fill:#fff3e0
    style A6 fill:#e3f2fd
```

---

## 八、部署架构

```mermaid
graph TB
    subgraph "负载均衡层"
        LB[Nginx/ALB]
    end

    subgraph "服务层"
        Webhook1[feishu-webhook-1]
        Webhook2[feishu-webhook-2]
        Webhook3[feishu-webhook-N]
    end

    subgraph "消息队列层"
        Redis[(Redis Cluster)]
    end

    subgraph "工作节点层"
        Worker1[Feishu Worker-1]
        Worker2[Feishu Worker-2]
        Worker3[Cron Worker]
    end

    subgraph "数据层"
        PG[(PostgreSQL)]
        MinIO[MinIO/S3]
    end

    LB --> Webhook1
    LB --> Webhook2
    LB --> Webhook3

    Webhook1 --> Redis
    Webhook2 --> Redis
    Webhook3 --> Redis

    Redis --> Worker1
    Redis --> Worker2
    Redis --> Worker3

    Worker1 --> PG
    Worker1 --> MinIO
    Worker2 --> PG
    Worker2 --> MinIO
    Worker3 --> PG

    style Webhook1 fill:#e1f5fe
    style Webhook2 fill:#e1f5fe
    style Worker1 fill:#e8f5e9
    style Worker2 fill:#e8f5e9
    style Worker3 fill:#fff3e0
```

---

## 九、监控告警体系

```mermaid
graph TB
    subgraph "指标采集"
        A1[消息处理数]
        A2[队列长度]
        A3[处理耗时]
        A4[错误率]
        A5[隔离队列长度]
    end

    subgraph "告警规则"
        B1[错误率>5%<br/>Critical]
        B2[队列积压>1000<br/>Warning]
        B3[隔离队列>0<br/>P1告警]
        B4[限流触发<br/>Warning]
    end

    subgraph "响应机制"
        C1[自动扩容Worker]
        C2[人工介入处理]
        C3[故障自愈]
    end

    A1 --> B1
    A2 --> B2
    A5 --> B3
    A4 --> B4

    B1 --> C3
    B2 --> C1
    B3 --> C2
    B4 --> C1

    style B1 fill:#ffcdd2
    style B3 fill:#ffcdd2
    style C3 fill:#c8e6c9
```

---

## 十、核心创新点总结

```mermaid
graph LR
    subgraph "技术创新"
        A1[双队列架构<br/>独立扩缩容]
        A2[段落感知刷新<br/>流式卡片]
        A3[Redis防抖<br/>双阶段策略]
        A4[隔离队列<br/>异常处理]
    end

    subgraph "业务价值"
        B1[高可用<br/>99.9% SLA]
        B2[高性能<br/>支持1000+ QPS]
        B3[可观测<br/>全链路追踪]
        B4[易运维<br/>故障自愈]
    end

    A1 --> B2
    A2 --> B1
    A3 --> B2
    A4 --> B3
    A4 --> B4

    style A1 fill:#e3f2fd
    style A2 fill:#e3f2fd
    style A3 fill:#e3f2fd
    style A4 fill:#e3f2fd
    style B1 fill:#c8e6c9
    style B2 fill:#c8e6c9
    style B3 fill:#c8e6c9
    style B4 fill:#c8e6c9
```

---

## 汇报要点提示

1. **架构先进性**
   - 采用独立队列架构，避免资源竞争
   - BRPOPLPUSH 原子操作保证消息不丢失
   - 幂等设计防止重复处理

2. **用户体验**
   - BlockStreaming 流式卡片，实时打字机效果
   - 段落感知刷新，保持内容完整性
   - 防抖机制智能合并连续消息

3. **安全合规**
   - 多层安全校验（签名、加密、防重放）
   - 访问控制策略（白名单、禁用、开放）
   - 完整审计日志记录

4. **运维友好**
   - 完善的监控告警体系
   - 故障自愈能力
   - 水平扩展支持

5. **性能指标**
   - 消息处理延迟：< 4秒（含防抖）
   - 支持并发：1000+ QPS
   - 可用性：99.9% SLA

6. **风险控制**
   - 隔离队列处理异常消息
   - 自动降级策略
   - 完善的回滚方案

---

**文档版本**: v1.0  
**创建日期**: 2026-03-12  
**适用场景**: 技术方案评审、架构汇报、团队分享
