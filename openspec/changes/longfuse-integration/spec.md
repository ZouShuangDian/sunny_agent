# Feature Specification: Langfuse 可观测性集成

**Feature Branch**: `feature/langfuse`
**Created**: 2026-02-19
**Updated**: 2026-03-13
**Status**: Draft
**Input**: User description: "集成 Langfuse 进行可观测性管理，改动现有 Agent 代码，监控 Agent 运行状态，并支持测试数据集管理和评估"

## Repositories

| 仓库 | 路径 | 职责 |
|------|------|------|
| **sunny_agent** (后端) | `/Users/yanwen/Documents/github/sunny_agent` | FastAPI API、Trace 集成、Langfuse 服务管理、数据聚合 |
| **sunny-agent-web** (前端) | `/Users/yanwen/Documents/github/sunny-agent-web` | Vue 3 UI、可观测性 Tab 页面、用量图表、管理员面板扩展 |

> 本 spec 覆盖前后端完整功能。后端任务在 `sunny_agent` 仓库执行，前端任务在 `sunny-agent-web` 仓库执行。

## Scope

### In Scope

- **Trace 集成**：通过 Langfuse SDK 实现 Agent 执行链路的自动追踪和记录
- **监控仪表盘**：利用 Langfuse 内置仪表盘展示运行状态和性能指标
- **测试数据集管理**：支持创建、编辑、导入测试数据集
- **Agent 评估**：通过 Experiment 机制调用真实 Agent 进行自动化评估
- **账号同步**：SunnyAgent 与 Langfuse 的用户账号自动同步
- **系统管理集成**：在 SunnyAgent 管理界面提供 Langfuse 入口
- **Trace 数据导出**：支持将 Trace 数据导出为 JSON/CSV 格式

### Out of Scope

- **自定义仪表盘开发**：不开发定制化的监控仪表盘，使用 Langfuse 内置功能
- **多租户隔离**：本期不实现不同组织/团队的数据隔离
- **实时告警系统**：不实现基于阈值的自动告警（可后续集成第三方告警系统）
- **历史数据迁移**：不迁移集成前的历史执行数据
- **Langfuse 服务运维**：不包含 Langfuse 服务本身的监控、备份、升级等运维工作
- **Prompt Playground 集成**：本期不实现 Langfuse Prompt Playground 的集成，完整 Agent 测试使用 Dataset + Experiment 方式

## Clarifications

### Session 2026-03-13

- Q: 内置 Langfuse 服务的启停管理方式？ → A: 通过 Docker Compose CLI（`docker compose up/down`）管理容器生命周期，要求宿主机安装 Docker
- Q: Langfuse 控制台自动登录机制？ → A: 后端代理登录获取 session token 并重定向；Langfuse 不支持时降级为手动登录。权限双层保障：SunnyAgent 仅管理员可见入口 + Langfuse 仅同步管理员账号
- Q: 费用预估的模型价格策略？ → A: 直接使用 Langfuse 原生 cost 字段聚合，不自行维护价格表
- Q: Langfuse Admin API 认证与初始化方式？ → A: 内置服务通过 docker-compose 环境变量（`LANGFUSE_INIT_*`）预配置初始组织、项目和 API Key，启动时自动完成初始化，无需运行时调用 Admin API
- Q: 内置与外部 Langfuse 的登录和账号同步统一方案？ → A: .env 统一配置一套 Langfuse 管理员凭据（email/password）。内置服务将其作为 `LANGFUSE_INIT_USER_*` 创建初始账号；外部服务填写已有账号。控制台跳转时后端用该凭据代理登录获取 session。所有 SunnyAgent 管理员共用一个 Langfuse 账号，不做逐人同步

### Session 2026-03-06

