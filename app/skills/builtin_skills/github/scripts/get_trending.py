"""获取 GitHub Trending 仓库（按时间周期和语言）"""
import json
import os
import sys
from datetime import datetime, timedelta

import requests


def _headers() -> dict:
    h = {"Accept": "application/vnd.github.v3+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        h["Authorization"] = f"token {token}"
    return h


def get_trending(language: str = "", since: str = "daily") -> dict:
    """获取 Trending 仓库，返回热门仓库列表"""
    days = {"daily": 1, "weekly": 7, "monthly": 30}.get(since, 1)
    date_from = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    query = f"created:>{date_from}"
    if language:
        query += f" language:{language}"
    params = {"q": query, "sort": "stars", "order": "desc", "per_page": 20}
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
            "created_at": r["created_at"][:10],
            "url": r["html_url"],
        }
        for i, r in enumerate(data["items"], 1)
    ]
    return {"since": since, "language": language or "all", "items": items}


if __name__ == "__main__":
    args = json.loads(sys.stdin.read() or "{}")
    try:
        result = get_trending(**args)
        print(json.dumps({"status": "success", **result}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
