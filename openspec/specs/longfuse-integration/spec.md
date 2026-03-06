# Feature Specification: Langfuse 可观测性集成

**Feature Branch**: `007-langfuse-integration`
**Created**: 2026-02-19
**Updated**: 2026-03-06
**Status**: Draft
**Input**: User description: "集成 Langfuse 进行可观测性管理，改动现有 Agent 代码，监控 Agent 运行状态，并支持测试数据集管理和评估"

## Scope

### In Scope

- **Trace 集成**：通过 Langfuse SDK 实现 Agent 执行链路的自动追踪和记录
- **监控仪表盘**：利用 Langfuse 内置仪表盘展示运行状态和性能指标
- **测试数据集管理**：支持创建、编辑、导入测试数据集
- **Agent 评估**：通过 Experiment 机制调用真实 Agent 进行自动化评估
- **账号同步**：SunnyAgent 与 Langfuse 的用户账号自动同步
- **系统管理集成**：在 SunnyAgent 管理界面提供 Langfuse 入口

### Out of Scope

- **自定义仪表盘开发**：不开发定制化的监控仪表盘，使用 Langfuse 内置功能
- **多租户隔离**：本期不实现不同组织/团队的数据隔离
- **实时告警系统**：不实现基于阈值的自动告警（可后续集成第三方告警系统）
- **Trace 数据导出**：不实现将 Trace 数据导出到其他系统的功能
- **历史数据迁移**：不迁移集成前的历史执行数据
- **Langfuse 服务运维**：不包含 Langfuse 服务本身的监控、备份、升级等运维工作
- **Prompt Playground 集成**：本期不实现 Langfuse Prompt Playground 的集成，完整 Agent 测试使用 Dataset + Experiment 方式

## Clarifications

### Session 2026-03-06

- Q: Prompt Playground 是否需要集成？ → A: 本期不实现，完整 Agent 测试使用 Dataset + Experiment 方式
- Q: Langfuse 初始化方式？ → A: SunnyAgent 首次启动时自动初始化，生成 API Key，用户无需手动配置
- Q: 现有 observability 如何处理？ → A: 需要改造现有代码以支持 Langfuse Trace
- Q: Trace 数据如何关联用户和对话？ → A: 每个 Trace 必须携带 user_id 和 session_id，支持按用户和对话维度查询
- Q: 系统管理如何访问 Langfuse？ → A: 在系统设置页面增加"可观测性"Tab，显示 Langfuse 状态、跳转链接、Token 用量统计
- Q: Token 用量统计维度？ → A: 按用户维度统计，不按模型区分。管理员可查所有用户，普通用户只能查自己
- Q: 用量统计时间范围？ → A: 支持选择时间范围和起始日期，默认查询当天

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 查看 Agent 执行链路追踪 (Priority: P1)

作为运维人员，我希望能够查看每次 Agent 调用的完整执行链路，包括意图识别、任务规划、Actor 执行等各阶段的详细信息，以便快速定位问题和优化性能。

**Why this priority**: Agent 执行链路追踪是可观测性的核心功能，直接影响问题排查效率和系统优化能力。没有 Trace 功能，其他监控功能将缺乏上下文。

**Independent Test**: 可以通过发起一次对话请求，然后在 Langfuse 界面中查看完整的 Trace 记录来独立测试此功能。

**Acceptance Scenarios**:

1. **Given** 用户发送一条消息触发 Agent 执行, **When** Agent 处理完成, **Then** 系统通过 LangGraph Callback 自动记录完整的执行 Trace，包含各阶段耗时和关键参数
2. **Given** 运维人员打开 Langfuse 界面, **When** 选择某次对话的 Trace, **Then** 可以看到从用户输入到最终输出的完整调用链路（包括 AIME 各组件）
3. **Given** Agent 执行过程中发生错误, **When** 查看该次执行的 Trace, **Then** 错误位置和错误信息清晰标注

---

### User Story 2 - 监控 Agent 运行状态和性能指标 (Priority: P1)

作为运维人员，我希望能够实时监控所有 Agent 的运行状态、调用次数、平均响应时间、Token 消耗等关键指标，以便及时发现异常并采取措施。

**Why this priority**: 实时监控是保障系统稳定运行的基础，与 Trace 功能同等重要，共同构成可观测性的核心。