- Q: Prompt Playground 是否需要集成？ → A: 本期不实现，完整 Agent 测试使用 Dataset + Experiment 方式
- Q: Langfuse 初始化方式？ → A: SunnyAgent 首次启动时自动初始化，生成 API Key，用户无需手动配置
- Q: 现有 observability 如何处理？ → A: 需要改造现有代码以支持 Langfuse Trace
- Q: Trace 数据如何关联用户和对话？ → A: 每个 Trace 必须携带 user_id 和 session_id，支持按用户和对话维度查询
- Q: 系统管理如何访问 Langfuse？ → A: 在系统设置页面增加"可观测性"Tab，显示 Langfuse 状态、跳转链接、Token 用量统计
- Q: Token 用量统计维度？ → A: 按用户维度统计，不按模型区分。管理员可查所有用户，普通用户只能查自己
- Q: 用量统计时间范围？ → A: 支持选择时间范围和起始日期，默认查询当天
- Q: Langfuse 环境如何配置？ → A: 支持通过界面配置 Langfuse 服务 URL，保存前验证连通性，也可手动配置 API Key

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 查看 Agent 执行链路追踪 (Priority: P1)

作为运维人员，我希望能够查看每次 Agent 调用的完整执行链路，包括意图识别、任务规划、Actor 执行等各阶段的详细信息，以便快速定位问题和优化性能。

**Why this priority**: Agent 执行链路追踪是可观测性的核心功能，直接影响问题排查效率和系统优化能力。没有 Trace 功能，其他监控功能将缺乏上下文。

**Independent Test**: 可以通过发起一次对话请求，然后在 Langfuse 界面中查看完整的 Trace 记录来独立测试此功能。

**Acceptance Scenarios**:

1. **Given** 用户发送一条消息触发 Agent 执行, **When** Agent 处理完成, **Then** 系统通过 LiteLLM Langfuse Callback 和 Langfuse SDK Span 自动记录完整的执行 Trace，包含各阶段耗时和关键参数
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

### User Story 4 - Langfuse 服务管理 (Priority: P1)

作为系统管理员，我希望能够灵活选择 Langfuse 服务来源：使用系统内置的 Langfuse 服务，或连接外部已有的 Langfuse 服务，以便根据部署环境选择最合适的方式。

**Why this priority**: Langfuse 服务是所有可观测性功能的基础，必须先有可用的服务才能进行后续操作。

**Independent Test**: 可以通过启动内置服务或配置外部服务地址，验证系统能否成功连接并获取服务状态。

**Acceptance Scenarios**:

**内置 Langfuse 服务**
1. **Given** 系统首次部署且无外部 Langfuse, **When** 管理员选择"启用内置服务", **Then** 系统自动启动 Langfuse v3 服务（含 ClickHouse、Redis、MinIO、PostgreSQL）
2. **Given** 内置 Langfuse 服务已启动, **When** 查看服务状态, **Then** 显示"内置服务运行中"及服务地址
3. **Given** 内置服务运行中, **When** 管理员选择停止内置服务, **Then** 系统停止 Langfuse 及相关组件
4. **Given** 系统重启, **When** 内置服务之前已启用, **Then** 自动恢复内置 Langfuse 服务

**外部 Langfuse 服务**
5. **Given** 管理员选择使用外部服务, **When** 输入 Langfuse 服务地址并点击验证, **Then** 系统检测连通性并显示服务版本和延迟
6. **Given** 已有外部 Langfuse 服务, **When** 管理员配置该服务地址, **Then** 系统成功连接，无需启动内置服务
7. **Given** 配置的外部服务不可用, **When** 系统检测状态, **Then** 显示"服务异常"并记录错误日志

**服务切换**
8. **Given** 当前使用内置服务, **When** 管理员切换到外部服务, **Then** 停止内置服务并连接外部服务
9. **Given** 当前使用外部服务, **When** 管理员切换到内置服务, **Then** 启动内置服务并断开外部连接

---

### User Story 4.1 - Langfuse 项目自动初始化 (Priority: P1)

作为系统管理员，我希望在选择 Langfuse 服务后，系统能够自动初始化项目并将生成的 API Key 写入 .env 配置文件，无需手动配置，以便快速开始 Trace 数据采集。

**Why this priority**: 项目自动初始化是零配置体验的关键，降低使用门槛。

**Independent Test**: 可以通过启动内置服务或连接外部服务，验证系统是否自动初始化并在 .env 中生成正确的 API Key。

**Acceptance Scenarios**:

**内置服务初始化**
1. **Given** 管理员启用内置 Langfuse 服务, **When** 服务启动完成, **Then** 系统自动创建项目、生成 API Key 并写入 .env 文件
2. **Given** 内置服务初始化成功, **When** 查看 .env 文件, **Then** 包含 LANGFUSE_PUBLIC_KEY 和 LANGFUSE_SECRET_KEY

**外部服务初始化**
3. **Given** 管理员配置外部 Langfuse 服务地址, **When** 连接验证成功, **Then** 系统自动在该服务上创建项目、生成 API Key 并写入 .env 文件
4. **Given** 外部服务初始化成功, **When** 查看 .env 文件, **Then** 包含正确的 LANGFUSE_HOST、LANGFUSE_PUBLIC_KEY 和 LANGFUSE_SECRET_KEY

**状态检测与恢复**
5. **Given** 系统已运行一段时间, **When** 重新启动 SunnyAgent, **Then** 系统从 .env 读取配置，不重复创建项目
6. **Given** .env 中已有有效的 API Key, **When** 系统启动, **Then** 直接使用现有配置，跳过初始化
7. **Given** 项目已初始化, **When** 用户从 SunnyAgent 跳转到 Langfuse, **Then** 自动携带认证信息，无需手动登录

**初始化失败处理**
8. **Given** 自动初始化失败, **When** 管理员查看系统日志, **Then** 显示详细错误信息和排查指引

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
4. **Given** 管理员点击"打开 Langfuse 控制台"链接, **When** 系统处理跳转, **Then** 在新标签页打开 Langfuse 界面并自动登录
5. **Given** 普通用户查看可观测性 Tab, **When** 查看 Langfuse 卡片, **Then** 不显示"打开 Langfuse 控制台"链接（仅管理员可见）

**Token 用量统计**
5. **Given** 用户查看 Token 用量统计区域, **When** 页面加载完成, **Then** 默认显示当天的用量数据（起始日期和终止日期均为今天）
6. **Given** 用户选择起始日期和终止日期, **When** 点击查询, **Then** 显示指定时间段内的用量统计
7. **Given** 用量数据加载完成, **When** 查看统计卡片, **Then** 显示总调用次数、总 Token 数（含输入/输出明细）、预估费用
8. **Given** 用量数据加载完成, **When** 查看趋势图, **Then** 显示按日维度的 Token 用量柱状图

**用户维度权限控制**
9. **Given** 当前用户是管理员, **When** 查看用量统计, **Then** 可以看到所有用户的汇总数据，并可按用户筛选
10. **Given** 当前用户是普通用户, **When** 查看用量统计, **Then** 只能看到自己的用量数据

**共用管理员账号**
11. **Given** 管理员点击"打开 Langfuse 控制台", **When** 系统处理跳转, **Then** 使用 .env 中配置的管理员凭据自动登录 Langfuse
12. **Given** 普通用户发起对话, **When** Trace 记录完成, **Then** Trace 中包含该用户的 user_id，管理员可在 Langfuse 中按 user_id 筛选

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

### User Story 7 - Trace 数据导出 (Priority: P2)

作为管理员或用户，我希望能够将 Trace 数据导出为 JSON 或 CSV 格式文件，以便进行离线分析、归档备份或与其他系统集成。

**Why this priority**: Trace 数据导出是数据分析和合规归档的重要功能，但依赖于基础 Trace 功能已完善。

**Independent Test**: 可以通过选择时间范围和导出格式，点击导出按钮，验证下载的文件是否包含正确的 Trace 数据。

**Acceptance Scenarios**:

1. **Given** 用户在可观测性 Tab 页面, **When** 选择时间范围并点击导出按钮, **Then** 系统生成包含指定范围内 Trace 数据的文件
2. **Given** 用户选择 JSON 格式导出, **When** 导出完成, **Then** 下载的文件为有效 JSON 格式，包含 Trace 的完整结构化数据
3. **Given** 用户选择 CSV 格式导出, **When** 导出完成, **Then** 下载的文件为 CSV 格式，包含 Trace 的扁平化关键字段
4. **Given** 当前用户是管理员, **When** 导出数据, **Then** 可以选择导出所有用户的 Trace 数据或指定用户
5. **Given** 当前用户是普通用户, **When** 导出数据, **Then** 仅能导出自己的 Trace 数据
6. **Given** 导出的数据量较大, **When** 开始导出, **Then** 系统显示进度提示，导出完成后自动下载

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
- 当导出的时间范围内无 Trace 数据时，应返回空文件并给出友好提示
- 当导出数据量超过系统限制（如 10000 条）时，应提示用户缩小时间范围或分批导出

