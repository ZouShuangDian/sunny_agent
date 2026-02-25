"""搜索 GitHub 仓库（按关键词、语言过滤）"""
import json
import os
import sys
from typing import Optional

import requests


def _headers() -> dict:
    h = {"Accept": "application/vnd.github.v3+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        h["Authorization"] = f"token {token}"
    return h


def search_repos(
    query: str,
    sort: str = "stars",
    order: str = "desc",
    per_page: int = 10,
    language: Optional[str] = None,
) -> dict:
    """搜索 GitHub 仓库，返回仓库列表"""
    full_query = f"{query} language:{language}" if language else query
    params = {
        "q": full_query,
        "sort": sort,
        "order": order,
        "per_page": min(per_page, 100),
    }
    resp = requests.get(
        "https://api.github.com/search/repositories",
        params=params,
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    items = [
        {
            "rank": i,
            "full_name": r["full_name"],
            "description": r.get("description"),
            "stars": r["stargazers_count"],
            "forks": r["forks_count"],
            "language": r.get("language"),
            "url": r["html_url"],
        }
        for i, r in enumerate(data["items"], 1)
    ]
    return {"total_count": data["total_count"], "items": items}


if __name__ == "__main__":
    args = json.loads(sys.stdin.read() or "{}")
    try:
        result = search_repos(**args)
        print(json.dumps({"status": "success", **result}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