**Independent Test**: 可以通过查看 Langfuse 的监控仪表盘，验证各项指标是否正常显示和更新。

**Acceptance Scenarios**:

1. **Given** 系统正常运行, **When** 打开 Langfuse 仪表盘, **Then** 可以看到各 Agent 的调用次数、成功率、平均响应时间、Token 消耗等指标
2. **Given** 某 Agent 响应时间异常, **When** 查看监控界面, **Then** 可以快速定位到问题 Trace
3. **Given** 系统运行一段时间后, **When** 查看趋势图, **Then** 可以看到历史指标变化趋势和成本分析

---

### User Story 3 - 管理测试数据集并评估 Agent (Priority: P2)

作为开发人员，我希望能够创建测试数据集，并通过自定义评估函数调用真实的 SunnyAgent Agent 进行评估，以确保 Agent 性能持续改进。

**Why this priority**: 测试数据集管理和 Agent 评估是持续优化的基础，但在基础监控功能就绪后才能发挥最大价值。

**Independent Test**: 可以通过在 Langfuse 中创建测试数据集、编写评估脚本调用 SunnyAgent `/api/chat`、查看评估结果来独立验证此功能。

**Acceptance Scenarios**:

1. **Given** 开发人员需要创建测试数据集, **When** 在 Langfuse 界面或通过 SDK 创建数据集, **Then** 系统创建空的测试数据集
2. **Given** 已有测试数据集, **When** 通过 UI/SDK/CSV 导入测试用例（输入-期望输出对）, **Then** 测试用例被添加到数据集中
3. **Given** 测试数据集包含多个测试用例, **When** 运行 Experiment（自定义任务函数调用 `/api/chat`）, **Then** 系统逐一调用真实 Agent 并记录实际输出
4. **Given** 评估完成, **When** 配置 LLM-as-a-Judge 评估器, **Then** 系统自动对比期望输出和实际输出并给出评分

---

### User Story 4 - Langfuse 自动初始化与免配置访问 (Priority: P1)

作为系统管理员，我希望 SunnyAgent 首次启动时能够自动初始化 Langfuse 环境并生成必要的 API Key，使用户从 SunnyAgent 跳转到 Langfuse 时无需手动配置任何认证信息。

**Why this priority**: 自动初始化是零配置体验的基础，直接影响用户首次使用的门槛和运维复杂度。

**Independent Test**: 可以通过在全新环境部署 SunnyAgent，验证 Langfuse 是否自动完成初始化，用户是否能直接跳转访问。

**Acceptance Scenarios**:

1. **Given** SunnyAgent 首次启动且 Langfuse 未初始化, **When** 系统启动完成, **Then** 自动创建 Langfuse 项目并生成 Public/Secret Key
2. **Given** Langfuse 已自动初始化, **When** 用户从 SunnyAgent 跳转到 Langfuse, **Then** 系统自动携带认证信息，用户无需手动输入 API Key
3. **Given** 系统已运行一段时间, **When** 重新启动 SunnyAgent, **Then** 系统检测到已初始化状态，不重复创建项目和 Key
4. **Given** 自动初始化失败, **When** 管理员查看系统日志, **Then** 可以看到详细的错误信息和手动配置指引

---

### User Story 5 - 系统管理可观测性 Tab (Priority: P1)

作为管理员或用户，我希望在系统设置页面中有一个"可观测性"Tab，能够查看 Langfuse 运行状态、Token 用量统计，并能快速跳转到 Langfuse 控制台。

**Why this priority**: 统一入口和用量可视化是提升运维体验的关键，让用户无需进入 Langfuse 即可了解核心指标。

**Independent Test**: 可以通过打开系统设置页面的可观测性 Tab，验证各功能模块是否正常显示和交互。

**Acceptance Scenarios**:

**Langfuse 状态与跳转**
1. **Given** 用户打开系统设置页面, **When** 切换到可观测性 Tab, **Then** 可以看到 Langfuse 卡片，显示"Agent 执行链路追踪与监控平台"描述
2. **Given** Langfuse 服务正常运行, **When** 查看状态指示器, **Then** 显示绿色"运行正常"状态
3. **Given** Langfuse 服务不可用, **When** 查看状态指示器, **Then** 显示红色"服务异常"状态
4. **Given** 用户点击"打开 Langfuse 控制台"链接, **When** 系统处理跳转, **Then** 在新标签页打开 Langfuse 界面并自动登录