## Requirements *(mandatory)*

### Functional Requirements

**Trace 追踪（P1）**
- **FR-001**: 系统 MUST 通过 LiteLLM 内置 Langfuse Callback 自动记录 Agent 执行链路（LLM 调用），并通过 Langfuse Python SDK `@observe()` / `start_span()` 记录非 LLM Span
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

**Langfuse 服务管理（P1）**

*内置服务*
- **FR-012**: 系统 MUST 支持启动内置的 Langfuse v3 服务（含 ClickHouse、Redis、MinIO、PostgreSQL）
- **FR-013**: 系统 MUST 支持停止内置 Langfuse 服务
- **FR-014**: 系统 MUST 在系统重启时自动恢复之前启用的内置服务
- **FR-015**: 系统 MUST 显示内置服务的运行状态和本地访问地址

*外部服务*
- **FR-016**: 系统 MUST 支持配置外部 Langfuse 服务地址（URL）
- **FR-017**: 系统 MUST 在配置服务地址时验证连通性并返回服务版本信息
- **FR-018**: 系统 MUST 在启动时自动连接已配置的外部 Langfuse 服务

*服务切换*
- **FR-019**: 系统 MUST 支持在内置服务和外部服务之间切换
- **FR-020**: 系统 MUST 在切换到外部服务时停止内置服务（如已启动）
- **FR-021**: 系统 MUST 在切换到内置服务时断开外部服务连接

**Langfuse 项目自动初始化（P1）**
- **FR-022**: 内置服务 MUST 通过 docker-compose 环境变量（`LANGFUSE_INIT_*`）预配置初始组织、项目和 API Key，Langfuse 启动时自动完成初始化
- **FR-023**: 系统 MUST 将预配置的 API Key 写入 .env 文件（LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY）
- **FR-024**: 系统 MUST 在 .env 中同时写入 LANGFUSE_HOST 配置
- **FR-025**: 系统 MUST 在启动时从 .env 读取配置，若已存在有效配置则直接使用
- **FR-026**: 外部服务场景 MUST 支持管理员手动配置已有的 API Key
- **FR-027**: 系统 SHOULD 在初始化失败时记录详细日志并提供排查指引

**Observability 改造（P1）**
- **FR-028**: 系统 MUST 改造现有 observability 代码以支持 Langfuse Trace 上报
- **FR-029**: 系统 MUST 在每个 Trace 中记录用户 ID（user_id），关联到发起请求的用户
- **FR-030**: 系统 MUST 在每个 Trace 中记录对话 ID（session_id），关联同一对话的多轮交互
- **FR-031**: 系统 MUST 确保 Span 正确嵌套，反映 Agent 执行的层级结构

**系统管理可观测性 Tab（P1）**

*Langfuse 状态与跳转*
- **FR-032**: 系统 MUST 在系统设置页面提供"可观测性"Tab
- **FR-033**: 系统 MUST 显示 Langfuse 卡片，包含服务来源（内置/外部）、描述和运行状态指示器
- **FR-034**: 系统 MUST 实时检测 Langfuse 服务状态（运行正常/服务异常/未配置）
- **FR-035**: 系统 MUST 仅对管理员显示"打开 Langfuse 控制台"链接，点击后在新标签页打开并自动登录

*Langfuse 服务管理（仅管理员）*
- **FR-036**: 系统 MUST 仅允许管理员访问 Langfuse 服务管理功能
- **FR-037**: 系统 MUST 支持选择服务来源：内置服务 或 外部服务
- **FR-038**: 系统 MUST 支持启动/停止内置 Langfuse 服务
- **FR-039**: 系统 MUST 支持配置外部 Langfuse 服务 URL 并验证连通性
- **FR-040**: 系统 MUST 在界面显示当前服务来源、地址、初始化状态和 .env 配置状态
- **FR-041**: 系统 MUST 在服务连接成功后自动触发项目初始化并写入 .env

