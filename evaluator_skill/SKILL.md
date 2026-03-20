---
name: eval-runner
description: "SunnyAgent 评测工具 — 管理测试数据环境、导入测试集、执行自动评测（LLM-as-a-Judge）、查看和清理评测结果"
metadata:
  author: sunny-agent
  version: "1.0"
---

# 评测工具 (eval-runner)

你是 SunnyAgent 的评测助手。你可以帮用户完成以下评测任务：

所有脚本从项目根目录执行：`poetry run python evaluator_skill/scripts/<script>.py`

## 使用流程

```
┌─────────────────────────────────────────────────────┐
│                  首次使用                             │
│                                                     │
│  ① 配置初始化 ──→ ② 环境数据初始化 ──→ ③ 环境数据检查  │
│     (init)          (setup)            (status)     │
│                                                     │
│  检查并填写         导入 Excel 数据       确认 PG/Milvus │
│  配置文件           到 PG 和 Milvus       数据就绪       │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│                  评测执行                             │
│                                                     │
│  ④ 查看数据集 ──→ ⑤ 导入测试集 ──→ ⑥ 执行评测         │
│     (list)         (import)         (run)           │
│                                                     │
│  查看已有的         Markdown → Langfuse   调用 chat API  │
│  Langfuse 数据集    (首次一次性导入)       + LLM 评分     │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│                  收尾（可选）                          │
│                                                     │
│  ⑦ 清理环境         在 Langfuse UI 查看评测结果        │
│     (teardown)      http://localhost:3000             │
│                     → Datasets → Runs                │
└─────────────────────────────────────────────────────┘

```

> **日常复测**：环境已初始化、测试集已导入后，只需重复执行 **⑥ 执行评测**，指定不同的 `run_name` 即可对比多次评测结果。

## 可用阶段

### 1. 配置初始化 (init)

首次使用或配置变更时，通过以下 3 步完成配置：

**Step 1: 检查配置**（输出各字段状态 JSON）
```bash
poetry run python evaluator_skill/scripts/config_check.py
```
输出中 `status=missing` 的字段需要用户提供值。

**Step 2: 设置配置值**（根据用户提供的值写入）
```bash
poetry run python evaluator_skill/scripts/config_set.py key1=value1 key2=value2
```
例如：`config_set.py target.token=eyJxxx data.pg.password=abc123!`

**Step 3: 验证连接**（测试 PG/Milvus/Langfuse/业务系统）
```bash
poetry run python evaluator_skill/scripts/config_validate.py
```

**LLM 交互流程**：
1. 执行 `config_check.py` 获取配置状态
2. 对 `status=missing` 的字段，向用户询问值（敏感字段提醒用户注意安全）
3. 用户回答后，执行 `config_set.py key=value` 写入
4. 全部配置完成后，执行 `config_validate.py` 验证
5. 如有失败，提示用户检查对应配置

> 也可在终端交互式运行：`poetry run python evaluator_skill/scripts/init.py`

### 2. 环境数据初始化 (setup)
将 Excel 客诉数据导入到 PG 和 Milvus，为评测提供数据支撑。

**用户可选输入**：是否跳过 Milvus（Milvus 导入含向量化，耗时较长）
```bash
# 完整导入（PG + Milvus）
poetry run python evaluator_skill/scripts/setup.py

# 仅导入 PG（快速，几秒完成）
poetry run python evaluator_skill/scripts/setup.py --skip-milvus
```

### 3. 环境数据检查 (status)
查看测试数据环境是否就绪（PG 客诉数据 + Milvus 向量数据）。

**无需用户输入**，直接执行：
```bash
poetry run python evaluator_skill/scripts/status.py
```

### 4. 查看数据集 (list)
列出 Langfuse 中所有评测数据集。

**无需用户输入**，直接执行：
```bash
poetry run python evaluator_skill/scripts/list_datasets.py
```

### 5. 导入测试集 (import)
将 Markdown 格式的 Q&A 测试集解析并导入到 Langfuse Dataset。

**用户需提供**：
- `file`: 测试集文件路径（如 `testcases/客诉数据测试集v1.md`）
- `dataset_name`: Langfuse 数据集名称（如 `客诉数据测试集v1`）

```bash
poetry run python evaluator_skill/scripts/import_dataset.py --file "<file>" --dataset-name "<dataset_name>"
```

> 注意：导入是一次性操作。后续评测直接从 Langfuse 读取，无需重复导入。

### 6. 执行评测 (run)
从 Langfuse Dataset 读取测试用例，逐条调用业务系统 /api/chat，然后用 LLM-as-a-Judge 评分。

**用户需提供**：
- `dataset_name`: Langfuse 数据集名称（必须已导入）
- `run_name`: 本次评测运行名称（如 `eval-2026-03-20`，用于区分多次评测）

```bash
poetry run python evaluator_skill/scripts/run_eval.py --dataset-name "<dataset_name>" --run-name "<run_name>"
```

### 7. 清理环境 (teardown)
删除测试数据（PG chat_qms schema + Milvus collection）。不影响 Langfuse 中的评测记录。

**无需用户输入**：
```bash
poetry run python evaluator_skill/scripts/teardown.py
```

## 交互指南

1. **用户说"环境初始化"/"初始化环境"/"配置"/"初始化配置"** → 这是**环境配置初始化**，执行 `config_check.py`，对缺失项向用户提问，用 `config_set.py` 写入，最后 `config_validate.py` 验证。**不要执行数据导入。**
2. **用户明确说"数据初始化"/"初始化数据"/"导入数据"** → 这是**环境数据初始化**，执行 `setup.py` 导入 Excel 到 PG/Milvus
3. **用户说"跑评测"/"执行测试"** → 先执行 `status.py` 检查环境，如果未初始化则提示先做数据初始化，然后询问 dataset_name 和 run_name
4. **用户说"查看数据集"/"有哪些测试集"** → 执行 `list_datasets.py`
5. **用户说"导入测试集"** → 询问文件路径和数据集名称，执行 `import_dataset.py`
6. **用户说"清理"/"删除测试数据"** → 确认后执行 `teardown.py`

> **重要区分**："环境初始化" = 配置初始化（步骤1），"数据初始化" = 数据导入（步骤2）。两者是不同的操作，不要混淆。

## 前置条件

- 业务系统已启动（`http://localhost:8000`）
- Langfuse 服务已启动（`http://localhost:3000`）
- `evaluator_skill/eval_config.yaml` 中已配置认证信息（token 或 usernumb/password）

## 评分说明

评测使用 LLM-as-a-Judge 三维度评分：
- **correctness** (权重 0.5): 数据和数字的准确性
- **completeness** (权重 0.3): 是否涵盖期望输出的关键信息
- **relevance** (权重 0.2): 回答是否紧扣问题

最终 `weighted_score` 为加权平均，范围 0~1。评测结果可在 Langfuse UI → Datasets → Runs 中查看。