**Token 用量统计**
5. **Given** 用户查看 Token 用量统计区域, **When** 页面加载完成, **Then** 默认显示当天的用量数据
6. **Given** 用户选择时间范围（如 7 天）和起始日期, **When** 点击刷新, **Then** 显示指定时间段内的用量统计
7. **Given** 用量数据加载完成, **When** 查看统计卡片, **Then** 显示总调用次数、总 Token 数（含输入/输出明细）、预估费用
8. **Given** 用量数据加载完成, **When** 查看趋势图, **Then** 显示按日维度的 Token 用量柱状图

**用户维度权限控制**
9. **Given** 当前用户是管理员, **When** 查看用量统计, **Then** 可以看到所有用户的汇总数据，并可按用户筛选
10. **Given** 当前用户是普通用户, **When** 查看用量统计, **Then** 只能看到自己的用量数据

**账号同步**
11. **Given** SunnyAgent 创建新用户, **When** 该用户首次访问 Langfuse, **Then** Langfuse 自动创建对应账号
12. **Given** SunnyAgent 禁用某用户, **When** 该用户尝试访问 Langfuse, **Then** Langfuse 拒绝访问

---

### User Story 6 - 改造现有 Observability 支持 Langfuse Trace (Priority: P1)

作为开发人员，我希望现有 SunnyAgent 的可观测性代码能够被改造，使 Langfuse Trace 能够正常工作，并且 Trace 数据能够关联到具体的用户和对话。

**Why this priority**: 这是 Langfuse 集成的核心技术基础，没有对现有 observability 的改造，Trace 功能无法正常工作。

**Independent Test**: 可以通过发起一次对话，然后在 Langfuse 中按用户 ID 或对话 ID 筛选，验证 Trace 是否正确关联。

**Acceptance Scenarios**:

1. **Given** 用户发起一次对话, **When** Agent 处理完成, **Then** Trace 数据包含用户 ID（user_id）和对话 ID（session_id）
2. **Given** 同一用户发起多次对话, **When** 在 Langfuse 中按用户筛选, **Then** 可以看到该用户的所有 Trace 记录
3. **Given** 一次对话包含多轮交互, **When** 查看该对话的 Trace, **Then** 所有交互的 Trace 通过 session_id 关联在一起
4. **Given** 现有 observability 代码已改造, **When** Agent 执行各阶段, **Then** 各阶段的 Span 正确嵌套并记录到 Langfuse

---

### Edge Cases

- 当 Langfuse 服务不可用时，Agent 应继续正常工作，Trace 数据异步上报失败后丢弃（不阻塞主流程）
- 当 Langfuse 服务不可用时，系统管理界面的 Langfuse 链接应显示服务不可用状态
- 当 Trace 数据量过大时，Langfuse 支持采样策略配置
- 当测试数据集为空时运行评估，应给出友好提示
- 当 Agent 执行超时时，Trace 应记录已完成的部分和超时信息
- 当评估任务函数调用 `/api/chat` 失败时，应记录错误并继续下一个测试用例
- 当自动初始化失败时（如 Langfuse 服务未就绪），系统应记录错误并在下次启动时重试
- 当请求上下文缺失 user_id 时（如匿名用户），应使用默认标识并记录警告
- 当同一对话跨越多个 session 时（如会话超时重连），应能通过用户维度关联历史 Trace

## Requirements *(mandatory)*

### Functional Requirements

**Trace 追踪（P1）**
- **FR-001**: 系统 MUST 通过 Langfuse LangChain/LangGraph Callback 自动记录 Agent 执行链路
- **FR-002**: 系统 MUST 记录 AIME 核心组件（IntentAnalyzer、Planner、ActorFactory、Actor）的执行信息作为 Span
- **FR-003**: 系统 MUST 记录每个执行阶段的耗时、输入参数、输出结果、Token 消耗
- **FR-004**: 系统 MUST 在 Agent 执行出错时记录错误类型、错误位置和堆栈信息
- **FR-005**: 系统 MUST 支持通过环境变量配置 Langfuse 服务地址和认证信息
- **FR-006**: 系统 MUST 在 Langfuse 不可用时不影响 Agent 正常运行（异步上报 + 优雅降级）

