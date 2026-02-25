# GitHub API Reference

This document provides reference information for GitHub API usage in this skill.

## Rate Limits

**Unauthenticated requests**: 60 requests per hour per IP address
**Authenticated requests**: 5,000 requests per hour (requires GitHub token)

To use authenticated requests, set the `GITHUB_TOKEN` environment variable:
```bash
export GITHUB_TOKEN="your_github_token_here"
```

Then modify script headers to include:
```python
headers = {
    "Accept": "application/vnd.github.v3+json",
    "Authorization": f"token {os.environ.get('GITHUB_TOKEN', '')}"
}
```

## Common Query Qualifiers

### Search Repositories

- `language:LANGUAGE` - Filter by programming language (e.g., `language:python`)
- `stars:>N` - Repositories with more than N stars (e.g., `stars:>1000`)
- `forks:>N` - Repositories with more than N forks
- `created:>YYYY-MM-DD` - Created after date
- `pushed:>YYYY-MM-DD` - Updated after date
- `topic:TOPIC` - Filter by topic
- `user:USERNAME` - Filter by user
- `org:ORGNAME` - Filter by organization
- `is:public` or `is:private` - Public or private repos
- `archived:false` - Exclude archived repositories

### Combine Qualifiers

Use spaces to combine multiple qualifiers:
```
machine learning language:python stars:>1000
```

## Popular Topics

- Machine Learning: `machine-learning`, `deep-learning`, `artificial-intelligence`
- Web Development: `web-development`, `frontend`, `backend`, `fullstack`
- Mobile: `android`, `ios`, `react-native`, `flutter`
- DevOps: `devops`, `kubernetes`, `docker`, `ci-cd`
- Data Science: `data-science`, `data-analysis`, `data-visualization`
- Blockchain: `blockchain`, `cryptocurrency`, `web3`
- Game Development: `game-development`, `unity`, `unreal-engine`

## Sort Options

- `stars` - Sort by star count (most popular)
- `forks` - Sort by fork count
- `updated` - Sort by last update time
- `help-wanted-issues` - Sort by help-wanted issues count

## Common Languages

python, javascript, java, typescript, go, rust, cpp, c, ruby, php, swift, kotlin, csharp, shell, html, css

## Useful API Endpoints

- Search repositories: `GET /search/repositories`
- Get repository: `GET /repos/{owner}/{repo}`
- Get user: `GET /users/{username}`
- Get user repos: `GET /users/{username}/repos`
- Get repository README: `GET /repos/{owner}/{repo}/readme`
- Get repository languages: `GET /repos/{owner}/{repo}/languages`
- Get repository topics: `GET /repos/{owner}/{repo}/topics`
