# Feature Specification: Project Management

**Feature Branch**: `006-project-management`
**Created**: 2026-02-17
**Updated**: 2026-03-04
**Status**: Implemented

**Input**: User description: "项目管理功能,以用户权限为单位,左侧导航与历史对话同级,支持项目增删改、文件上传、对话关联"

## Clarifications

### Session 2026-02-17

- Q: What file types are supported for project sources? → A: Documents + Code (PDF, DOCX, TXT, MD, CSV, JSON, common code files)
- Q: Can users create projects with duplicate names? → A: No, unique names required per user
- Q: Maximum files per project? → A: 50 files per project

## User Scenarios & Testing *(mandatory)*

### User Story 1 - 项目基础管理 (Priority: P1) ✅ Implemented

用户需要能够创建、编辑和删除项目,项目作为组织工作的基本单元。用户登录后可以在左侧导航看到项目列表,点击可以进行项目操作。

**Why this priority**: 项目是整个功能的核心实体,没有项目就无法进行后续的文件管理和对话关联。这是最基础的功能,必须首先实现。

**Independent Test**: 可以通过创建一个新项目、修改其名称、然后删除来完整测试,无需其他功能即可验证核心价值。

**Acceptance Scenarios**:

1. ✅ **Given** 用户已登录, **When** 用户点击"新建项目"按钮并填写项目名称, **Then** 系统创建新项目并显示在项目列表中
2. ✅ **Given** 项目已存在, **When** 用户点击项目设置并修改名称, **Then** 项目名称更新成功
3. ✅ **Given** 项目已存在, **When** 用户选择删除项目并确认, **Then** 项目及其关联数据被删除
4. ✅ **Given** 用户A创建了项目, **When** 用户B尝试访问该项目, **Then** 系统拒绝访问并返回权限错误

---

### User Story 2 - 项目工作区界面 (Priority: P1) ✅ Implemented

用户点击项目后进入项目工作区,采用双栏布局:
- **左侧 Sources 面板**: 文件源管理列表,支持上传文件、多选文件、可收起
- **右侧 Chat 面板**: 对话窗口,复用现有对话实现

**Why this priority**: 工作区是用户与项目交互的主要入口,与项目管理同等重要。

**Independent Test**: 可以通过创建项目后点击进入,验证双栏布局正常显示,Sources 和 Chat 功能可用。

**Acceptance Scenarios**:

1. ✅ **Given** 项目已存在, **When** 用户点击项目名称, **Then** 系统显示项目工作区,左侧为 Sources 面板,右侧为 Chat 面板
2. ✅ **Given** 用户在项目工作区, **When** 用户点击 Sources 面板的收起按钮, **Then** Sources 面板收起,Chat 面板扩展占满宽度
3. ✅ **Given** 用户在项目工作区, **When** 用户在 Chat 面板发送消息, **Then** 对话功能与现有对话保持一致
4. ✅ **Given** 项目有关联文件, **When** 用户在 Chat 输入框看到, **Then** 显示已选择的文件数量(如 "1 source")

---

### User Story 3 - 项目导航集成 (Priority: P1) ✅ Implemented

项目列表与历史对话(History)在左侧导航中同级展示。用户可以:
- 展开项目查看其下的所有对话
- 在导航树上将对话从项目中移除
- 从历史对话列表将对话添加到项目

**Why this priority**: 导航是用户体验的关键部分,需要与现有界面无缝集成,对话与项目的关联管理是核心交互。

**Independent Test**: 可以验证左侧导航正确显示Projects和History,并测试对话的添加/移除项目操作。

**Acceptance Scenarios**:

1. ✅ **Given** 用户已登录, **When** 用户查看左侧导航, **Then** 看到Projects和History两个并列的导航项
2. ✅ **Given** 用户有多个项目, **When** 用户展开Projects, **Then** 看到所有属于该用户的项目列表
3. ✅ **Given** 项目有关联的对话, **When** 用户展开某个项目, **Then** 看到该项目下的所有对话列表
4. ✅ **Given** 项目下有对话, **When** 用户右键点击对话并选择"从项目移除", **Then** 对话从项目中移除,回到History列表
5. ✅ **Given** 用户在History列表, **When** 用户右键点击对话并选择"添加到项目", **Then** 显示项目选择菜单
6. ✅ **Given** 用户选择了目标项目, **When** 确认添加, **Then** 对话关联到该项目,在项目下显示
7. ✅ **Given** 用户在某个项目中, **When** 用户点击History, **Then** 系统切换到历史对话列表

