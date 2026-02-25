"""按 GitHub Topic 标签搜索仓库"""
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


def search_by_topic(topic: str, per_page: int = 15) -> dict:
    """按 topic 标签搜索仓库，返回仓库列表"""
    params = {
        "q": f"topic:{topic}",
        "sort": "stars",
        "order": "desc",
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
            "topics": r.get("topics", [])[:5],
            "url": r["html_url"],
        }
        for i, r in enumerate(data["items"], 1)
    ]
    return {"topic": topic, "total_count": data["total_count"], "items": items}


if __name__ == "__main__":
    args = json.loads(sys.stdin.read() or "{}")
    try:
        result = search_by_topic(**args)
        print(json.dumps({"status": "success", **result}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
