# Feature Specification: 插件管理系统 (Plugin Management)

**Feature Branch**: `026-plugin-management`
**Created**: 2026-03-10
**Status**: Draft
**Input**: 开发和优化插件管理界面与交互体验，包括浏览、启用/禁用、上传、命令调用和 Workflow Skill 执行

## 领域模型

系统有两类扩展机制：**Plugin（插件）** 和 **独立 Skill（技能）**。

### Plugin（插件）

Plugin 是顶层扩展单元，包含两类子资源：

- **Command**：用户显式调用的 /命令，每个 command 对应一个 COMMAND.md 文件，定义工作流指引。Command 触发时走 **workflow skill** 路径 — Planner 读取步骤定义，进行多步骤任务规划和执行。
- **Skill**：LLM 自主调用的能力单元，每个 skill 对应一个 SKILL.md 文件。Skill 走 **capability skill** 路径 — 通过 skill_call 工具直接调用，单次执行。

```
Plugin (插件)
├── commands/          ← Command（workflow skills）
│   ├── analyze.md
│   └── build-report.md
└── skills/            ← Skill（capability skills）
    ├── data-extractor/
    │   └── SKILL.md
    └── chart-builder/
        └── SKILL.md
```

### 独立 Skill（技能）

独立 Skill 不从属于任何 Plugin，在 Skills 管理界面中独立管理。分为系统预置（Examples）和用户上传（My skills）两类。独立 Skill 可被 LLM 在任何对话中通过 skill_call 自主调用，不需要命令触发。

```
Standalone Skill (独立技能)
└── {skill-name}/
    ├── SKILL.md           ← 技能说明和指令
    ├── scripts/           ← 可选脚本目录
    └── LICENSE.txt        ← 可选
```

### Skill 类型对照

| 类型 | 归属 | 触发方式 | 执行方式 | 管理粒度 |
|------|------|---------|---------|---------|
| Plugin Command | Plugin 子资源 | 用户输入 `/{plugin}:{command}` | Planner 多步骤规划执行 | 随 Plugin 启用/禁用 |
| Plugin Skill | Plugin 子资源 | LLM 在命令执行中自主调用 | 单次执行，返回指令路径 | 随 Plugin 启用/禁用 |
| 独立 Skill | 独立存在 | LLM 在任意对话中自主调用 | 单次执行，返回指令路径 | 单独启用/禁用 |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 浏览已安装插件 (Priority: P1)

用户需要在统一的管理界面中查看所有可用的插件，了解每个插件包含的 Commands 和 Skills，以便决定启用哪些能力。

**Why this priority**: 可见性是所有管理操作的基础，用户无法管理看不到的资源

**Independent Test**: 用户登录后进入插件管理页面，可以看到所有插件列表，点击插件可查看其 Commands 和 Skills

**Acceptance Scenarios**:

1. **Given** 用户已登录, **When** 进入插件管理页面, **Then** 左侧边栏显示所有可用的插件列表（系统预置 + 用户上传），用户上传的插件标注 "Uploaded" 徽章
2. **Given** 插件列表已显示, **When** 点击某个插件, **Then** 左侧展开子导航（Commands、Skills），右侧面板显示该插件详情（名称、版本、作者、描述）
3. **Given** 插件详情面板显示, **When** 查看 Commands 标签页, **Then** 以卡片网格展示该插件的所有 /命令（每张卡片含描述 + 命令名称）
4. **Given** 插件详情面板显示, **When** 切换到 Skills 标签页, **Then** 展示该插件的所有 capability skills
5. **Given** 插件详情面板显示, **When** 查看 "Try asking..." 区域, **Then** 显示该插件的示例问题列表，点击可快速发起对话

---

### User Story 2 - 对话中使用命令调用插件 (Priority: P1)

用户在对话窗口中通过 `/{plugin_name}:{command_name}` 格式调用插件命令。系统注入 COMMAND.md 工作流指引到系统提示词，并将该插件的 capability skills 加载到上下文中供 LLM 在执行过程中自主调用。

**Why this priority**: 这是插件系统的核心用户交互方式，直接体现插件价值

**Independent Test**: 用户在对话框输入 `/{plugin_name}:{command_name}`，系统注入命令指引并加载插件 skills 上下文

**Acceptance Scenarios**:

1. **Given** 用户在对话输入框, **When** 输入 `/` 字符, **Then** 显示可用的插件命令自动完成列表
2. **Given** 自动完成列表显示, **When** 选择或输入完整的 `{plugin_name}:{command_name}`, **Then** 该命令被标记到消息中
3. **Given** 消息包含命令标记, **When** 发送消息, **Then** 后端读取 COMMAND.md 内容注入系统提示词，同时将该插件 skills/ 目录下的 capability skills 加载到执行上下文
4. **Given** 命令执行过程中, **When** LLM 判断需要某个 capability skill, **Then** 通过 skill_call 工具自主调用该插件的 skill
5. **Given** 用户输入不存在的命令, **When** 发送消息, **Then** 系统提示命令不存在

---

### User Story 3 - 启用/禁用插件 (Priority: P2)

用户需要控制哪些插件对自己可用，实现个性化工作环境。禁用插件时，其所有 Commands 和 Skills 同时不可用。每个用户拥有独立的启用/禁用设置。

**Why this priority**: 提供灵活的个性化控制，但依赖于 Story 1 的浏览功能

**Independent Test**: 用户禁用某个插件后，该插件的 Commands 不出现在自动完成列表中，其 Skills 也不会被 LLM 调用

**Acceptance Scenarios**:

1. **Given** 插件详情面板显示, **When** 用户切换插件的启用开关, **Then** 该插件状态立即更新
2. **Given** 某插件被禁用, **When** 用户在对话框输入 `/`, **Then** 该插件的 Commands 不出现在自动完成列表
3. **Given** 某插件被禁用, **When** 用户发起新对话, **Then** 该插件的 capability skills 不出现在 skill_call 可用列表中
4. **Given** 某插件被禁用, **When** 用户重新启用, **Then** 该插件的 Commands 和 Skills 恢复可用
5. **Given** 用户 A 禁用了某插件, **When** 用户 B 查看同一插件, **Then** 用户 B 的设置不受影响

---

### User Story 4 - 上传插件包 (Priority: P3)

用户通过上传 ZIP 文件包安装自定义插件，扩展系统功能。上传的插件包包含 commands/ 和 skills/ 目录，系统自动解析并注册所有子资源。上传的插件仅对上传者可见。

**Why this priority**: 自定义扩展能力，但依赖于浏览和命令调用功能

**Independent Test**: 上传有效的插件包后，该插件出现在用户的插件列表中，其 Commands 和 Skills 可立即使用

**Acceptance Scenarios**:

1. **Given** 用户在插件管理页面, **When** 点击 "+" 按钮并选择 "Upload plugin", **Then** 弹出上传对话框，显示安全警告和拖拽上传区
2. **Given** 上传对话框打开, **When** 拖拽或选择有效 ZIP 包并点击 Upload, **Then** 系统验证、解压并注册该插件及其 commands 和 skills
3. **Given** 上传了有效插件包, **When** 上传完成, **Then** 插件出现在用户的插件列表中并标注 "Uploaded" 徽章，展开可见 Commands 和 Skills
4. **Given** 用户上传无效的包（缺少必要文件、路径穿越、格式错误）, **When** 上传完成, **Then** 显示具体错误提示，不注册任何内容
5. **Given** 上传的插件与用户已有插件同名, **When** 上传, **Then** 覆盖更新原版本（commands 和 skills 同步更新）

---

### User Story 5 - Workflow 命令执行 (Priority: P2)

用户调用插件命令时，系统通过 Planner 按 COMMAND.md 中定义的步骤进行多步骤任务规划和执行。执行过程中，LLM 可自主调用该插件的 capability skills 完成各步骤所需的原子操作。

**Why this priority**: Workflow 执行是命令系统的核心价值，将多步骤复杂任务自动化

**Independent Test**: 用户调用一个包含多步骤的命令后，系统按步骤规划并执行，期间自动调用所需的 capability skills

**Acceptance Scenarios**:

1. **Given** 用户调用了一个插件命令, **When** 消息发送, **Then** Planner 读取 COMMAND.md 中的步骤定义进行任务规划
2. **Given** 命令定义了多个步骤, **When** Planner 规划任务, **Then** 按步骤顺序生成执行计划
3. **Given** 执行计划已生成, **When** 开始执行, **Then** 依次执行每个步骤并显示进度
4. **Given** 某步骤需要特定能力, **When** 执行该步骤, **Then** LLM 通过 skill_call 自动调用该插件的相应 capability skill
5. **Given** 命令执行完成, **When** 所有步骤结束, **Then** 汇总各步骤结果返回给用户

---