---

### User Story 4 - 文件源管理 (Priority: P2) ✅ Implemented

用户可以在项目 Sources 面板管理文件:上传新文件、查看文件列表、多选文件作为对话上下文、删除文件。

**文件存储策略**: 项目文件采用永久存储,按 `用户ID/项目ID/文件名` 的目录结构组织,确保文件持久化且便于管理。

**Why this priority**: 文件管理是项目的重要组成部分,但可以在基础功能完成后再实现。

**Independent Test**: 可以通过上传一个文件、在列表中勾选、然后删除来验证。

**Acceptance Scenarios**:

1. ✅ **Given** 用户在项目工作区, **When** 用户点击"+ Add sources"按钮并选择文件, **Then** 文件上传成功并永久存储在用户/项目目录下
2. ✅ **Given** Sources 列表有文件, **When** 用户查看文件列表, **Then** 看到文件图标、文件名(支持截断显示长文件名)
3. ✅ **Given** Sources 列表有多个文件, **When** 用户勾选文件复选框, **Then** 文件被选中,Chat 输入框显示选中的文件数量
4. ✅ **Given** Sources 列表有文件, **When** 用户点击"Select all sources"复选框, **Then** 所有文件被选中/取消选中
5. ✅ **Given** 文件已选中, **When** 用户在 Chat 发送消息, **Then** 选中的文件作为上下文传递给对话
6. ✅ **Given** Sources 列表有文件, **When** 用户选择删除文件, **Then** 文件从项目中移除,同时从存储目录删除
7. ✅ **Given** 用户重新登录或刷新页面, **When** 用户进入项目工作区, **Then** 之前上传的文件仍然存在

---

### Edge Cases

- ✅ 用户删除项目时,关联的文件和对话如何处理?文件级联删除(包括存储目录),对话解除关联回到History
- ✅ 用户在项目中上传同名文件时,系统应提示是否覆盖或自动重命名
- ✅ 用户在没有任何项目时访问Projects页面,应显示空状态引导创建
- ✅ 用户在项目加载过程中切换页面,应正确取消请求避免内存泄漏
- ✅ 文件名过长时需要截断显示,鼠标悬停显示完整名称
- ✅ 用户在没有选中任何文件时发起对话,对话正常进行(无文件上下文)
- ✅ 对话已属于某项目时,添加到另一项目应提示"移动"而非"添加"
- ✅ 项目下没有对话时,展开项目应显示空状态提示
- ✅ 用户创建或重命名项目时使用已存在的名称,应显示错误提示
- ✅ 项目已达到50个文件上限时,上传新文件应显示错误提示

---

## UI Prototypes *(界面原型)*

