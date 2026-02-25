"""获取 GitHub 用户信息及其热门仓库"""
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


def get_user_info(username: str) -> dict:
    """获取用户主页信息和热门仓库列表"""
    resp = requests.get(
        f"https://api.github.com/users/{username}",
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    user = resp.json()

    # 获取热门仓库（按 stars 排序）
    repos_resp = requests.get(
        f"https://api.github.com/users/{username}/repos",
        params={"sort": "stars", "direction": "desc", "per_page": 10},
        headers=_headers(),
        timeout=30,
    )
    repos_resp.raise_for_status()
    popular_repos = [
        {
            "name": r["name"],
            "description": r.get("description"),
            "stars": r["stargazers_count"],
            "forks": r["forks_count"],
            "language": r.get("language"),
            "url": r["html_url"],
        }
        for r in repos_resp.json()
    ]

    return {
        "login": user["login"],
        "name": user.get("name"),
        "bio": user.get("bio"),
        "followers": user["followers"],
        "following": user["following"],
        "public_repos": user["public_repos"],
        "company": user.get("company"),
        "location": user.get("location"),
        "blog": user.get("blog"),
        "created_at": user["created_at"][:10],
        "url": user["html_url"],
        "popular_repos": popular_repos,
    }


if __name__ == "__main__":
    args = json.loads(sys.stdin.read() or "{}")
    try:
        result = get_user_info(**args)
        print(json.dumps({"status": "success", **result}, ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
