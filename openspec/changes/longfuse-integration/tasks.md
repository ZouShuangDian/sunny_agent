# Tasks: Langfuse 可观测性集成

**Input**: Design documents from `openspec/changes/longfuse-integration/`
**Prerequisites**: impl-plan.md, spec.md, data-model.md, api-spec.md, research.md, quickstart.md
**Constitution**: TDD is NON-NEGOTIABLE — test tasks included for every user story

**Repositories**:
- **后端**: `sunny_agent` (当前仓库) — Phase 1–9, T001–T066
- **前端**: `sunny-agent-web` (`/Users/yanwen/Documents/github/sunny-agent-web`) — Phase 10, T067–T080

**Organization**: Tasks grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US7)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project dependencies, test framework, and shared tooling

- [ ] T001 Add `langfuse` and `cryptography` dependencies via `poetry add langfuse cryptography` in pyproject.toml
- [ ] T002 Add `pytest`, `pytest-asyncio`, `pytest-cov`, `httpx` (test client) dev dependencies via `poetry add --group dev pytest pytest-asyncio pytest-cov httpx` in pyproject.toml
- [ ] T003 [P] Create pytest configuration in pyproject.toml (`[tool.pytest.ini_options]` with asyncio_mode="auto", testpaths=["tests"])
- [ ] T004 [P] Create test fixtures base file with async DB session and test client in tests/conftest.py
- [ ] T005 [P] Create Langfuse Docker Compose configuration with ClickHouse, Redis, MinIO, PostgreSQL, Langfuse services and `LANGFUSE_INIT_*` env vars in infra/langfuse-compose.yml

**Checkpoint**: Dependencies installed, test infrastructure ready, Langfuse compose file exists

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that ALL user stories depend on — config, DB model, encryption, config loader

**CRITICAL**: No user story work can begin until this phase is complete

### Tests for Foundational Phase

- [ ] T006 [P] Write unit tests for Fernet encrypt/decrypt/generate_key functions in tests/unit/test_crypto.py
- [ ] T007 [P] Write unit tests for Langfuse settings fields (LANGFUSE_ENABLED, LANGFUSE_HOST, etc.) presence and defaults in tests/unit/test_config_langfuse.py
- [ ] T008 [P] Write unit tests for LangfuseConfig model CRUD and singleton constraint (id=1) in tests/unit/test_langfuse_config_model.py
- [ ] T009 Write integration tests for config loader logic (DB priority over .env, first-boot seed, ENCRYPTION_KEY auto-gen) in tests/integration/test_langfuse_config_loader.py

### Implementation for Foundational Phase

- [ ] T010 [P] Add LANGFUSE_*, ENCRYPTION_KEY, LANGFUSE_PII_PATTERNS settings fields to app/config.py
- [ ] T011 [P] Create Fernet encryption utility module (encrypt_secret, decrypt_secret, generate_encryption_key) in app/utils/crypto.py
- [ ] T012 Create LangfuseConfig SQLAlchemy model with singleton constraint per data-model.md in app/db/models/langfuse_config.py
- [ ] T013 Register LangfuseConfig import in app/db/models/__init__.py
- [ ] T014 Generate Alembic migration for langfuse_config table via `alembic revision --autogenerate -m "add langfuse_config table"`
- [ ] T015 Implement config loader service (DB-first, .env fallback, auto-generate ENCRYPTION_KEY, encrypt secret_key before DB write) in app/services/langfuse_config_loader.py
- [ ] T016 Wire config loader into application lifespan startup in app/main.py
- [ ] T017 Run all foundational tests — verify T006–T009 pass (Green)

**Checkpoint**: Config model, encryption, config loader all working. `alembic upgrade head` creates table. Tests pass.

---

## Phase 2.5: Technical Spikes (Blocking Phase 3–4)

**Purpose**: Validate 3 key technical assumptions before proceeding. Output: spike verification report.