### 整体布局架构

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              Header                                       │
├────────────────┬─────────────────────────────────────────────────────────┤
│                │                                                          │
│   Sidebar      │                    Main Content                          │
│   (240px)      │                                                          │
│                │  ┌────────────────┬─────────────────────────────────┐   │
│ ┌────────────┐ │  │                │                                 │   │
│ │ Projects   │ │  │  Sources       │        Chat Panel               │   │
│ │ ├─ Proj A  │ │  │  Panel         │                                 │   │
│ │ │  └─ Chat1│ │  │  (280px)       │                                 │   │
│ │ │  └─ Chat2│ │  │                │                                 │   │
│ │ ├─ Proj B  │ │  │  [可收起]       │                                 │   │
│ │            │ │  │                │                                 │   │
│ ├────────────┤ │  └────────────────┴─────────────────────────────────┘   │
│ │ History    │ │                                                          │
│ │ ├─ Chat 3  │ │                                                          │
│ │ ├─ Chat 4  │ │                                                          │
│ └────────────┘ │                                                          │
│                │                                                          │
└────────────────┴─────────────────────────────────────────────────────────┘
```

### 1. 左侧导航栏 (Sidebar)

```
┌────────────────────────┐
│ SunnyAgent             │  <- Logo
├────────────────────────┤
│ ╔════════════════════╗ │
│ ║  + New Project     ║ │  <- 新建项目按钮
│ ╚════════════════════╝ │
├────────────────────────┤
│                        │
│ ▼ Projects        (3)  │  <- 可折叠分组,显示项目数
│   ├─ 📁 项目 Alpha     │  <- 项目图标 + 名称
│   │   ├─ 💬 会话 1     │  <- 展开后显示关联对话
│   │   └─ 💬 会话 2     │
│   ├─ 📁 项目 Beta  ●   │  <- 当前选中项目
│   └─ 📁 项目 Gamma     │
│                        │
├────────────────────────┤
│ ▼ History         (12) │  <- 历史对话分组
│   ├─ 💬 未关联会话 1   │
│   ├─ 💬 未关联会话 2   │
│   └─ ...               │
│                        │
└────────────────────────┘
```

**交互说明**:
- 点击 "+ New Project" 弹出新建项目模态框
- 项目名称支持内联编辑 (双击或右键菜单)
- 右键菜单: 新建对话、重命名、删除
- 对话右键菜单: 重命名、从项目移除、删除

### 2. 新建项目模态框

```
┌─────────────────────────────────────┐
│           Create Project        [X] │
├─────────────────────────────────────┤
│                                     │
│  Project Name                       │
│  ┌─────────────────────────────┐    │
│  │ My New Project              │    │
│  └─────────────────────────────┘    │
│                                     │
│  ┌─────────┐  ┌─────────────────┐   │
│  │ Cancel  │  │     Create      │   │
│  └─────────┘  └─────────────────┘   │
│                                     │
└─────────────────────────────────────┘
```

### 3. 项目首页 (Project Home)

当选中项目但没有活跃对话时显示:

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│                         📁 项目 Alpha                               │
│                    Created: 2026-02-17                              │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Ask anything about this project...                          │   │
│  │                                              [➤ Send]       │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
├────────────────────┬────────────────────┬───────────────────────────┤
│                    │                    │                           │
│  📄 Files (5)      │  🎯 Skills (3)     │  💬 Conversations (2)     │
│  ────────────────  │  ────────────────  │  ─────────────────────    │
│  • report.pdf      │  • Data Analysis   │  • 关于报告的讨论          │
│  • data.csv        │  • Code Review     │  • 数据分析会话            │
│  • notes.md        │  • Research        │                           │
│  + 2 more          │                    │                           │
│                    │                    │                           │
└────────────────────┴────────────────────┴───────────────────────────┘
```

### 4. 项目工作区 (Project Workspace) - 双栏布局

```
┌────────────────────────────────────────────────────────────────────────┐
│                         Project: 项目 Alpha                        [⋮] │
├──────────────────────┬─────────────────────────────────────────────────┤
│                      │                                                 │
│  Sources        [◀]  │                   Chat Area                     │
│  ──────────────────  │                                                 │
│                      │  ┌─────────────────────────────────────────┐    │
│  ☑ Select all (5)    │  │ 🤖 AI: How can I help with your project? │    │
│                      │  └─────────────────────────────────────────┘    │
│  ┌────────────────┐  │                                                 │
│  │ ☑ 📄 report.pdf│  │  ┌─────────────────────────────────────────┐    │
│  │    1.2 MB      │  │  │ 👤 User: 分析这份报告的主要内容          │    │
│  ├────────────────┤  │  └─────────────────────────────────────────┘    │
│  │ ☑ 📊 data.csv  │  │                                                 │
│  │    456 KB      │  │  ┌─────────────────────────────────────────┐    │
│  ├────────────────┤  │  │ 🤖 AI: 根据您选择的文件,报告主要包含... │    │
│  │ ☐ 📝 notes.md  │  │  └─────────────────────────────────────────┘    │
│  │    12 KB       │  │                                                 │
│  ├────────────────┤  │                                                 │
│  │ ☐ 📜 code.py   │  │                                                 │
│  │    8 KB        │  │                                                 │
│  └────────────────┘  │  ┌─────────────────────────────────────────┐    │
│                      │  │ [2 sources selected]                    │    │
│  ─────────────────── │  │ Ask a question...                       │    │
│  + Add sources       │  │                                    [➤]  │    │
│                      │  └─────────────────────────────────────────┘    │
│                      │                                                 │
└──────────────────────┴─────────────────────────────────────────────────┘
```

