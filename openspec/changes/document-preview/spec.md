# Feature Specification: 对话文档预览

**Feature Directory**: `openspec/changes/document-preview`
**Created**: 2026-03-20
**Status**: Draft
**Input**: 对生成的 markdown/pdf/docx/excel 文件进行预览，在左侧滑动窗口中展示，右上方提供下载。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Markdown 文件预览 (Priority: P1)

用户在对话中让 Agent 生成了一份 Markdown 文档（如工作日报、技术方案）。生成完成后，用户希望直接在界面上预览文档内容，而不需要下载后用外部工具打开。

**Why this priority**: Markdown 是 Agent 最常生成的文档格式，也是渲染成本最低的格式，作为 MVP 能快速交付价值。

**Independent Test**: 用户发送"生成一份工作日报"，Agent 生成 .md 文件后，点击文件即可在左侧滑出面板中看到排版后的文档内容。

**Acceptance Scenarios**:

1. **Given** Agent 生成了一份 .md 文件并在聊天中展示文件卡片，**When** 用户点击文件卡片，**Then** 左侧滑出预览面板，渲染 Markdown 内容（含标题、列表、表格、代码块）
2. **Given** 预览面板已打开，**When** 用户点击右上角下载按钮，**Then** 弹出下拉菜单显示可下载的格式，用户选择后浏览器触发文件下载
3. **Given** 预览面板已打开，**When** 用户点击面板外的聊天区域或关闭按钮，**Then** 面板平滑收起

---

### User Story 2 - PDF 文件预览 (Priority: P1)

用户让 Agent 生成了一份 PDF 文档（如查询结果导出、报告）。用户希望直接在界面上翻阅 PDF 内容。

**Why this priority**: PDF 是正式文档的标准格式，与 Markdown 同为最高优先级。

**Independent Test**: Agent 生成 .pdf 文件后，用户点击文件卡片，左侧面板中嵌入 PDF 查看器，支持翻页和缩放。

**Acceptance Scenarios**:

1. **Given** Agent 生成了一份 .pdf 文件，**When** 用户点击文件卡片，**Then** 左侧滑出预览面板，嵌入 PDF 查看器展示文档内容
2. **Given** PDF 预览面板已打开，**When** 文档多于一页，**Then** 用户可以滚动翻页查看全部内容
3. **Given** PDF 预览面板已打开，**When** 用户点击右上角下载按钮，**Then** 可下载原始 PDF 文件

---

### User Story 3 - DOCX 文件预览 (Priority: P2)

用户让 Agent 生成了一份 Word 文档。用户希望在界面上预览文档内容，包括标题、正文、表格等排版元素。

**Why this priority**: DOCX 格式常用于正式文件输出，但渲染复杂度高于 Markdown 和 PDF，优先级次于前两者。

**Independent Test**: Agent 生成 .docx 文件后，用户点击文件卡片，左侧面板中展示文档内容（标题、正文、表格等基本排版）。

**Acceptance Scenarios**:

1. **Given** Agent 生成了一份 .docx 文件，**When** 用户点击文件卡片，**Then** 左侧滑出预览面板，展示文档内容（标题、段落、表格、列表）
2. **Given** DOCX 预览面板已打开，**When** 用户点击下载按钮，**Then** 可下载原始 .docx 文件

---

### User Story 4 - Excel 文件预览 (Priority: P2)

用户让 Agent 生成了一份 Excel 表格（如数据统计、导出报表）。用户希望在界面上预览表格数据。

**Why this priority**: Excel 是数据分析场景的重要输出格式，但渲染需要独立的表格组件，优先级与 DOCX 一致。

**Independent Test**: Agent 生成 .xlsx 文件后，用户点击文件卡片，左侧面板中展示表格数据，支持多 Sheet 切换。

**Acceptance Scenarios**:

1. **Given** Agent 生成了一份 .xlsx 文件，**When** 用户点击文件卡片，**Then** 左侧滑出预览面板，以表格形式展示数据
2. **Given** Excel 有多个 Sheet，**When** 预览面板打开，**Then** 顶部显示 Sheet 标签页，用户可切换查看不同 Sheet
3. **Given** Excel 预览面板已打开，**When** 用户点击下载按钮，**Then** 可下载原始 .xlsx 文件

---

### Edge Cases

- 文件内容为空时，预览面板应显示"文档内容为空"的友好提示
- 文件过大（超过 10MB）时，应提示用户下载查看而非在线预览
- 文件损坏或格式无法解析时，应显示错误提示并提供下载原文件的选项
- 预览面板打开时，用户继续与 Agent 对话，面板应保持打开不受影响
- 连续点击不同文件时，面板应切换为新文件的预览内容

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: 系统 MUST 支持预览 4 种文件格式：Markdown (.md)、PDF (.pdf)、Word (.docx)、Excel (.xlsx/.xls)
- **FR-002**: 用户点击聊天中的文件卡片时，系统 MUST 在界面左侧滑出预览面板
- **FR-003**: 预览面板 MUST 占据界面左侧约 60% 宽度，以滑动动画方式展开
- **FR-004**: 预览面板右上角 MUST 提供下载按钮，点击后弹出下拉菜单列出可下载的格式
- **FR-005**: 预览面板 MUST 提供关闭按钮，点击后面板平滑收起
- **FR-006**: Markdown 预览 MUST 渲染标题、列表、表格、代码块、链接等标准元素
- **FR-007**: PDF 预览 MUST 支持多页滚动查看
- **FR-008**: DOCX 预览 MUST 展示标题、段落、表格、列表等基本排版
- **FR-009**: Excel 预览 MUST 支持多 Sheet 切换查看
- **FR-010**: 预览面板顶部 MUST 显示文件名称和文件类型标识
- **FR-011**: 文件无法预览时，系统 MUST 显示错误提示并提供下载原文件的降级方案

### Key Entities

- **生成文件 (GeneratedFile)**: Agent 在对话中生成的文件，包含文件名、类型、大小、下载地址、预览内容
- **预览面板 (PreviewPanel)**: 左侧滑出的文档预览窗口，包含文件元信息、预览区域、下载操作区

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 用户点击文件卡片后，预览面板在 1 秒内完成展开并开始显示内容
- **SC-002**: Markdown 和 PDF 文件的预览准确率达到 95%（排版与原文基本一致）
- **SC-003**: 用户无需离开对话界面即可完成文件预览和下载操作
- **SC-004**: 所有 4 种文件格式均支持一键下载
- **SC-005**: 预览面板的打开/关闭动画流畅，无卡顿感

## Assumptions

- Agent 生成的文件已通过后端接口提供下载地址（`download_url`），前端已有 present-files 组件展示文件卡片
- 前端已有 `sy-drawer` 侧边栏组件可复用
- 前端已有 `stream-markdown` 组件用于 Markdown 渲染
- 文件预览为纯前端渲染，不需要后端额外的预览转换服务
- DOCX 和 Excel 的预览精度可以接受一定程度的排版损失（如复杂样式、图片位置偏移）