- [ ] T018 Spike 1 — Verify LiteLLM Langfuse Callback with `acompletion(stream=True)` + async generator: configure `litellm.success_callback=["langfuse"]`, call `acompletion`, check Langfuse for complete Generation record in scripts/spikes/spike_litellm_callback.py
- [ ] T019 Spike 2 — Verify `LANGFUSE_INIT_*` env var idempotency: first start creates org/project/key, restart with volume preserves state, restart without volume recreates in scripts/spikes/spike_langfuse_init.py
- [ ] T020 Spike 3 — Verify Langfuse v3 `/api/public/metrics/daily` and `/api/public/metrics/usage` endpoint availability; if unavailable, confirm `/api/public/traces` returns token/cost fields for fallback aggregation in scripts/spikes/spike_langfuse_api.py
- [ ] T021 Write spike verification report summarizing results and any Phase 3+ plan adjustments in openspec/changes/longfuse-integration/spike-report.md

**Checkpoint**: All 3 spikes verified. Implementation approach for Phases 3–4 confirmed.

---

## Phase 3: US6 — 改造 Observability 支持 Langfuse Trace (Priority: P1) MVP

**Goal**: Agent 执行自动产生 Trace，Span 正确嵌套，携带 user_id 和 session_id

**Independent Test**: 发送一条 chat 消息，在 Langfuse 界面可看到完整 Trace（chat_request → react_loop → think/act → tool_calls），包含 user_id 和 session_id

### Tests for US6

- [ ] T022 [P] [US6] Write unit tests for Langfuse client singleton (get_langfuse returns None when disabled, returns Langfuse when enabled, shutdown flushes) in tests/unit/test_langfuse_client.py
- [ ] T023 [P] [US6] Write unit tests for PII scrubber (phone, ID card, email, credential patterns replaced; custom patterns via config) in tests/unit/test_pii_filter.py
- [ ] T024 [P] [US6] Write unit tests for langfuse_trace_var ContextVar propagation (set in chat handler, retrievable in downstream) in tests/unit/test_langfuse_context.py
- [ ] T025 [US6] Write integration test verifying chat request creates Trace with correct user_id, session_id, and nested spans in tests/integration/test_trace_creation.py

### Implementation for US6

- [ ] T026 [P] [US6] Create Langfuse client singleton module (get_langfuse, shutdown_langfuse) in app/observability/langfuse_client.py
- [ ] T027 [P] [US6] Create PII filter module with builtin patterns + LANGFUSE_PII_PATTERNS support in app/observability/pii_filter.py
- [ ] T028 [US6] Add `langfuse_trace_var` ContextVar to app/observability/context.py
- [ ] T029 [US6] Configure LiteLLM Langfuse callback (success_callback + failure_callback) in app/main.py lifespan startup; add shutdown_langfuse to lifespan shutdown
- [ ] T030 [US6] Create top-level Trace in chat request handler with user_id and session_id, set langfuse_trace_var, in app/api/chat.py
- [ ] T031 [US6] Add `react_loop` Span to ReAct engine run() method, reading trace from langfuse_trace_var in app/execution/l3/react_engine.py
- [ ] T032 [US6] Add `think` Span to thinker think_stream() method in app/execution/l3/thinker.py
- [ ] T033 [US6] Add `tool:{name}` Span per tool_call in actor act() method in app/execution/l3/actor.py
- [ ] T034 [US6] Add error recording to Trace/Span in exception handlers (trace.update with level="ERROR") in app/api/chat.py and app/execution/l3/react_engine.py
- [ ] T035 [US6] Register PII filter hook on Langfuse client initialization in app/observability/langfuse_client.py
- [ ] T036 [US6] Run all US6 tests — verify T022–T025 pass (Green)

**Checkpoint**: Chat messages produce complete Langfuse Traces with nested Spans. PII scrubbed. Graceful degradation when Langfuse unavailable.

---

## Phase 4: US4 + US4.1 — Langfuse 服务管理与项目自动初始化 (Priority: P1)

**Goal**: 管理员可启停内置 Langfuse 服务，配置外部服务，项目自动初始化

**Independent Test**: 管理员通过 API 启动内置服务 → 服务健康 → 停止服务；配置外部服务 URL → 验证连通性

### Tests for US4

- [ ] T037 [P] [US4] Write unit tests for LangfuseManager (start_builtin, stop_builtin, get_builtin_status mock subprocess calls) in tests/unit/test_langfuse_manager.py
- [ ] T038 [P] [US4] Write contract tests for 7 service management API endpoints (config CRUD, builtin start/stop/status, validate, initialize) in tests/contract/test_observability_config_api.py
- [ ] T039 [US4] Write integration test for builtin service lifecycle (start → health check → stop) in tests/integration/test_builtin_service.py