**Sources Panel 交互**:
- `[◀]` 按钮: 收起面板,面板收起后显示为窄条可展开
- `Select all`: 全选/取消全选文件
- 文件项: 复选框 + 文件图标 + 名称 + 大小
- 文件悬停: 显示完整文件名 tooltip
- 文件右键: 重命名、删除、下载
- `+ Add sources`: 打开文件选择器上传文件

**Chat Panel 说明**:
- `[2 sources selected]` 显示当前选中的文件数量
- 发送消息时自动携带选中的文件作为上下文

### 5. Sources 面板收起状态

```
┌────┬────────────────────────────────────────────────────────────────────┐
│    │                                                                    │
│ [▶]│                        Chat Area (Full Width)                      │
│    │                                                                    │
│ 📄 │  ┌────────────────────────────────────────────────────────────┐   │
│ 5  │  │                     对话内容区域                           │   │
│    │  │                     (扩展占满宽度)                         │   │
│    │  └────────────────────────────────────────────────────────────┘   │
│    │                                                                    │
└────┴────────────────────────────────────────────────────────────────────┘
```

### 6. 文件上传进度

```
┌────────────────────┐
│  Sources      [◀]  │
│  ──────────────────│
│                    │
│  Uploading...      │
│  ┌────────────────┐│
│  │ 📄 large.pdf   ││
│  │ ████████░░ 80% ││  <- 上传进度条
│  ├────────────────┤│
│  │ 📄 small.txt   ││
│  │ ✓ Completed    ││  <- 上传完成
│  └────────────────┘│
│                    │
└────────────────────┘
```

### 7. 对话右键菜单

```
项目中的对话:
┌────────────────────┐
│ 💬 会话名称        │
└────────────────────┘
        │
        ▼
┌────────────────────┐
│ ✏️  Rename         │
│ ➖ Remove from     │
│    project         │
│ ─────────────────  │
│ 🗑️  Delete         │
└────────────────────┘

History 中的对话:
┌────────────────────┐
│ 💬 会话名称        │
└────────────────────┘
        │
        ▼
┌────────────────────┐
│ ✏️  Rename         │
│ ➕ Add to project  │ → ┌────────────────┐
│ ─────────────────  │   │ 📁 项目 Alpha  │
│ 🗑️  Delete         │   │ 📁 项目 Beta   │
└────────────────────┘   │ 📁 项目 Gamma  │
                         └────────────────┘
```

### 8. 空状态界面

**无项目时**:
```
┌─────────────────────────────────────┐
│                                     │
│            📁                       │
│                                     │
│     No projects yet                 │
│                                     │
│   Create your first project to      │
│   organize your work                │
│                                     │
│   ┌─────────────────────────────┐   │
│   │     + Create Project        │   │
│   └─────────────────────────────┘   │
│                                     │
└─────────────────────────────────────┘
```

**项目无文件时**:
```
┌────────────────────┐
│  Sources      [◀]  │
│  ──────────────────│
│                    │
│       📄          │
│                    │
│   No files yet     │
│                    │
│   ┌──────────────┐ │
│   │ + Add sources│ │
│   └──────────────┘ │
│                    │
└────────────────────┘
```

### 9. 响应式设计