**监控仪表盘（P1）**
- **FR-007**: 系统 MUST 利用 Langfuse 内置仪表盘展示 Agent 运行状态和性能指标

**测试数据集与评估（P2）**
- **FR-008**: 系统 MUST 支持通过 Langfuse UI/SDK 创建、查看、编辑、删除测试数据集
- **FR-009**: 系统 MUST 支持向测试数据集添加测试用例（包含输入和期望输出）
- **FR-010**: 系统 MUST 支持编写自定义评估脚本，在 Experiment 中调用 SunnyAgent `/api/chat` 进行真实 Agent 测试
- **FR-011**: 系统 MUST 支持 LLM-as-a-Judge 评估方式，自动对比期望输出和实际输出

**Langfuse 自动初始化（P1）**
- **FR-012**: 系统 MUST 在首次启动时自动检测 Langfuse 是否已初始化
- **FR-013**: 系统 MUST 在未初始化时自动创建 Langfuse 项目并生成 Public Key 和 Secret Key
- **FR-014**: 系统 MUST 将自动生成的 API Key 安全存储，供后续 Trace 上报使用
- **FR-015**: 系统 MUST 在重复启动时跳过初始化，避免重复创建项目和 Key
- **FR-016**: 系统 SHOULD 在自动初始化失败时记录详细日志并提供手动配置指引

**Observability 改造（P1）**
- **FR-017**: 系统 MUST 改造现有 observability 代码以支持 Langfuse Trace 上报
- **FR-018**: 系统 MUST 在每个 Trace 中记录用户 ID（user_id），关联到发起请求的用户
- **FR-019**: 系统 MUST 在每个 Trace 中记录对话 ID（session_id），关联同一对话的多轮交互
- **FR-020**: 系统 MUST 确保 Span 正确嵌套，反映 Agent 执行的层级结构

**系统管理可观测性 Tab（P1）**

*Langfuse 状态与跳转*
- **FR-021**: 系统 MUST 在系统设置页面提供"可观测性"Tab
- **FR-022**: 系统 MUST 显示 Langfuse 卡片，包含服务描述和运行状态指示器
- **FR-023**: 系统 MUST 实时检测 Langfuse 服务状态（运行正常/服务异常）
- **FR-024**: 系统 MUST 提供"打开 Langfuse 控制台"链接，点击后在新标签页打开并自动登录

*Token 用量统计*
- **FR-025**: 系统 MUST 显示 Token 用量统计区域，包含时间范围选择器（默认当天）
- **FR-026**: 系统 MUST 支持选择时间范围（当天、7天、30天等）和自定义起始日期
- **FR-027**: 系统 MUST 显示统计卡片：总调用次数、总 Token 数（含输入/输出明细）、预估费用
- **FR-028**: 系统 MUST 显示按日维度的 Token 用量趋势图（柱状图）
- **FR-029**: 系统 MUST 支持刷新按钮，手动更新用量数据
- **FR-030**: 系统 MUST 按用户维度统计用量，不按模型维度区分

*用户权限控制*
- **FR-031**: 系统 MUST 对管理员显示所有用户的汇总数据，并支持按用户筛选
- **FR-032**: 系统 MUST 对普通用户仅显示其个人的用量数据

*账号同步*
- **FR-033**: 系统 MUST 实现 SunnyAgent 与 Langfuse 的账号同步
- **FR-034**: 系统 MUST 在 SunnyAgent 创建用户时自动在 Langfuse 创建对应账号
- **FR-035**: 系统 MUST 在 SunnyAgent 禁用用户时同步禁用 Langfuse 账号访问权限

### Non-Functional Requirements

**性能要求**
- **NFR-001**: Trace 数据上报 MUST 采用异步方式，不阻塞 Agent 主流程
- **NFR-002**: 单次 Trace 上报延迟 SHOULD 不超过 100ms（网络正常情况下）
- **NFR-003**: 系统 SHOULD 支持配置 Trace 采样率，以控制高负载场景下的数据量

