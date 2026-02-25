"""获取指定 GitHub 仓库的详细信息（含 README 预览）"""
import base64
import json
import os
import sys

import requests


def _headers() -> dict:
    h = {"Accept": "application/vnd.github.v3+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        h["Authorization"] = f"token {token}"
    return h


def get_repo_info(owner: str, repo: str) -> dict:
    """获取仓库详情，包含 README 预览"""
    resp = requests.get(
        f"https://api.github.com/repos/{owner}/{repo}",
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    # 获取 README 预览
    readme_preview = ""
    readme_resp = requests.get(
        f"https://api.github.com/repos/{owner}/{repo}/readme",
        headers=_headers(),
        timeout=30,
    )
    if readme_resp.status_code == 200:
        content = base64.b64decode(readme_resp.json()["content"]).decode("utf-8", errors="replace")
        readme_preview = content[:1000] + ("..." if len(content) > 1000 else "")

    return {
        "full_name": data["full_name"],
        "description": data.get("description"),
        "stars": data["stargazers_count"],
        "forks": data["forks_count"],
        "watchers": data["watchers_count"],
        "open_issues": data["open_issues_count"],
        "language": data.get("language"),
        "topics": data.get("topics", []),
        "license": data["license"]["name"] if data.get("license") else None,
        "homepage": data.get("homepage"),
        "created_at": data["created_at"][:10],
        "updated_at": data["updated_at"][:10],
        "url": data["html_url"],
        "readme_preview": readme_preview,
    }


if __name__ == "__main__":
    args = json.loads(sys.stdin.read() or "{}")
    try:
        result = get_repo_info(**args)
        print(json.dumps({"status": "success", **result}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