**侧边栏折叠时**:
```
┌────┬───────────────────────────────────────────────────────────────────┐
│    │                                                                   │
│ ☰  │                        Main Content                               │
│    │                                                                   │
│ 📁 │   (点击 📁 图标弹出项目列表 Popover)                               │
│    │                                                                   │
│ 📜 │   ┌────────────────────┐                                          │
│    │   │ Projects           │                                          │
│ ⚙️ │   │ ├─ 项目 Alpha      │                                          │
│    │   │ ├─ 项目 Beta   ●   │                                          │
│    │   │ └─ 项目 Gamma      │                                          │
│    │   └────────────────────┘                                          │
│    │                                                                   │
└────┴───────────────────────────────────────────────────────────────────┘
```

---

## Requirements *(mandatory)*

### Functional Requirements

- ✅ **FR-001**: System MUST allow authenticated users to create new projects with a unique name (no duplicate names per user)
- ✅ **FR-002**: System MUST allow users to edit project names they own
- ✅ **FR-003**: System MUST allow users to delete projects they own, with confirmation dialog
- ✅ **FR-004**: System MUST display user's projects in the left navigation alongside History
- ✅ **FR-005**: System MUST provide a project workspace with two-column layout (Sources + Chat)
- ✅ **FR-006**: System MUST allow users to upload files to their projects via "+ Add sources" button
- ✅ **FR-006a**: System MUST store project files permanently (not in temp directory)
- ✅ **FR-006b**: System MUST organize files by user_id/project_id/filename directory structure
- ✅ **FR-006c**: System MUST support document and code file types: PDF, DOCX, TXT, MD, CSV, JSON, and common code files (py, js, ts, java, go, etc.)
- ✅ **FR-006d**: System MUST limit each project to a maximum of 50 files
- ✅ **FR-007**: System MUST display uploaded files in a scrollable Sources list with checkboxes
- ✅ **FR-008**: System MUST allow users to multi-select files as conversation context
- ✅ **FR-009**: System MUST allow users to collapse/expand the Sources panel
- ✅ **FR-010**: System MUST reuse existing chat implementation for the Chat panel
- ✅ **FR-011**: System MUST pass selected files as context when user sends a message
- ✅ **FR-012**: System MUST enforce user ownership - users can only access their own projects
- ✅ **FR-013**: System MUST cascade delete project files when a project is deleted
- ✅ **FR-014**: System MUST persist project data in the database
- ✅ **FR-015**: System MUST display project's conversations as expandable tree nodes in navigation
- ✅ **FR-016**: System MUST allow users to remove conversations from projects via right-click menu
- ✅ **FR-017**: System MUST allow users to add conversations to projects from History list
- ✅ **FR-018**: System MUST unlink (not delete) conversations when a project is deleted

### Key Entities

- **Project**: 项目基本信息,包含 id, name, user_id, created_at, updated_at, is_deleted
- **ProjectFile**: 项目关联文件,包含 id, project_id, file_id, storage_path, original_name, content_type, size_bytes, created_at
  - storage_path 格式: `{base_dir}/{user_id}/{project_id}/{filename}`
- **Conversation**: 现有对话表,添加了 project_id 外键字段以支持项目关联

---

## Technical Implementation *(实际实现)*

### API Endpoints

#### Projects

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/projects` | GET | User | 列出用户所有项目 (按更新时间倒序) |
| `/api/projects` | POST | User | 创建新项目 (201) |
| `/api/projects/{id}` | GET | User | 获取项目详情 (含文件数、对话数) |
| `/api/projects/{id}` | PATCH | User | 更新项目名称 |
| `/api/projects/{id}` | DELETE | User | 软删除项目 (204, 清理物理文件) |

#### Project Files

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/projects/{id}/files` | GET | User | 列出项目所有文件 |
| `/api/projects/{id}/files` | POST | User | 上传文件 (multipart/form-data) |
| `/api/projects/{id}/files/{fid}` | PATCH | User | 重命名文件 |
| `/api/projects/{id}/files/{fid}` | DELETE | User | 删除文件 (204) |
| `/api/projects/{id}/files/{fid}/download` | GET | User | 下载文件 |