**可靠性要求**
- **NFR-004**: 当 Langfuse 服务不可用时，Agent MUST 继续正常工作
- **NFR-005**: Trace 上报失败时 SHOULD 记录本地日志便于排查
- **NFR-006**: 账号同步失败时 MUST 有重试机制，并记录失败日志

**安全要求**
- **NFR-007**: Langfuse API 认证信息 MUST 通过环境变量或密钥管理服务获取，不得硬编码
- **NFR-008**: 敏感数据（如用户密码、Token）MUST NOT 被记录到 Trace 中
- **NFR-009**: 账号同步 API 调用 MUST 使用 HTTPS 加密传输

**可维护性要求**
- **NFR-010**: Langfuse 服务地址等配置项 SHOULD 支持热更新，无需重启服务
- **NFR-011**: 系统 SHOULD 提供 Langfuse 连接状态的健康检查接口

### Key Entities

- **Trace**: 表示一次完整的 Agent 执行记录，包含多个 Span，由 Langfuse 自动采集
  - `user_id`: 发起请求的用户标识，用于按用户筛选和分析
  - `session_id`: 对话会话标识，用于关联同一对话的多轮交互
  - `metadata`: 附加元数据（如租户信息、环境标识等）
- **Span**: 表示执行链路中的一个阶段（如意图识别、任务规划、Actor 执行），包含名称、耗时、状态、输入输出
- **Session**: 表示一次完整的对话会话，包含多个 Trace（多轮交互）
  - `session_id`: 会话唯一标识
  - `user_id`: 所属用户
  - `created_at`: 会话开始时间
- **Dataset**: Langfuse 测试数据集，包含名称、描述、创建时间、数据集项列表
- **DatasetItem**: 数据集项，包含输入（input）、期望输出（expected_output）、元数据
- **Experiment**: 一次评估运行，关联数据集版本，记录每个测试用例的实际输出和评分
- **Score**: 评估得分，支持 LLM-as-a-Judge、人工标注、自定义评分
- **LangfuseConfig**: Langfuse 配置信息，系统自动初始化时生成并存储
  - `project_id`: Langfuse 项目 ID
  - `public_key`: 用于 Trace 上报的公钥
  - `secret_key`: 用于管理操作的密钥（加密存储）
  - `initialized_at`: 初始化时间
- **TokenUsageStats**: Token 用量统计数据
  - `date`: 统计日期
  - `user_id`: 用户标识（用于按用户维度统计）
  - `total_calls`: 总调用次数
  - `total_tokens`: 总 Token 数
  - `input_tokens`: 输入 Token 数
  - `output_tokens`: 输出 Token 数
  - `estimated_cost`: 预估费用

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 运维人员可在 10 秒内从 Langfuse 界面找到任意一次对话的完整执行链路
- **SC-002**: 95% 的 Agent 调用的 Trace 数据成功上报到 Langfuse
- **SC-003**: Trace 数据上报（异步）不增加 Agent 主流程响应时间超过 10ms
- **SC-004**: 开发人员可在 5 分钟内通过 UI 或 SDK 创建一个包含 10 个测试用例的数据集
- **SC-005**: 系统支持同时运行 100 个测试用例的批量评估（Experiment）
- **SC-006**: 监控仪表盘数据延迟不超过 30 秒
- **SC-007**: 当 Langfuse 服务不可用时，Agent 响应时间不受影响
- **SC-008**: 管理员可从系统管理界面一键跳转到 Langfuse，无需再次登录
- **SC-009**: SunnyAgent 创建/禁用用户后，Langfuse 账号状态在 5 秒内同步完成
- **SC-010**: SunnyAgent 首次启动时，Langfuse 自动初始化在 30 秒内完成
- **SC-011**: 100% 的 Trace 包含有效的 user_id 和 session_id
- **SC-012**: 运维人员可通过 user_id 或 session_id 在 Langfuse 中快速筛选相关 Trace
- **SC-013**: 可观测性 Tab 页面加载时间不超过 2 秒
- **SC-014**: Token 用量统计数据查询响应时间不超过 3 秒（30 天范围内）
- **SC-015**: Langfuse 服务状态检测延迟不超过 5 秒

## Assumptions