### Implementation for US4

- [ ] T040 [P] [US4] Create Langfuse service manager (start_builtin, stop_builtin, get_builtin_status via asyncio.create_subprocess_exec; validate_connection via httpx; update_config writes DB only) in app/services/langfuse_manager.py
- [ ] T041 [US4] Create Observability API router with 7 management endpoints per api-spec.md 1.3–1.9 (all admin-only) in app/api/observability.py
- [ ] T042 [US4] Register observability router in app/main.py with prefix `/api/v1/observability` and tags=["observability"]
- [ ] T043 [US4] Run all US4 tests — verify T037–T039 pass (Green)

**Checkpoint**: Admin can manage Langfuse services via API. Config persisted to DB. Non-admin gets 403.

---

## Phase 5: US5 + US2 — 可观测性 Tab 与监控指标 (Priority: P1)

**Goal**: 前端可获取 Langfuse 状态、Token 用量汇总/趋势/用户分布；管理员可跳转控制台

**Independent Test**: 调用 usage/summary API 返回正确的 token 统计；调用 console-url API 管理员获取跳转链接，普通用户 403

### Tests for US5

- [ ] T044 [P] [US5] Write unit tests for ObservabilityService (get_status, get_console_url, get_usage_summary, get_usage_daily, get_usage_by_user, refresh_usage) with mocked Langfuse API responses in tests/unit/test_observability_service.py
- [ ] T045 [P] [US5] Write contract tests for 6 observability API endpoints (status, console-url, usage/summary, usage/daily, usage/by-user, usage/refresh) per api-spec.md 1.1–1.2, 1.10–1.13 in tests/contract/test_observability_usage_api.py
- [ ] T046 [US5] Write integration test verifying Redis caching of usage data (first call hits Langfuse, second call within TTL reads cache) in tests/integration/test_usage_cache.py

### Implementation for US5

- [ ] T047 [US5] Create ObservabilityService with Langfuse API calls via httpx.AsyncClient + Redis caching (5min TTL per data-model.md key patterns) in app/services/observability.py
- [ ] T048 [US5] Add 6 usage/status endpoints to observability router (status: any auth user; console-url: admin only per FR-035; usage summary/daily: permission-controlled; by-user: admin only; refresh: admin only) in app/api/observability.py
- [ ] T049 [US5] Add Redis cache key patterns for Langfuse health, usage by date/user, and usage summary in app/cache/redis_client.py
- [ ] T050 [US5] Run all US5 tests — verify T044–T046 pass (Green)

**Checkpoint**: Frontend can display Langfuse status, usage stats, and trends. Admin console jump works. Redis caching reduces Langfuse API load.

---

## Phase 6: US1 — 查看 Agent 执行链路追踪 (Priority: P1)

**Goal**: 运维人员可在 Langfuse 界面查看完整的 Agent 执行链路

**Independent Test**: 发起对话 → Langfuse Traces 列表可见该 Trace → 点击进入看到 chat_request → react_loop → think → act → tool_call 层级

> Note: US1 的核心技术工作已在 US6 (Phase 3) 完成。本 Phase 专注端到端验证和文档。

### Tests for US1

- [ ] T051 [US1] Write end-to-end test: send chat via API → query Langfuse traces API → assert trace exists with correct user_id, session_id, nested spans, and model/token/cost fields in tests/e2e/test_trace_e2e.py

### Implementation for US1

- [ ] T052 [US1] Verify Langfuse dashboard displays Trace list filtered by user_id and session_id (manual verification, document results)
- [ ] T053 [US1] Run US1 e2e test — verify T051 passes (Green)

**Checkpoint**: Complete Agent execution traces visible in Langfuse with correct hierarchy and metadata.

---

## Phase 7: US7 — Trace 数据导出 (Priority: P2)

**Goal**: 管理员和用户可导出 Trace 数据为 JSON/CSV

**Independent Test**: 调用 export API with format=json → 下载有效 JSON 文件；format=csv → 下载有效 CSV 文件

### Tests for US7

- [ ] T054 [P] [US7] Write unit tests for export_traces logic (JSON format output, CSV format output, 10000 limit enforcement, user permission filtering) in tests/unit/test_trace_export.py
- [ ] T055 [US7] Write contract test for `GET /api/v1/observability/traces/export` endpoint (json/csv format, auth, admin vs user scope) in tests/contract/test_trace_export_api.py