#### Project Conversations

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/projects/{id}/conversations` | GET | User | 列出项目所有对话 |
| `/api/conversations/{id}/project` | POST | User | 将对话添加到项目 |
| `/api/conversations/{id}/project` | DELETE | User | 从项目移除对话 |

### Database Schema

```sql
-- projects 表
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    is_deleted BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_projects_user ON projects(user_id);
CREATE INDEX idx_projects_updated ON projects(updated_at DESC);
CREATE UNIQUE INDEX uq_projects_user_name ON projects(user_id, name) WHERE is_deleted = FALSE;

-- project_files 表
CREATE TABLE project_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_id VARCHAR(36) NOT NULL,
    storage_path VARCHAR(512) NOT NULL,
    original_name VARCHAR(255) NOT NULL,
    content_type VARCHAR(100),
    size_bytes BIGINT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_project_files_project ON project_files(project_id);
CREATE INDEX idx_project_files_file_id ON project_files(file_id);
CREATE UNIQUE INDEX uq_project_files_project_name ON project_files(project_id, original_name);

-- conversations 表扩展
ALTER TABLE conversations ADD COLUMN project_id UUID REFERENCES projects(id) ON DELETE SET NULL;
CREATE INDEX idx_conversations_project ON conversations(project_id);
```

### Configuration

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `PROJECT_FILES_DIR` | `/tmp/sunnyagent_project_files` | 项目文件存储根目录 |
| Max file size | 10 MB | 单个文件最大上传大小 |
| Max files per project | 50 | 每个项目最大文件数 |

### Supported File Types

**文档类型**: `.pdf`, `.docx`, `.txt`, `.md`, `.csv`, `.json`

**代码类型**: `.py`, `.js`, `.ts`, `.tsx`, `.jsx`, `.java`, `.go`, `.c`, `.cpp`, `.h`, `.hpp`, `.rs`, `.rb`, `.php`, `.swift`, `.kt`

### Frontend Components

| 组件 | 文件路径 | 功能 |
|------|----------|------|
| `ProjectList` | `components/Projects/ProjectList.tsx` | 项目列表 + 树形结构 |
| `ProjectItem` | `components/Projects/ProjectItem.tsx` | 单个项目项 + 对话子项 |
| `ProjectHome` | `components/Projects/ProjectHome.tsx` | 项目首页 |
| `ProjectWorkspace` | `components/Projects/ProjectWorkspace.tsx` | 双栏工作区 |
| `SourcesPanel` | `components/Projects/SourcesPanel.tsx` | 文件源管理面板 |
| `NewProjectModal` | `components/Projects/NewProjectModal.tsx` | 新建项目模态框 |
| `ProjectSelectMenu` | `components/Projects/ProjectSelectMenu.tsx` | 项目选择下拉菜单 |
| `ProjectPopover` | `components/Projects/ProjectPopover.tsx` | 折叠侧边栏项目列表 |

### Frontend State Management

| Hook | 文件路径 | 功能 |
|------|----------|------|
| `useProjects` | `hooks/useProjects.ts` | 项目/文件/对话状态管理 |

**状态持久化**:
- 选中的项目 ID: `localStorage.selectedProjectId`
- Sources 面板展开状态: `localStorage.sourcesPanelExpanded`

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- ✅ **SC-001**: Users can create a new project in under 10 seconds
- ✅ **SC-002**: Users can navigate between projects and history with a single click
- ✅ **SC-003**: Project list displays within 500ms of page load
- ✅ **SC-004**: File upload completes within 30 seconds for files up to 10MB
- ✅ **SC-005**: 100% of project operations respect user ownership (no unauthorized access)
- ✅ **SC-006**: Project deletion with confirmation prevents accidental data loss
- ✅ **SC-007**: Users can manage at least 50 projects without UI performance degradation
- ✅ **SC-008**: Sources panel collapse/expand animation completes within 300ms

## Assumptions

- ✅ 现有的文件上传系统 (`/api/files/upload`) 可以复用,但需要扩展支持永久存储
- ✅ 现有的用户认证系统提供 user_id
- ✅ 左侧导航组件支持添加新的导航项
- ✅ 现有的 Chat 组件可以接收文件上下文参数
- ✅ 文件存储基础目录通过环境变量配置 (如 `PROJECT_FILES_DIR`)