- Langfuse 服务将被私有化部署，与 SunnyAgent 在同一网络环境
- 团队成员可以访问 Langfuse 的 Web 界面（英文界面，数据内容支持中文）
- 现有 Agent 代码基于 LangChain/LangGraph 框架，Langfuse 提供原生 Callback 支持
- 评估脚本通过 Langfuse Python SDK 编写，调用 SunnyAgent `/api/chat` 接口
- SunnyAgent 具有调用 Langfuse Admin API 的权限（用于自动初始化和账号同步）
- 现有 observability 代码结构支持改造为 Langfuse 集成（无需完全重写）
- 每个请求上下文中都能获取到当前用户 ID 和对话 ID

## Dependencies

### Langfuse Server v3 基础设施

> **重要**：Langfuse Server v3 不再仅依赖 PostgreSQL，需要以下完整基础设施栈：

| 组件 | 镜像 | 用途 | 必需 |
|------|------|------|------|
| **ClickHouse** | `clickhouse/clickhouse-server:24.3` | OLAP 分析引擎，存储 Trace/Span 数据 | ✅ 是 |
| **Redis** | `redis:7-alpine` | 缓存层，队列处理 | ✅ 是 |
| **MinIO** | `minio/minio:latest` | S3 兼容对象存储，存储大型事件数据 | ✅ 是 |
| **PostgreSQL** | `postgres:15` | 元数据存储（用户、项目、配置） | ✅ 是 |
| **Langfuse** | `langfuse/langfuse:3` | 主服务 | ✅ 是 |

### SDK 版本要求

| 组件 | 版本要求 | 说明 |
|------|----------|------|
| Langfuse Server | `≥ 3.63.0` | SDK v3 所需的最低服务端版本 |
| Langfuse Python SDK | `≥ 3.0.0` | 基于 OpenTelemetry 的新版 SDK |

> ⚠️ **版本兼容性**：SDK v3 与 Server v2 **不兼容**，必须使用 Server v3。

### 其他依赖

- Langfuse Admin API 可用（用于账号同步）
- SunnyAgent `/api/chat` 接口稳定可用（用于评估）
- 现有 AIME Agent 核心代码支持添加 Langfuse Callback
- SunnyAgent 前端系统管理页面存在（用于嵌入 Langfuse 链接）

## Architecture Decisions

1. **部署方式**: Langfuse v3 使用 Docker Compose 部署，包含 ClickHouse + Redis + MinIO + PostgreSQL 完整栈
2. **Trace 集成**: 使用 Langfuse 原生的 LangChain/LangGraph Callback，几乎零代码改动
3. **Agent 评估**: 不使用 Langfuse Playground 测试完整 Agent，而是通过 Dataset + Experiment + 自定义任务函数调用 `/api/chat`
4. **LLM-as-a-Judge**: 评估时复用 SunnyAgent 已配置的 LLM（通过环境变量），无需在 Langfuse 单独配置
5. **账号同步方案**: 采用 Admin API 方案 — SunnyAgent 用户 CRUD 操作时调用 Langfuse Instance Management API 同步账号（创建、禁用、删除）
6. **系统管理集成**: 在 SunnyAgent 管理后台添加 Langfuse 外链，点击后在新窗口（新标签页）打开 Langfuse 完整界面
7. **Span 处理模式**: 在 async generator 中使用直接 span 引用（`start_span()`/`start_generation()`）而非上下文管理器，避免 OpenTelemetry context 丢失问题（详见 research.md）
8. **自动初始化策略**: SunnyAgent 启动时检测 Langfuse 配置状态，未初始化则调用 Langfuse Admin API 自动创建项目和 API Key，配置信息加密存储到数据库
9. **Trace 用户关联**: 每个 Trace 必须携带 `user_id` 和 `session_id`，其中 `user_id` 从请求上下文获取，`session_id` 由对话管理模块生成
10. **存储设计**: Trace 原始数据存储在 Langfuse（ClickHouse），SunnyAgent 仅存储 Langfuse 配置信息和用户-Trace 的索引映射（用于快速定位）

## Risks & Mitigations