### Implementation for US7

- [ ] T056 [US7] Add export_traces method to ObservabilityService (paginated Langfuse API fetch, JSON/CSV formatting, 10000 cap) in app/services/observability.py
- [ ] T057 [US7] Add `GET /traces/export` endpoint with StreamingResponse and Content-Disposition header to observability router in app/api/observability.py
- [ ] T058 [US7] Run all US7 tests — verify T054–T055 pass (Green)

**Checkpoint**: Trace data exportable as JSON and CSV. Admin exports all users. Regular user exports own data only.

---

## Phase 8: US3 — 管理测试数据集并评估 Agent (Priority: P2)

**Goal**: 开发人员可通过 Langfuse 管理测试数据集并运行 Experiment 评估

**Independent Test**: 运行评估脚本 → Langfuse 中出现 Experiment 结果和评分

> Note: Dataset 管理使用 Langfuse 原生 UI/SDK，SunnyAgent 无需实现 API。本 Phase 仅提供评估脚本模板。

### Tests for US3

- [ ] T059 [US3] Write smoke test verifying eval script can import required modules and construct Langfuse client in tests/unit/test_eval_script.py

### Implementation for US3

- [ ] T060 [US3] Create evaluation script template (Dataset read → call /api/chat → LLM-as-a-Judge scoring → Experiment record) in scripts/langfuse_eval.py
- [ ] T061 [US3] Run US3 test — verify T059 passes (Green)

**Checkpoint**: Evaluation script runnable. Langfuse shows Experiment results.

---

## Phase 9: US5-FE — 前端可观测性 Tab (Priority: P1, sunny-agent-web 仓库)

**Goal**: 在管理面板中实现可观测性页面，展示 Langfuse 状态、服务管理、Token 用量统计和数据导出

**Repository**: `sunny-agent-web` (`/Users/yanwen/Documents/github/sunny-agent-web`)

**Independent Test**: 打开管理面板 → 可观测性 Tab 可见 → Langfuse 状态正确显示 → 用量统计图表加载 → 导出下载成功

**Depends on**: 后端 Phase 4 (US4) + Phase 5 (US5) API 就绪

### Tests for US5-FE

- [ ] T067 [P] [US5] Write Vitest unit tests for observability API module (mock axios, verify request URLs, params, auth headers) in sunny-agent-web/src/api/observability/__tests__/index.test.ts
- [ ] T068 [P] [US5] Write Vitest component tests for usage-stats.vue (date picker interaction, summary card rendering, permission-based visibility) in sunny-agent-web/src/components/admin-manage/observability/__tests__/usage-stats.test.ts

### Implementation for US5-FE

- [ ] T069 [P] [US5] Create observability API TypeScript types (LangfuseStatus, UsageSummary, DailyUsage, UserUsage, TraceExportItem, LangfuseConfig) in sunny-agent-web/src/api/observability/types.ts
- [ ] T070 [P] [US5] Create observability API module with all 14 endpoint calls (getStatus, getConsoleUrl, getConfig, updateConfig, validateConnection, initializeProject, startBuiltinService, stopBuiltinService, getBuiltinStatus, getUsageSummary, getUsageDaily, getUsageByUser, refreshUsage, exportTraces) in sunny-agent-web/src/api/observability/index.ts
- [ ] T071 [US5] Add OBSERVABILITY tab to admin sidebar navigation (icon: Activity from lucide-vue-next, admin-only visibility) in sunny-agent-web/src/components/admin-manage/admin-sidebar/index.vue
- [ ] T072 [US5] Route OBSERVABILITY tab to observability component in sunny-agent-web/src/components/admin-manage/index.vue
- [ ] T073 [US5] Create observability page main layout with 3 sections (status card, service management, usage stats) in sunny-agent-web/src/components/admin-manage/observability/index.vue
- [ ] T074 [US5] Implement Langfuse status card component (health indicator dot, version display, "打开控制台" button for admin calling getConsoleUrl and window.open) in sunny-agent-web/src/components/admin-manage/observability/langfuse-status.vue
- [ ] T075 [US5] Implement service management component (service mode selector builtin/external, start/stop buttons for builtin, URL input + validate button for external, API key config form, initialize button) in sunny-agent-web/src/components/admin-manage/observability/service-manage.vue
- [ ] T076 [US5] Implement usage statistics component (el-date-picker daterange default today, summary cards for totalCalls/totalTokens/inputTokens/outputTokens/estimatedCost, daily trend bar chart, admin user filter dropdown + user distribution el-table, refresh button, export button with format selector triggering file download) in sunny-agent-web/src/components/admin-manage/observability/usage-stats.vue
- [ ] T077 [US5] Run all US5-FE tests — verify T067–T068 pass (Green)