### User Story 6 - 浏览和管理独立 Skills (Priority: P2)

用户需要在 Skills 管理界面中查看所有可用的独立技能（系统预置 Examples + 用户上传 My skills），了解每个技能的描述和 SKILL.md 内容，并控制其启用/禁用状态。

**Why this priority**: 独立 Skills 是 LLM 能力的基础组成部分，用户需要可见性和控制权

**Independent Test**: 用户点击 Skills 导航进入 Skills 管理界面，可以看到所有技能列表，选中技能查看详情，切换启用/禁用开关

**Acceptance Scenarios**:

1. **Given** 用户在管理页面, **When** 点击左侧 "Skills" 导航, **Then** 显示三栏布局：左侧边栏不变、中间 Skills 列表（分 "My skills" / "Examples" 两组）、右侧 Skill 详情
2. **Given** Skills 列表显示, **When** 点击某个 skill, **Then** 右侧显示该 skill 详情（名称、Added by、Description、SKILL.md 内容预览）
3. **Given** Skills 列表中有可展开的 skill, **When** 点击展开箭头, **Then** 显示该 skill 包含的文件列表（SKILL.md, scripts/, LICENSE.txt 等）
4. **Given** Skill 详情面板显示, **When** 用户切换启用/禁用 Toggle, **Then** 该 skill 状态立即更新
5. **Given** 某独立 skill 被禁用, **When** 用户发起新对话, **Then** 该 skill 不出现在 skill_call 可用列表中
6. **Given** 用户 A 禁用了某系统 skill, **When** 用户 B 查看, **Then** 用户 B 的设置不受影响
7. **Given** Skills 列表头部, **When** 点击搜索图标并输入关键词, **Then** 实时过滤匹配的 skill

---

### User Story 7 - 上传独立 Skill 包 (Priority: P3)

用户通过上传 ZIP 文件包安装自定义独立技能。上传的技能出现在 "My skills" 分组中，仅对上传者可见。

**Why this priority**: 自定义技能扩展，但依赖于 Skills 浏览功能

**Independent Test**: 上传包含 SKILL.md 的有效 ZIP 包后，该技能出现在 "My skills" 列表中且默认启用

**Acceptance Scenarios**:

1. **Given** 用户在 Skills 管理界面, **When** 点击 "+" 按钮, **Then** 弹出上传对话框
2. **Given** 上传对话框打开, **When** 拖拽或选择包含 SKILL.md 的有效 ZIP 包并上传, **Then** 系统验证、解压并注册该技能
3. **Given** 上传了有效技能包, **When** 上传完成, **Then** 技能出现在 "My skills" 分组中且默认启用
4. **Given** 用户上传无效的包（缺少 SKILL.md）, **When** 上传完成, **Then** 显示错误提示
5. **Given** 上传的技能与用户已有技能同名, **When** 上传, **Then** 覆盖更新原版本

---

### User Story 8 - 浏览插件市场 (Priority: P3)

用户需要浏览所有可安装的插件，发现新插件并选择安装到自己的工作环境。

**Why this priority**: 发现和获取新插件的入口，但不影响核心功能使用

**Independent Test**: 用户点击添加按钮后可以浏览所有可用插件，按分类筛选和搜索

**Acceptance Scenarios**:

1. **Given** 用户在插件管理页面, **When** 点击 "+" 按钮并选择 "Browse plugins", **Then** 打开插件市场弹窗
2. **Given** 插件市场弹窗打开, **When** 切换标签页（Preset / Uploaded）, **Then** 显示对应分类的插件列表
3. **Given** 插件市场弹窗打开, **When** 在搜索框输入关键词, **Then** 实时过滤显示匹配的插件
4. **Given** 插件市场显示某个已安装的插件, **When** 查看该插件卡片, **Then** 显示 "Manage" 按钮跳转到详情页

---

### Edge Cases

- 上传的 ZIP 包格式错误（非 zip、损坏、包含路径穿越攻击）时的安全处理
- 插件正在被对话使用时禁用该插件的行为（当前对话继续，新对话不可用）
- 上传的包大小超过限制（当前限制 10MB）
- 并发上传同名插件包的幂等处理
- 插件的 skills/ 目录为空或某个 skill 缺少 SKILL.md 时的降级处理
- 用户删除自己上传的插件时，关联的 commands、skills 和 settings 级联清理
- 系统预置插件不允许用户删除，仅允许启用/禁用
- 插件包中 commands/ 和 skills/ 目录都不存在时的处理
- 独立 skill 正在被对话使用时禁用该 skill 的行为
- 独立 skill 与插件内 skill 同名时的冲突处理
- 用户删除自己上传的独立 skill 时，关联的 user_skill_settings 级联清理