| 风险 | 影响 | 可能性 | 缓解措施 |
|------|------|--------|----------|
| Langfuse 服务宕机影响 Agent 可用性 | 高 | 中 | 异步上报 + 优雅降级，Langfuse 不可用时 Agent 继续正常工作 |
| Trace 数据量过大导致存储成本增加 | 中 | 高 | 支持采样率配置，生产环境可设置较低采样率 |
| 账号同步延迟导致用户无法访问 | 中 | 低 | 同步操作在用户 CRUD 时实时触发，并有重试机制 |
| SDK 版本升级带来兼容性问题 | 中 | 中 | 锁定 SDK 版本，升级前在测试环境验证 |
| 敏感信息泄露到 Trace | 高 | 低 | 实现数据脱敏层，过滤密码、Token 等敏感字段 |
| LLM-as-a-Judge 评估不准确 | 低 | 中 | 支持人工复核评分，评估结果仅作参考 |
| 自动初始化失败导致 Trace 功能不可用 | 高 | 低 | 提供手动配置备选方案，启动时重试机制 |
| API Key 泄露导致安全风险 | 高 | 低 | 加密存储 Secret Key，定期轮换机制 |
| Observability 改造影响现有功能 | 中 | 中 | 充分测试，保持向后兼容，支持开关切换 |

## Integration Interfaces

### SunnyAgent → Langfuse SDK

**Trace 上报接口**
- 使用 `langfuse.openai.OpenAI` 包装器或 `@observe()` 装饰器
- 通过 LangChain/LangGraph Callback Handler 自动采集
- Trace 必须携带 `user_id` 和 `session_id` 参数
- 配置来源：自动初始化生成的 API Key（从数据库读取）

**Dataset 管理接口**
- `langfuse.create_dataset(name, description)` - 创建数据集
- `langfuse.get_dataset(name)` - 获取数据集
- `dataset.create_item(input, expected_output, metadata)` - 添加测试用例

### SunnyAgent → Langfuse Admin API

**自动初始化接口**
- `POST /api/v1/projects` - 创建项目
- `POST /api/v1/projects/{projectId}/api-keys` - 生成 API Key
- `GET /api/v1/projects` - 检查项目是否已存在

**账号同步接口**
- `POST /api/v1/organizations/{orgId}/members` - 创建用户
- `PATCH /api/v1/organizations/{orgId}/members/{memberId}` - 更新用户状态
- `DELETE /api/v1/organizations/{orgId}/members/{memberId}` - 删除用户

### SunnyAgent 后端 API（前端调用）

> 以下为 SunnyAgent 后端提供给前端的 RESTful API，前后端分离架构。

#### 1. Langfuse 状态检查

```
GET /api/v1/observability/status
```

**描述**: 获取 Langfuse 服务运行状态

**权限**: 已登录用户

**响应**:
```json
{
  "status": "healthy" | "unhealthy" | "unknown",
  "initialized": true,
  "langfuseUrl": "https://langfuse.example.com",
  "lastCheckAt": "2026-03-06T10:30:00Z",
  "message": "运行正常"
}
```

---

#### 2. 获取 Langfuse 控制台跳转链接

```
GET /api/v1/observability/console-url
```

**描述**: 获取带认证信息的 Langfuse 控制台跳转 URL

**权限**: 已登录用户

**响应**:
```json
{
  "url": "https://langfuse.example.com/project/xxx?token=yyy",
  "expiresAt": "2026-03-06T11:30:00Z"
}
```

---

#### 3. 获取 Token 用量汇总统计

```
GET /api/v1/observability/usage/summary
```

**描述**: 获取指定时间范围内的用量汇总

**权限**:
- 管理员：可查看所有用户
- 普通用户：仅查看自己

**请求参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| startDate | string | 是 | 起始日期，格式 YYYY-MM-DD |
| endDate | string | 是 | 结束日期，格式 YYYY-MM-DD |
| userId | string | 否 | 用户ID（管理员可指定，普通用户忽略此参数） |

**响应**:
```json
{
  "totalCalls": 27,
  "totalTokens": 305300,
  "inputTokens": 295800,
  "outputTokens": 9500,
  "estimatedCost": 0.83,
  "currency": "USD",
  "period": {
    "startDate": "2026-03-04",
    "endDate": "2026-03-06"
  }
}
```

---

#### 4. 获取 Token 用量趋势（按日）

```
GET /api/v1/observability/usage/daily
```

**描述**: 获取按日维度的用量趋势数据

