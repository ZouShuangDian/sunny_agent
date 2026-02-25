---
name: github
description: GitHub 仓库、用户、趋势项目探索。适用于：搜索仓库、查看仓库详情、查询用户信息、发现 Trending 项目、按 Topic 浏览等 GitHub 相关任务。
timeout_ms: 60000
---

# GitHub Skill 执行指令

你正在执行 `github` Skill。根据用户需求选择对应脚本，调用 `skill_exec` 工具完成任务。

## 可用脚本

### search_repos — 关键词搜索仓库

适用：按关键词、编程语言搜索仓库。

调用方式：
```
skill_exec(skill_name="github", script="search_repos", args={
  "query": "machine learning",   // 必填，搜索关键词
  "language": "python",          // 可选，编程语言
  "sort": "stars",               // 可选：stars/forks/updated，默认 stars
  "per_page": 10                 // 可选，返回数量（最多 100），默认 10
})
```

返回：`items` 列表，每项含 full_name、stars、forks、description、language、url。

### get_trending — 发现 Trending 项目

适用：查看近期热门 GitHub 项目。

调用方式：
```
skill_exec(skill_name="github", script="get_trending", args={
  "language": "python",    // 可选，语言过滤，默认全语言
  "since": "daily"         // 可选：daily/weekly/monthly，默认 daily
})
```

返回：`items` 列表，含 full_name、stars、created_at、description、url。

### get_repo_info — 仓库详情

适用：查看指定仓库的详细信息（描述、Stars、README 预览等）。

调用方式：
```
skill_exec(skill_name="github", script="get_repo_info", args={
  "owner": "pytorch",   // 必填，仓库所有者
  "repo": "pytorch"     // 必填，仓库名称
})
```

返回：仓库完整信息，含 stars、topics、license、readme_preview。

### get_user_info — 用户信息

适用：查看 GitHub 用户主页及其热门项目。

调用方式：
```
skill_exec(skill_name="github", script="get_user_info", args={
  "username": "torvalds"   // 必填，GitHub 用户名
})
```

返回：用户信息 + `popular_repos` 列表（按 stars 排序的前 10 个仓库）。

### search_by_topic — 按 Topic 搜索

适用：探索特定领域的仓库（如 machine-learning、devops、blockchain）。

调用方式：
```
skill_exec(skill_name="github", script="search_by_topic", args={
  "topic": "machine-learning",   // 必填，topic 标签（用连字符）
  "per_page": 15                 // 可选，返回数量，默认 15
})
```

返回：`items` 列表，含 full_name、stars、topics、description、url。

### search_users — 搜索用户

适用：根据关键词查找 GitHub 用户。

调用方式：
```
skill_exec(skill_name="github", script="search_users", args={
  "query": "python developer",   // 必填，搜索关键词
  "per_page": 10,                // 可选，返回数量，默认 10
  "min_followers": 100           // 可选，最少关注者数量
})
```

返回：`items` 列表，含 login、type、url。

## 执行原则

1. 根据用户意图选择最合适的脚本，优先用最少的调用完成任务
2. 若需先定位再详查：先 search_repos 找到 full_name，再 get_repo_info 获取详情
3. 遇到 GitHub API 限速（403/429）时，提示用户配置 GITHUB_TOKEN 环境变量（60 → 5000 次/小时）