**Checkpoint**: Admin panel has working Observability tab. Status, service management, usage stats, and export all functional.

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories across both repos

### 后端 (sunny_agent)

- [ ] T078 [P] Run `ruff check` and `ruff format --check` on all new files, fix any violations
- [ ] T079 [P] Run full test suite `pytest tests/ --cov=app --cov-report=term-missing` — verify 80%+ coverage on new code
- [ ] T080 Validate quickstart.md end-to-end: follow all 5 steps on clean environment, verify Trace appears in Langfuse
- [ ] T081 [P] Review all new files for hardcoded secrets, ensure ENCRYPTION_KEY and LANGFUSE_SECRET_KEY are only read from env/DB
- [ ] T082 Verify graceful degradation: stop Langfuse → send chat → confirm Agent responds normally and no crash

### 前端 (sunny-agent-web)

- [ ] T083 [P] Run `npm run lint` on all new files in sunny-agent-web, fix any violations
- [ ] T084 E2E smoke test: start backend + frontend dev servers → login as admin → open Observability tab → verify status loads → select date range → verify usage data → click export → verify file downloads

---

## Dependencies & Execution Order

### Phase Dependencies

```
Phase 1 (Setup) — no dependencies                          [后端]
    │
    ▼
Phase 2 (Foundational: config, model, encryption) — BLOCKS ALL [后端]
    │
    ▼
Phase 2.5 (Spikes: LiteLLM, INIT_*, metrics API)           [后端]
    │
    ▼
Phase 3 (US6: Trace integration + PII) ─────────────┐      [后端]
    │                                                 │
    ▼                                                 ▼
Phase 4 (US4: Service mgmt)              Phase 5 (US5: Usage API)  [后端]
    │                                                 │
    ▼                                                 │
Phase 6 (US1: E2E trace)                              │     [后端]
    │                                                 │
    ▼                                                 ▼
Phase 7 (US7: Trace export) ◀────────────────────────┘     [后端]
    │
    ▼
Phase 8 (US3: Eval scripts) — can start after Phase 2.5    [后端]

Phase 9 (US5-FE: 前端可观测性 Tab)                          [前端]
    ▲ depends on Phase 4 + Phase 5 backend API ready
    │ can run in parallel with Phase 6/7/8

Phase 10 (Polish) — after all phases                        [两端]
```

### User Story Dependencies

- **US6 (Phase 3)**: Can start after Phase 2.5 — foundation for all other stories [后端]
- **US4 (Phase 4)**: Can start after Phase 3 — parallelizable with US5 [后端]
- **US5 backend (Phase 5)**: Can start after Phase 3 — parallelizable with US4 [后端]
- **US5 frontend (Phase 9)**: Can start after Phase 4 + 5 — parallelizable with Phase 6/7/8 [前端]
- **US1 (Phase 6)**: Depends on US6 (Phase 3) — E2E validation [后端]
- **US2 (embedded)**: No code needed — uses Langfuse built-in dashboard
- **US7 (Phase 7)**: Depends on US5 backend (Phase 5) [后端]
- **US3 (Phase 8)**: Can start after Phase 2.5 — independent script [后端]

### Within Each User Story

- Tests MUST be written and FAIL before implementation (Constitution Principle I)
- Models before services
- Services before endpoints
- Core implementation before integration
- Story verification test at end of each phase

### Parallel Opportunities

**后端内部并行:**
- T003 + T004 + T005 (Phase 1 setup files)
- T006 + T007 + T008 (Phase 2 unit tests)
- T010 + T011 (Phase 2 config + crypto)
- T022 + T023 + T024 (US6 unit tests)
- T026 + T027 (US6 langfuse client + pii filter)
- T037 + T038 (US4 unit + contract tests)
- T044 + T045 (US5 unit + contract tests)
- Phase 4 (US4) || Phase 5 (US5) — entire phases parallelizable
- T054 + T055 (US7 unit + contract tests)