**权限**:
- 管理员：可查看所有用户
- 普通用户：仅查看自己

**请求参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| startDate | string | 是 | 起始日期，格式 YYYY-MM-DD |
| endDate | string | 是 | 结束日期，格式 YYYY-MM-DD |
| userId | string | 否 | 用户ID（管理员可指定） |

**响应**:
```json
{
  "data": [
    {
      "date": "2026-03-04",
      "totalCalls": 10,
      "totalTokens": 120000,
      "inputTokens": 115000,
      "outputTokens": 5000,
      "estimatedCost": 0.32
    },
    {
      "date": "2026-03-05",
      "totalCalls": 17,
      "totalTokens": 185300,
      "inputTokens": 180800,
      "outputTokens": 4500,
      "estimatedCost": 0.51
    }
  ]
}
```

---

#### 5. 获取用户分布统计

```
GET /api/v1/observability/usage/by-user
```

**描述**: 获取按用户维度的用量分布

**权限**: 仅管理员

**请求参数**:
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| startDate | string | 是 | 起始日期，格式 YYYY-MM-DD |
| endDate | string | 是 | 结束日期，格式 YYYY-MM-DD |
| limit | number | 否 | 返回数量限制，默认 50 |
| offset | number | 否 | 分页偏移，默认 0 |

**响应**:
```json
{
  "data": [
    {
      "userId": "admin",
      "userName": "管理员",
      "totalCalls": 27,
      "totalTokens": 305300,
      "estimatedCost": 0.83
    },
    {
      "userId": "user_001",
      "userName": "张三",
      "totalCalls": 12,
      "totalTokens": 128500,
      "estimatedCost": 0.35
    }
  ],
  "pagination": {
    "total": 15,
    "limit": 50,
    "offset": 0
  }
}
```

---

#### 6. 刷新用量数据

```
POST /api/v1/observability/usage/refresh
```

**描述**: 手动触发用量数据刷新（从 Langfuse 同步最新数据）

**权限**: 管理员

**响应**:
```json
{
  "success": true,
  "lastSyncAt": "2026-03-06T10:35:00Z",
  "message": "数据刷新成功"
}
```

---

### SunnyAgent → Langfuse API（后端调用）

> 以下为 SunnyAgent 后端调用 Langfuse API 的接口定义

#### 自动初始化接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/v1/projects` | POST | 创建 Langfuse 项目 |
| `/api/v1/projects` | GET | 检查项目是否已存在 |
| `/api/v1/projects/{projectId}/api-keys` | POST | 生成 API Key |

#### 账号同步接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/v1/organizations/{orgId}/members` | POST | 创建用户 |
| `/api/v1/organizations/{orgId}/members/{memberId}` | PATCH | 更新用户状态 |
| `/api/v1/organizations/{orgId}/members/{memberId}` | DELETE | 删除用户 |

#### 用量统计接口（Langfuse 原生）

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/public/metrics/daily` | GET | 获取按日维度的原始用量数据 |
| `/api/public/metrics/usage` | GET | 获取汇总统计 |
| `/api/public/health` | GET | 健康检查 |

#### 健康检查接口

```
GET /api/public/health
```

**响应**:
```json
{
  "status": "OK",
  "version": "3.63.0"
}
```

---

### 前端 → SunnyAgent 后端 调用流程

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────┐
│   前端 UI   │────▶│ SunnyAgent 后端 │────▶│  Langfuse   │
│  (Vue/React)│◀────│   (Python/Go)   │◀────│   Server    │
└─────────────┘     └─────────────────┘     └─────────────┘
      │                     │                      │
      │  /api/v1/observability/*                   │
      │────────────────────▶│                      │
      │                     │  /api/public/*       │
      │                     │─────────────────────▶│
      │                     │◀─────────────────────│
      │◀────────────────────│                      │
      │   JSON Response     │                      │
```

### 存储设计

**SunnyAgent 数据库存储**
- `langfuse_config` 表：存储 Langfuse 配置信息（project_id, public_key, encrypted_secret_key, initialized_at）
- `trace_index` 表（可选）：存储 user_id/session_id 到 trace_id 的映射，用于快速查询

**Langfuse 存储**
- Trace/Span 原始数据存储在 ClickHouse
- 用户/项目元数据存储在 PostgreSQL