*Token 用量统计*
- **FR-042**: 系统 MUST 显示 Token 用量统计区域，包含起始日期和终止日期选择器
- **FR-043**: 系统 MUST 支持选择起始日期和终止日期，默认均为当天
- **FR-044**: 系统 MUST 显示统计卡片：总调用次数、总 Token 数（含输入/输出明细）、预估费用
- **FR-045**: 系统 MUST 显示按日维度的 Token 用量趋势图（柱状图）
- **FR-046**: 系统 MUST 支持刷新按钮，手动更新用量数据
- **FR-047**: 系统 MUST 按用户维度统计用量，费用直接使用 Langfuse 原生 cost 字段聚合（Langfuse 内置模型价格表，按实际模型计算）

*用户权限控制*
- **FR-048**: 系统 MUST 对管理员显示所有用户的汇总数据，并支持按用户筛选
- **FR-049**: 系统 MUST 对普通用户仅显示其个人的用量数据

*Langfuse 账号管理*
- **FR-050**: 系统 MUST 通过 .env 统一管理一套 Langfuse 管理员凭据（`LANGFUSE_ADMIN_EMAIL`、`LANGFUSE_ADMIN_PASSWORD`），所有 SunnyAgent 管理员共用该账号访问 Langfuse 控制台
- **FR-051**: 内置服务 MUST 将 .env 中的管理员凭据作为 `LANGFUSE_INIT_USER_*` 传入 docker-compose，启动时自动创建 Langfuse 管理员账号
- **FR-052**: 系统 MUST 确保普通用户的对话 Trace 仍记录 user_id，供管理员在 Langfuse 中按 user_id 筛选定位

**Trace 数据导出（P2）**
- **FR-054**: 系统 MUST 支持将 Trace 数据导出为 JSON 格式
- **FR-055**: 系统 MUST 支持将 Trace 数据导出为 CSV 格式
- **FR-056**: 系统 MUST 支持按时间范围筛选导出的 Trace 数据
- **FR-057**: 系统 MUST 对管理员允许导出所有用户或指定用户的 Trace 数据
- **FR-058**: 系统 MUST 对普通用户仅允许导出其个人的 Trace 数据
- **FR-059**: 系统 SHOULD 对大数据量导出显示进度提示
- **FR-060**: 系统 MUST 在导出文件中包含 Trace 基本信息（trace_id, user_id, session_id, 时间戳, 耗时, Token 消耗）

### Non-Functional Requirements

**性能要求**
- **NFR-001**: Trace 数据上报 MUST 采用异步方式，不阻塞 Agent 主流程
- **NFR-002**: 单次 Trace 上报延迟 SHOULD 不超过 100ms（网络正常情况下）
- **NFR-003**: 系统 SHOULD 支持配置 Trace 采样率，以控制高负载场景下的数据量

**可靠性要求**
- **NFR-004**: 当 Langfuse 服务不可用时，Agent MUST 继续正常工作
- **NFR-005**: Trace 上报失败时 SHOULD 记录本地日志便于排查
- **NFR-006**: Langfuse 管理员凭据验证失败时 SHOULD 记录日志并在控制台跳转时提示管理员检查 .env 配置

**安全要求**
- **NFR-007**: Langfuse API 认证信息 MUST 通过环境变量或密钥管理服务获取，不得硬编码
- **NFR-008**: 敏感数据（如用户密码、Token）MUST NOT 被记录到 Trace 中。系统 MUST 在 Langfuse SDK flush 前通过 `before_send` hook 对 input/output 进行 PII 脱敏（正则匹配手机号、身份证号、邮箱、密码等模式并替换为 `[REDACTED]`）
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
- **SC-009**: 管理员点击控制台跳转后，3 秒内完成自动登录并打开 Langfuse 界面
- **SC-010**: SunnyAgent 首次启动时，Langfuse 自动初始化在 30 秒内完成
- **SC-011**: 100% 的 Trace 包含有效的 user_id 和 session_id
- **SC-012**: 运维人员可通过 user_id 或 session_id 在 Langfuse 中快速筛选相关 Trace
- **SC-013**: 可观测性 Tab 页面加载时间不超过 2 秒
- **SC-014**: Token 用量统计数据查询响应时间不超过 3 秒（30 天范围内）
- **SC-015**: Langfuse 服务状态检测延迟不超过 5 秒
- **SC-016**: 用户可在 30 秒内完成 1000 条 Trace 数据的导出（不含下载时间）
- **SC-017**: 导出的 JSON 文件可被标准 JSON 解析器正确解析
- **SC-018**: 导出的 CSV 文件可被 Excel 等标准工具正确打开