## Requirements *(mandatory)*

### Functional Requirements

**插件浏览与详情**：
- **FR-001**: 系统 MUST 在管理页面展示用户可见的所有插件列表（系统预置 + 用户上传）
- **FR-002**: 每个插件项 MUST 显示名称、来源、启用状态和 commands 数量
- **FR-003**: 点击插件 MUST 展示详情面板，包含名称、版本、描述、Commands 列表和 Skills 列表

**启用/禁用控制**：
- **FR-004**: 用户 MUST 能够在插件级别切换启用/禁用状态
- **FR-005**: 禁用插件时 MUST 同时禁用其所有 Commands 和 Skills
- **FR-006**: 启用/禁用状态 MUST 按用户独立存储
- **FR-007**: 禁用的插件的 Commands MUST 不出现在自动完成列表中
- **FR-008**: 禁用的插件的 Skills MUST 不出现在 skill_call 可用列表中

**命令调用（workflow skill 路径）**：
- **FR-009**: 对话输入框 MUST 支持 `/` 触发命令自动完成
- **FR-010**: 自动完成列表 MUST 仅显示用户已启用插件的 commands
- **FR-011**: 命令格式 MUST 为 `/{plugin_name}:{command_name}`
- **FR-012**: 后端 MUST 读取 COMMAND.md 内容注入系统提示词作为工作流指引
- **FR-013**: 后端 MUST 扫描该插件的 skills/ 目录，将 capability skills 列表加载到执行上下文

**Capability Skill 调用**：
- **FR-014**: 命令执行过程中，LLM MUST 能通过 skill_call 工具调用当前插件的 capability skills
- **FR-015**: skill_call MUST 返回 SKILL.md 路径和 scripts 目录路径（pull 模式），由 LLM 自主读取和执行
- **FR-016**: 非当前插件的 skills MUST 不被注入到插件命令执行上下文

**插件上传**：
- **FR-017**: 系统 MUST 支持上传 ZIP 格式的插件包
- **FR-018**: 系统 MUST 验证上传包的安全性（路径穿越检测、格式验证）
- **FR-019**: 上传成功后系统 MUST 自动注册插件，解析 commands/ 目录注册 Commands，扫描 skills/ 目录注册 Skills
- **FR-020**: 上传的插件 MUST 仅对上传者可见（owner_usernumb 隔离）
- **FR-021**: 同名插件上传 MUST 覆盖更新（commands 和 skills 同步更新）

**删除管理**：
- **FR-022**: 用户 MUST 只能删除自己上传的插件
- **FR-023**: 删除插件时 MUST 级联删除其所有 commands 和 skills 记录
- **FR-024**: 删除操作 MUST 同时清理文件系统中的插件目录（包含 commands/ 和 skills/）

**Workflow 执行**：
- **FR-025**: 命令触发时，Planner MUST 读取 COMMAND.md 中的步骤定义进行多步骤任务规划
- **FR-026**: Planner MUST 按步骤顺序生成执行计划并依次执行
- **FR-027**: 执行过程中 LLM MUST 能自动调用该插件的 capability skills 完成各步骤
- **FR-028**: Workflow 执行过程 MUST 向用户显示当前步骤进度

**插件市场**：
- **FR-029**: 系统 MUST 提供插件浏览弹窗，展示所有可安装的插件
- **FR-030**: 弹窗 MUST 支持按来源分类（Preset / Uploaded）
- **FR-031**: 弹窗 MUST 提供搜索功能过滤插件

**独立 Skill 浏览与管理**：
- **FR-032**: 系统 MUST 提供独立的 Skills 管理界面（三栏布局：边栏 + 技能列表 + 技能详情）
- **FR-033**: 技能列表 MUST 分为 "My skills"（用户上传）和 "Examples"（系统预置）两组
- **FR-034**: 选中技能 MUST 展示详情面板（名称、Added by、Description、SKILL.md 内容预览）
- **FR-035**: 技能列表项 MUST 支持展开显示包含的文件列表（SKILL.md, scripts/, LICENSE.txt 等）
- **FR-036**: Skills 列表 MUST 提供搜索功能过滤技能

