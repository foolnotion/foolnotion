#!/usr/bin/env python3
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

USERNAME = os.environ["GH_USERNAME"]
TOKEN = os.environ["GH_TOKEN"]
README_PATH = os.environ.get("README_PATH", "README.md")
SVG_PATH = os.environ.get("SVG_PATH", "top-langs.svg")
TOP_N = int(os.environ.get("TOP_N_LANGS", "6"))

# Repos owned by other orgs where USERNAME is the primary developer but which
# ownerAffiliations: OWNER won't pick up. "owner/name" format.
EXTRA_REPOS = [
    r for r in os.environ.get("EXTRA_REPOS", "").split(",") if r.strip()
]

# Repos to skip when aggregating languages (still counted for stars) because
# they vendor large third-party codebases that would otherwise dominate the
# byte counts. Bare repo names, matched against any owner.
EXCLUDE_LANG_REPOS = {
    r.strip() for r in os.environ.get("EXCLUDE_LANG_REPOS", "").split(",") if r.strip()
}

API = "https://api.github.com/graphql"
HEADERS = {"Authorization": f"bearer {TOKEN}"}

OWNED_QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    repositories(first: 100, after: $after, ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        stargazerCount
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""

CREATED_AT_QUERY = """
query($login: String!) {
  user(login: $login) { createdAt }
}
"""

CONTRIBUTIONS_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""

EXTRA_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    name
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


def fetch_contribution_stats():
    created_at = graphql(CREATED_AT_QUERY, {"login": USERNAME})["user"]["createdAt"]
    start_year = int(created_at[:4])
    current_year = datetime.now(timezone.utc).year
    now = datetime.now(timezone.utc)

    total_commits = 0
    days = {}
    for year in range(start_year, current_year + 1):
        frm = f"{year}-01-01T00:00:00Z"
        to_dt = min(datetime(year + 1, 1, 1, tzinfo=timezone.utc), now)
        to = to_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        collection = graphql(CONTRIBUTIONS_QUERY, {"login": USERNAME, "from": frm, "to": to})["user"][
            "contributionsCollection"
        ]
        total_commits += collection["totalCommitContributions"]
        for week in collection["contributionCalendar"]["weeks"]:
            for day in week["contributionDays"]:
                days[day["date"]] = day["contributionCount"]

    sorted_days = sorted(days.items())

    longest_streak = 0
    running = 0
    for _, count in sorted_days:
        if count > 0:
            running += 1
            longest_streak = max(longest_streak, running)
        else:
            running = 0

    idx = len(sorted_days) - 1
    if idx >= 0 and sorted_days[idx][1] == 0:
        idx -= 1  # today isn't over yet; don't let it break the streak
    current_streak = 0
    while idx >= 0 and sorted_days[idx][1] > 0:
        current_streak += 1
        idx -= 1

    return total_commits, current_streak, longest_streak


def build_stats(repos):
    total_stars = sum(r["stargazerCount"] for r in repos)
    lang_bytes = {}
    lang_color = {}
    for r in repos:
        if r["name"] in EXCLUDE_LANG_REPOS:
            continue
        for edge in r["languages"]["edges"]:
            name = edge["node"]["name"]
            lang_bytes[name] = lang_bytes.get(name, 0) + edge["size"]
            lang_color[name] = edge["node"]["color"] or "#ccc"
    total_bytes = sum(lang_bytes.values()) or 1
    top_langs = sorted(lang_bytes.items(), key=lambda kv: kv[1], reverse=True)[:TOP_N]
    return total_stars, top_langs, lang_color, total_bytes


ROW_HEIGHT = 34
CHART_WIDTH = 480
PADDING = 14
BAR_HEIGHT = 8
TRACK_COLOR = "#6e768133"
TEXT_COLOR = "#6e7681"


def render_svg(top_langs, lang_color, total_bytes):
    height = PADDING * 2 + len(top_langs) * ROW_HEIGHT
    bar_max_width = CHART_WIDTH - PADDING * 2

    rows = []
    for i, (name, size) in enumerate(top_langs):
        pct = 100 * size / total_bytes
        bar_width = bar_max_width * pct / 100
        text_y = PADDING + i * ROW_HEIGHT + 12
        bar_y = PADDING + i * ROW_HEIGHT + 18
        color = lang_color.get(name, "#8f8f8f")
        rows.append(f'''
    <circle cx="{PADDING + 4}" cy="{text_y - 4}" r="4" fill="{color}" />
    <text x="{PADDING + 14}" y="{text_y}" font-size="13" fill="{TEXT_COLOR}">{name}</text>
    <text x="{CHART_WIDTH - PADDING}" y="{text_y}" font-size="12" fill="{TEXT_COLOR}" text-anchor="end">{pct:.1f}%</text>
    <rect x="{PADDING}" y="{bar_y}" width="{bar_max_width}" height="{BAR_HEIGHT}" rx="4" fill="{TRACK_COLOR}" />
    <rect x="{PADDING}" y="{bar_y}" width="{bar_width:.1f}" height="{BAR_HEIGHT}" rx="4" fill="{color}" />''')

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{CHART_WIDTH}" height="{height}" viewBox="0 0 {CHART_WIDTH} {height}" font-family="-apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif">{"".join(rows)}
</svg>
'''


def render_markdown(total_stars, total_commits, current_streak, longest_streak):
    return (
        f"**Total Stars:** {total_stars}\n\n"
        f"**Total Commits:** {total_commits}\n\n"
        f"**Current Streak:** {current_streak} days &nbsp;(longest: {longest_streak} days)\n\n"
        f"**Top Languages:**\n\n"
        f"![Top Languages]({SVG_PATH})"
    )


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
    total_commits, current_streak, longest_streak = fetch_contribution_stats()

    with open(SVG_PATH, "w", encoding="utf-8") as f:
        f.write(render_svg(top_langs, lang_color, total_bytes))

    update_readme(render_markdown(total_stars, total_commits, current_streak, longest_streak))


if __name__ == "__main__":
    main()