## Assumptions

- Langfuse 服务将被私有化部署，与 SunnyAgent 在同一网络环境
- 团队成员可以访问 Langfuse 的 Web 界面（英文界面，数据内容支持中文）
- 现有 Agent 代码基于 LiteLLM（`litellm.acompletion`）进行 LLM 调用，LiteLLM 内置 Langfuse Callback 支持
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
- 现有 AIME Agent 核心代码支持添加 Langfuse Span（通过 ContextVar 传递 Trace 上下文）
- SunnyAgent 前端系统管理页面存在（用于嵌入 Langfuse 链接）

## Architecture Decisions

1. **部署方式**: Langfuse v3 使用 Docker Compose 部署，包含 ClickHouse + Redis + MinIO + PostgreSQL 完整栈。SunnyAgent 后端通过 Docker Compose CLI（`docker compose up -d` / `docker compose down`）管理内置服务生命周期，要求宿主机预装 Docker Engine
2. **Trace 集成**: LLM 调用通过 LiteLLM 内置 Langfuse Callback 自动采集（零改动 LLMClient）；非 LLM Span（ReAct 循环、工具调用等）通过 Langfuse Python SDK 手动创建
3. **Agent 评估**: 不使用 Langfuse Playground 测试完整 Agent，而是通过 Dataset + Experiment + 自定义任务函数调用 `/api/chat`
4. **LLM-as-a-Judge**: 评估时复用 SunnyAgent 已配置的 LLM（通过环境变量），无需在 Langfuse 单独配置
5. **统一凭据管理与自动登录**: .env 中统一配置一套 Langfuse 管理员凭据（`LANGFUSE_ADMIN_EMAIL`、`LANGFUSE_ADMIN_PASSWORD`）。内置服务将其作为 `LANGFUSE_INIT_USER_EMAIL`/`LANGFUSE_INIT_USER_PASSWORD` 传入 docker-compose 创建初始账号；外部服务由管理员填写已有账号。控制台跳转时后端用该凭据调用 Langfuse 登录 API 获取 session，重定向管理员；不支持时降级为跳转登录页手动登录
6. **账号同步策略**: 所有 SunnyAgent 管理员共用一个 Langfuse 管理员账号访问控制台，不做逐人同步。普通用户不能访问 Langfuse 控制台，但其对话 Trace 通过 user_id 记录，管理员可在 Langfuse 中按 user_id 筛选
7. **Span 处理模式**: 在 async generator 中使用直接 span 引用（`start_span()`/`start_generation()`）而非上下文管理器，避免 OpenTelemetry context 丢失问题（详见 research.md）
7.1. **Trace 数据脱敏**: 在 Langfuse SDK flush 前通过 `before_send` hook 过滤 PII 数据（手机号、身份证号、邮箱、密码等）。使用正则匹配替换敏感模式，确保 NFR-008 合规。脱敏规则可通过 `LANGFUSE_PII_PATTERNS` 环境变量扩展自定义模式
8. **两种部署场景 + 自动初始化**：
   - **内置服务场景**：通过 docker-compose 环境变量（`LANGFUSE_INIT_ORG_ID`、`LANGFUSE_INIT_PROJECT_NAME`、`LANGFUSE_INIT_PROJECT_PUBLIC_KEY`、`LANGFUSE_INIT_PROJECT_SECRET_KEY` 等）预配置初始化参数，Langfuse 启动时自动创建组织、项目和 API Key，无需运行时调用 Admin API
   - **外部服务场景**：配置外部服务地址 → 验证连接 → 管理员手动提供已有的 API Key（不自动在外部实例上创建项目）

   API Key 统一通过 .env 文件管理，内置服务的 Key 由 docker-compose 预配置生成

