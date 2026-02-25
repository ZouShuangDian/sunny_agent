"""搜索 GitHub 用户"""
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


def search_users(
    query: str,
    per_page: int = 10,
    min_followers: Optional[int] = None,
) -> dict:
    """搜索 GitHub 用户，返回用户列表"""
    full_query = query
    if min_followers is not None:
        full_query = f"{query} followers:>={min_followers}"
    params = {
        "q": full_query,
        "sort": "followers",
        "order": "desc",
        "per_page": min(per_page, 30),
    }
    resp = requests.get(
        "https://api.github.com/search/users",
        params=params,
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    items = [
        {
            "rank": i,
            "login": u["login"],
            "type": u["type"],
            "url": u["html_url"],
        }
        for i, u in enumerate(data["items"], 1)
    ]
    return {"total_count": data["total_count"], "items": items}


if __name__ == "__main__":
    args = json.loads(sys.stdin.read() or "{}")
    try:
        result = search_users(**args)
        print(json.dumps({"status": "success", **result}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