**独立 Skill 启用/禁用**：
- **FR-037**: 用户 MUST 能够在 Skill 详情面板中切换启用/禁用状态
- **FR-038**: 启用/禁用状态 MUST 按用户独立存储（user_skill_settings）
- **FR-039**: 系统预置 skill 的默认启用状态 MUST 由 is_default_enabled 字段决定
- **FR-040**: 禁用的独立 skill MUST 不出现在 skill_call 可用列表中

**独立 Skill 上传**：
- **FR-041**: Skills 列表头部 MUST 提供 "+" 按钮用于上传独立 skill 包
- **FR-042**: 系统 MUST 支持上传 ZIP 格式的 skill 包
- **FR-043**: 系统 MUST 验证 skill 包包含 SKILL.md 文件
- **FR-044**: 上传成功后系统 MUST 自动注册技能（UPSERT 策略）并默认启用
- **FR-045**: 上传的独立 skill MUST 仅对上传者可见（scope=user, owner_usernumb 隔离）
- **FR-046**: skill 文件更新 MUST 采用原子写入（临时文件 → 备份 → 重命名）

**独立 Skill 删除**：
- **FR-047**: 用户 MUST 只能删除自己上传的独立 skill
- **FR-048**: 删除独立 skill 时 MUST 级联删除关联的 user_skill_settings 记录
- **FR-049**: 删除操作 MUST 同时清理文件系统中的 skill 目录

**管理员控制**：
- **FR-050**: 管理员 MUST 能够通过 is_active 字段全局禁用插件或独立 skill
- **FR-051**: 管理员禁用的资源 MUST 对所有用户不可见，优先级高于用户个人设置

### Key Entities

- **Plugin**: 插件实体（顶层容器） — 名称（用户范围内唯一）、版本、描述、所属用户、存储路径、激活状态；包含 Commands 和 Skills 两类子资源
- **Command**: 插件命令（workflow skill） — 名称（插件范围内唯一）、描述、参数提示、COMMAND.md 文件路径；由用户通过 `/{plugin}:{command}` 显式触发，走 Planner 多步骤执行
- **Skill（插件内）**: 插件技能（capability skill） — 名称、描述、SKILL.md 路径、可选 scripts/ 目录；由 LLM 在命令执行过程中通过 skill_call 自主调用；随 Plugin 启用/禁用
- **Skill（独立）**: 独立技能 — 名称（全局唯一）、描述、SKILL.md 路径、作用域（system/user）、所属用户、激活状态、默认启用状态、是否含脚本；可被 LLM 在任意对话中自主调用；单独启用/禁用
- **UserPluginSetting**: 用户级插件设置 — 关联 usernumb + plugin_id，存储用户个人的 is_enabled 开关；控制整个插件（含所有 Commands 和 Skills）的可用性
- **UserSkillSetting**: 用户级技能设置 — 关联 usernumb + skill_id，存储用户个人的 is_enabled 开关；控制独立 Skill 的可用性

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 用户可在 3 秒内看到完整的插件列表及其 Commands/Skills 概览
- **SC-002**: 插件和独立 Skill 的启用/禁用操作在 1 秒内生效，无需刷新页面或重启服务
- **SC-003**: 上传 10MB 以内的插件/Skill 包在 5 秒内完成验证、存储和注册
- **SC-004**: 命令自动完成在输入 `/` 后 500ms 内显示可用列表
- **SC-005**: 90% 的用户能在无额外指导下完成插件或 Skill 上传操作
- **SC-006**: 插件和独立 Skill 的启用/禁用状态在服务重启后保持一致
- **SC-007**: Skills 管理界面可在 3 秒内加载完整的技能列表

## UI 设计参考（基于 Claude.ai 插件界面）

> 以下为 Claude.ai 插件管理界面截图的布局分析，作为 SunnyAgent 的设计参考。SunnyAgent 为自部署系统，需做适当调整。

### 页面结构（双栏布局）

**左侧边栏**：
- 顶部标题 "Customize" + 返回箭头
- 全局导航：Skills、Connectors（跨插件维度）
- "Personal plugins" 分区 + "+" 添加按钮
- 插件列表：每项显示图标 + 名称 + commands 数量，用户上传的插件标注 "Uploaded" 徽章，禁用的插件标注 "Disabled" 徽章并降低视觉对比度
- 选中插件后展开子导航：Commands、Skills