9. **单项目 + user_id 隔离策略**：
   - **项目设计**：一个 SunnyAgent 实例对应一个 Langfuse 项目，所有用户的 Trace 存储在同一项目中
   - **用户标识**：每个 Trace 携带 user_id 和 session_id，用于标识对话归属
   - **权限控制**：
     - 管理员：可访问 Langfuse 控制台，查看所有用户的 Trace，通过 user_id 筛选定位问题
     - 普通用户：不能访问 Langfuse 控制台，只能在 SunnyAgent 界面查看自己的用量统计
   - **账号同步**：只同步管理员到 Langfuse，普通用户不同步（但其对话 Trace 会记录 user_id）
10. **Trace 用户关联**: 每个 Trace 必须携带 `user_id` 和 `session_id`，其中 `user_id` 从请求上下文获取，`session_id` 由对话管理模块生成
11. **存储设计**: Trace 原始数据存储在 Langfuse（ClickHouse），SunnyAgent 仅存储 Langfuse 配置信息（`langfuse_config` 表）。用量查询通过 Langfuse Public API + Redis 缓存实现，不维护本地索引
12. **配置 Source of Truth**: 数据库 `langfuse_config` 表为运行时配置的唯一权威来源。`.env` 文件仅用于**首次初始化种子值**和 Docker Compose 环境变量注入。运行时配置变更（通过管理 API）只写数据库，不修改 .env 文件。启动时优先读数据库，数据库无记录时回退读 .env 并写入数据库
13. **Secret Key 加密存储**: 使用 `cryptography.fernet.Fernet` 对称加密存储 Langfuse Secret Key。加密密钥从环境变量 `ENCRYPTION_KEY` 获取（必填项，首次部署时自动生成并写入 .env）。提供 `encrypt_secret()` / `decrypt_secret()` 工具函数

## Risks & Mitigations

| 风险 | 影响 | 可能性 | 缓解措施 |
|------|------|--------|----------|
| Langfuse 服务宕机影响 Agent 可用性 | 高 | 中 | 异步上报 + 优雅降级，Langfuse 不可用时 Agent 继续正常工作 |
| Trace 数据量过大导致存储成本增加 | 中 | 高 | 支持采样率配置，生产环境可设置较低采样率 |
| .env 管理员凭据泄露 | 高 | 低 | .env 文件权限控制（600），生产环境使用密钥管理服务 |
| SDK 版本升级带来兼容性问题 | 中 | 中 | 锁定 SDK 版本，升级前在测试环境验证 |
| 敏感信息泄露到 Trace | 高 | 低 | 实现数据脱敏层，过滤密码、Token 等敏感字段 |
| LLM-as-a-Judge 评估不准确 | 低 | 中 | 支持人工复核评分，评估结果仅作参考 |
| 自动初始化失败导致 Trace 功能不可用 | 高 | 低 | 提供手动配置备选方案，启动时重试机制 |
| API Key 泄露导致安全风险 | 高 | 低 | 加密存储 Secret Key，定期轮换机制 |
| Observability 改造影响现有功能 | 中 | 中 | 充分测试，保持向后兼容，支持开关切换 |

## Integration Interfaces

### SunnyAgent → Langfuse SDK

**Trace 上报接口**
- LLM 调用通过 LiteLLM 内置 Langfuse Callback 自动采集
- 非 LLM Span 使用 Langfuse Python SDK `@observe()` 装饰器或 `start_span()` 手动创建
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

**管理员登录接口**
- `POST /api/auth/sign-in` - 使用 .env 中的管理员凭据登录 Langfuse，获取 session token（用于控制台跳转自动登录）

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

#### 管理员登录接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/auth/sign-in` | POST | 使用管理员凭据登录，获取 session token |

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

**Langfuse 存储**
- Trace/Span 原始数据存储在 ClickHouse
- 用户/项目元数据存储在 PostgreSQL

**Redis 缓存**
- 用量统计结果按 user_id + 时间维度缓存（5min TTL），避免频繁调用 Langfuse API
