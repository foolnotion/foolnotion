#!/usr/bin/env python3
import os
import re
import sys
import requests

USERNAME = os.environ["GH_USERNAME"]
TOKEN = os.environ["GH_TOKEN"]
README_PATH = os.environ.get("README_PATH", "README.md")
TOP_N = int(os.environ.get("TOP_N_LANGS", "6"))

# Repos owned by other orgs where USERNAME is the primary developer but which
# ownerAffiliations: OWNER won't pick up. "owner/name" format.
EXTRA_REPOS = [
    r for r in os.environ.get("EXTRA_REPOS", "").split(",") if r.strip()
]

API = "https://api.github.com/graphql"
HEADERS = {"Authorization": f"bearer {TOKEN}"}

OWNED_QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    repositories(first: 100, after: $after, ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC) {
      pageInfo { hasNextPage endCursor }
      nodes {
        stargazerCount
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""

EXTRA_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    stargazerCount
    languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
      edges { size node { name color } }
    }
  }
}
"""


def graphql(query, variables):
    resp = requests.post(API, json={"query": query, "variables": variables}, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        print(data["errors"], file=sys.stderr)
        sys.exit(1)
    return data["data"]


def fetch_all_repos():
    repos = []
    after = None
    while True:
        block = graphql(OWNED_QUERY, {"login": USERNAME, "after": after})["user"]["repositories"]
        repos.extend(block["nodes"])
        if not block["pageInfo"]["hasNextPage"]:
            break
        after = block["pageInfo"]["endCursor"]

    for full_name in EXTRA_REPOS:
        owner, name = full_name.strip().split("/", 1)
        repo = graphql(EXTRA_QUERY, {"owner": owner, "name": name})["repository"]
        repos.append(repo)

    return repos


def build_stats(repos):
    total_stars = sum(r["stargazerCount"] for r in repos)
    lang_bytes = {}
    lang_color = {}
    for r in repos:
        for edge in r["languages"]["edges"]:
            name = edge["node"]["name"]
            lang_bytes[name] = lang_bytes.get(name, 0) + edge["size"]
            lang_color[name] = edge["node"]["color"] or "#ccc"
    total_bytes = sum(lang_bytes.values()) or 1
    top_langs = sorted(lang_bytes.items(), key=lambda kv: kv[1], reverse=True)[:TOP_N]
    return total_stars, top_langs, lang_color, total_bytes


def render_bar(pct, width=20):
    filled = round(width * pct / 100)
    return "█" * filled + "░" * (width - filled)


def render_markdown(total_stars, top_langs, lang_color, total_bytes):
    lines = []
    lines.append(f"**Total Stars:** {total_stars} ⭐\n")
    lines.append("**Top Languages:**\n")
    lines.append("```text")
    for name, size in top_langs:
        pct = 100 * size / total_bytes
        lines.append(f"{name:<15} {render_bar(pct)} {pct:5.1f}%")
    lines.append("```")
    return "\n".join(lines)


def update_readme(content):
    with open(README_PATH, "r", encoding="utf-8") as f:
        readme = f.read()

    pattern = re.compile(
        r"(<!--START_SECTION:stats-->).*(<!--END_SECTION:stats-->)", re.DOTALL
    )
    replacement = f"\\1\n{content}\n\\2"
    if not pattern.search(readme):
        print("No <!--START_SECTION:stats--> / <!--END_SECTION:stats--> markers found in README.md", file=sys.stderr)
        sys.exit(1)
    new_readme = pattern.sub(replacement, readme)

    if new_readme != readme:
        with open(README_PATH, "w", encoding="utf-8") as f:
            f.write(new_readme)
        print("README updated.")
    else:
        print("No changes.")


def main():
    repos = fetch_all_repos()
    total_stars, top_langs, lang_color, total_bytes = build_stats(repos)
    content = render_markdown(total_stars, top_langs, lang_color, total_bytes)
    update_readme(content)


if __name__ == "__main__":
    main()