**右侧详情面板**：
- 标题区：插件名称 + 操作栏（启用/禁用 Toggle + ... 更多菜单，菜单含"删除插件"选项）
- 元数据行：Version（版本）、Author（作者）
- Description：插件描述文本
- 标签页切换：Commands / Skills
- Commands 内容区：
  - 说明文字 "Use these shortcuts to trigger an entire workflow..."
  - 3 列卡片网格，每张卡片含描述 + /命令名称
- "Try asking..." 区域：示例问题列表，点击可快速使用（每项带右箭头图标）

### "+" 添加菜单（下拉弹出）

- Browse plugins：打开插件市场弹窗
- Upload plugin：打开上传弹窗
- Create with Claude：AI 辅助创建插件（可选，后续版本考虑）

### Browse Plugins 弹窗（模态框）

- 标题："Browse plugins"
- 副标题说明插件用途
- 标签页：By Anthropic & Partners / Personal（SunnyAgent 映射为 Preset / Uploaded）
- 右侧搜索框
- 关闭按钮（右上角 X）
- 内容区：插件卡片网格

### Upload 弹窗（模态框）

- 标题："Upload local plugin"
- 红色安全警告横幅：提示用户确保信任插件后再安装，上传的插件不受 SunnyAgent 控制
- 拖拽上传区（虚线边框）+ 上传图标 + "Drag and drop or click to upload"
- "Browse files" 按钮
- 底部操作：Cancel / Upload 按钮

### Upload Skill 弹窗（模态框）

- 标题："Upload skill"
- 提示横幅：说明 Skill 目录必须包含 SKILL.md 文件，可选 scripts/ 目录，上传后默认启用
- 拖拽上传区（虚线边框）+ 上传图标 + "Drag and drop or click to upload"
- 格式提示：支持 .zip 格式，目录需包含 SKILL.md
- "Browse files" 按钮
- 底部操作：Cancel / Upload 按钮

### Skills 管理界面（三栏布局）

点击左侧边栏 "Skills" 导航后，右侧区域切换为三栏布局：

**中间列（Skills 列表面板）**：
- 标题 "Skills" + 搜索图标 + "+" 添加按钮
- "My skills" 分组（可折叠）：用户上传的独立 skill
- "Examples" 分组（可折叠）：系统预置的独立 skill
- 每项显示文件图标 + skill 名称，选中时高亮
- 选中的 skill 可展开显示文件列表（SKILL.md, LICENSE.txt 等）

**右侧列（Skill 详情面板）**：
- 标题区：skill 名称 + 启用/禁用 Toggle + ... 更多菜单（菜单含"删除技能"选项，仅用户上传的 skill 可删除）
- "Added by" 元数据
- "Description"（带 info 图标）：skill 描述文本
- 分隔线
- SKILL.md 内容预览区：工具栏（Preview / Source 切换）+ 渲染的 markdown 内容

### SunnyAgent 适配调整

| Claude.ai | SunnyAgent | 说明 |
|-----------|------------|------|
| By Anthropic & Partners / Personal | Preset / Uploaded | 分类标签页映射 |
| "Local" 徽章 | "Uploaded" 徽章 | 标识用户上传的插件 |
| Update / Customize 按钮 | 不实现（v1.0） | 用户可删除后重新上传 |
| Create with Claude | 后续版本考虑 | AI 辅助创建插件 |
| Connectors 子导航 | 不实现（v1.0） | 当前不支持连接器 |
| Version / Author | 从 plugin.json 元数据读取 | 默认 "1.0.0" / "Unknown" |

## Assumptions

- 插件包目录结构：根目录包含 plugin.json 元数据，commands/ 目录存放 COMMAND.md 文件，skills/ 目录存放 capability skill 子目录（各含 SKILL.md）
- Plugin 是插件管理的最小单位 — 启用/禁用在插件级别操作，不支持单独禁用插件内的某个 Command 或 Skill
- 独立 Skill 的管理粒度为单个 Skill — 每个独立 Skill 可单独启用/禁用
- 独立 Skill 包目录结构：根目录包含 SKILL.md，可选 scripts/ 目录和其他文件
- 独立 Skill 名称验证规则与插件名称相同：`^[a-z][a-z0-9-]{0,62}$`
- 所有登录用户都可以访问插件管理功能
- 上传的包大小限制为 10MB
- 插件名称验证规则：`^[a-z][a-z0-9-]{0,62}$`（小写字母开头，可含数字和连字符，最长 63 字符）
- 系统预置插件由管理员管理，用户仅能控制启用/禁用