**前端内部并行:**
- T067 + T068 (US5-FE tests)
- T069 + T070 (API types + module)

**跨仓库并行:**
- Phase 9 (前端) || Phase 6 + 7 + 8 (后端) — 后端 API 就绪后前后端可完全并行

---

## Parallel Example: 后端 + 前端并行

```bash
# After Phase 4 + 5 (backend API ready), launch frontend and remaining backend in parallel:

# 后端 Developer: Phase 6 → Phase 7 → Phase 8
Task: T051 "E2E trace test in tests/e2e/test_trace_e2e.py"
Task: T054 "Export unit tests in tests/unit/test_trace_export.py"
Task: T056 "Export service in app/services/observability.py"
Task: T060 "Eval script in scripts/langfuse_eval.py"

# 前端 Developer: Phase 9 (all frontend work)
Task: T069 "API types in sunny-agent-web/src/api/observability/types.ts"
Task: T070 "API module in sunny-agent-web/src/api/observability/index.ts"
Task: T071 "Admin sidebar tab in sunny-agent-web/.../admin-sidebar/index.vue"
Task: T073 "Observability page in sunny-agent-web/.../observability/index.vue"
Task: T074 "Status card in sunny-agent-web/.../observability/langfuse-status.vue"
Task: T075 "Service management in sunny-agent-web/.../observability/service-manage.vue"
Task: T076 "Usage stats in sunny-agent-web/.../observability/usage-stats.vue"
```

---

## Implementation Strategy

### MVP First (US6 Only — Phase 1–3, 后端)

1. Complete Phase 1: Setup (dependencies + test framework)
2. Complete Phase 2: Foundational (config + model + encryption)
3. Complete Phase 2.5: Spikes (verify 3 key assumptions)
4. Complete Phase 3: US6 (Trace integration)
5. **STOP and VALIDATE**: Send chat → verify Trace in Langfuse
6. This alone delivers core observability value (no frontend needed)

### Incremental Delivery

1. Phase 1–2.5 → Foundation ready [后端]
2. + Phase 3 (US6) → Traces working → **MVP!** [后端]
3. + Phase 4 (US4) → Service management API [后端]
4. + Phase 5 (US5) → Usage statistics API (parallel with Phase 4) [后端]
5. + Phase 9 (US5-FE) → Frontend UI (can start now) [前端]
6. + Phase 6 (US1) → E2E verification [后端, parallel with Phase 9]
7. + Phase 7 (US7) → Export capability [后端, parallel with Phase 9]
8. + Phase 8 (US3) → Eval scripts [后端]
9. Phase 10 → Polish and coverage [两端]

### Parallel Team Strategy (Backend + Frontend)

With 1 backend + 1 frontend developer:

1. **Backend dev**: Phase 1 → 2 → 2.5 → 3 → 4 → 5 → 6 → 7 → 8
2. **Frontend dev**: waits until Phase 4+5 done → Phase 9 (all frontend)
3. Together: Phase 10 (polish)

With 2 backend + 1 frontend developer:

1. **Backend A**: Phase 1 → 2 → 2.5 → 3 → US4 (Phase 4) → US7 (Phase 7)
2. **Backend B**: (joins after Phase 2.5) → US5 (Phase 5) → US1 (Phase 6) → US3 (Phase 8)
3. **Frontend**: Phase 9 starts after Phase 4+5 → parallel with Phase 6/7/8

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- Constitution mandates TDD: every phase has tests written FIRST
- US2 (监控仪表盘) has no code tasks — uses Langfuse built-in dashboard
- Spike results (Phase 2.5) may adjust Phase 3+ implementation approach
- Error codes follow `http_status * 100` convention (40000, 40100, 40300, 50000)
- Runtime config changes write DB only, never .env (Architecture Decision #12)
- 后端任务 (T001–T082) 在 `sunny_agent` 仓库执行
- 前端任务 (T067–T077, T083–T084) 在 `sunny-agent-web` 仓库执行
- 前端使用 Vitest 测试框架（项目已配置），后端使用 pytest
