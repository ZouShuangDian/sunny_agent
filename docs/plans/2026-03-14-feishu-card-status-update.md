# 飞书卡片状态更新功能实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现一个通过飞书卡片状态更新展示机器人工作流程的功能，提升用户交互体验。

**Architecture:** 基于现有的 `BlockStreamingManager` 扩展状态管理，在消息处理流程中分阶段更新卡片内容，展示"思考中" → "校验中" → "生成答案中" → 发送最终答案的状态流转。

**Tech Stack:** Python 3.11+, FastAPI, 飞书 CardKit API, asyncio

---

## 需求分析

### 流程
1. 收到用户消息 → 立即发送卡片，状态"⏳ 思考中..."
2. 校验阶段（休眠 1 秒）→ 更新卡片状态"🔍 校验中..."
3. 调用大模型（休眠 5 秒模拟）→ 更新卡片状态"🤖 生成答案中..."
4. 大模型返回 → 关闭卡片流式模式，发送最终答案给用户

### 关键点
- 使用飞书流式卡片 API（已封装在 `BlockStreamingManager`）
- 需要管理卡片状态流转
- 最终答案通过普通消息发送，卡片状态清空

---

## Task 1: 创建卡片状态管理器

**Files:**
- Create: `app/feishu/card_status_manager.py`
- Test: `tests/feishu/test_card_status.py`

**步骤:**
1. 定义卡片状态枚举（Thinking, Validating, Generating, Completed）
2. 创建 `CardStatusManager` 类，管理单个卡片的状态流转
3. 实现状态更新方法，调用 `BlockStreamingManager` 更新卡片内容
4. 添加状态转换验证

---

## Task 2: 集成到消息处理流程

**Files:**
- Modify: `app/feishu/worker_feishu.py` (或消息处理入口)
- Create: `app/feishu/schemas.py` (消息处理相关 schema)

**步骤:**
1. 在消息处理函数中，收到消息后立即创建卡片
2. 分阶段调用 `CardStatusManager.update_status()`
3. 最终发送答案时关闭卡片流式模式

---

## Task 3: 添加配置和日志

**Files:**
- Modify: `app/config.py`
- Modify: 相关日志配置

**步骤:**
1. 添加卡片状态更新的超时配置
2. 添加详细的结构化日志
3. 添加 Prometheus 指标（可选）

---

## Task 4: 测试

**Files:**
- Create: `tests/feishu/test_card_status_integration.py`

**步骤:**
1. 单元测试：测试状态管理器
2. 集成测试：模拟完整的消息处理流程
3. 手动测试：在飞书中验证效果

---

## API 文档参考

- 飞书卡片套件：https://open.feishu.cn/document/cardkit-v1/feishu-card-resource-overview
- 流式卡片更新：`PUT /cardkit/v1/cards/{card_id}/elements/{element_id}/content`
- 关闭流式模式：`PATCH /cardkit/v1/cards/{card_id}/settings`
