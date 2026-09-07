"""
Generates two simple SVG cards (assets/stats.svg, assets/top-langs.svg) for
the profile README, using only the GitHub REST API. No third-party stats
service involved: this is fully self-contained and only depends on GitHub
being up.
"""

import os
import urllib.request
import json
from collections import defaultdict

USERNAME = os.environ["GH_USERNAME"]
TOKEN = os.environ["GH_TOKEN"]

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "User-Agent": USERNAME,
}


def gh_get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def fetch_public_repos():
    repos = []
    page = 1
    while True:
        url = f"https://api.github.com/users/{USERNAME}/repos?per_page=100&page={page}&type=owner"
        batch = gh_get(url)
        if not batch:
            break
        repos.extend(batch)
        page += 1
    return [r for r in repos if not r["fork"]]


def language_totals(repos):
    totals = defaultdict(int)
    for repo in repos:
        try:
            langs = gh_get(repo["languages_url"])
        except Exception:
            continue
        for lang, bytes_count in langs.items():
            totals[lang] += bytes_count
    return totals


def render_stats_svg(repo_count, star_count, fork_count):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="400" height="120" viewBox="0 0 400 120">
  <style>
    .title {{ font: 600 16px sans-serif; fill: #333; }}
    .label {{ font: 14px sans-serif; fill: #555; }}
    .value {{ font: 600 14px sans-serif; fill: #0969da; }}
  </style>
  <rect width="400" height="120" rx="8" fill="#fff" stroke="#e1e4e8" />
  <text x="20" y="30" class="title">{USERNAME}'s GitHub Stats (public repos)</text>
  <text x="20" y="60" class="label">Public repositories:</text>
  <text x="220" y="60" class="value">{repo_count}</text>
  <text x="20" y="85" class="label">Total stars:</text>
  <text x="220" y="85" class="value">{star_count}</text>
  <text x="20" y="110" class="label">Total forks:</text>
  <text x="220" y="110" class="value">{fork_count}</text>
</svg>"""


def render_top_langs_svg(totals, top_n=6):
    top = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    total_bytes = sum(v for _, v in top) or 1
    height = 40 + 28 * len(top)
    rows = []
    y = 55
    for lang, bytes_count in top:
        pct = 100 * bytes_count / total_bytes
        bar_width = 2.2 * pct
        rows.append(f"""
  <text x="20" y="{y}" class="label">{lang}</text>
  <rect x="20" y="{y + 6}" width="{bar_width:.1f}" height="8" rx="4" fill="#0969da" />
  <text x="{20 + bar_width + 10:.1f}" y="{y + 13}" class="pct">{pct:.1f}%</text>""")
        y += 28

    rows_svg = "".join(rows)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="400" height="{height}" viewBox="0 0 400 {height}">
  <style>
    .title {{ font: 600 16px sans-serif; fill: #333; }}
    .label {{ font: 13px sans-serif; fill: #555; }}
    .pct {{ font: 12px sans-serif; fill: #555; }}
  </style>
  <rect width="400" height="{height}" rx="8" fill="#fff" stroke="#e1e4e8" />
  <text x="20" y="30" class="title">Most used languages (public repos)</text>
  {rows_svg}
</svg>"""


def main():
    os.makedirs("assets", exist_ok=True)

    repos = fetch_public_repos()
    repo_count = len(repos)
    star_count = sum(r["stargazers_count"] for r in repos)
    fork_count = sum(r["forks_count"] for r in repos)

    with open("assets/stats.svg", "w", encoding="utf-8") as f:
        f.write(render_stats_svg(repo_count, star_count, fork_count))

    totals = language_totals(repos)
    with open("assets/top-langs.svg", "w", encoding="utf-8") as f:
        f.write(render_top_langs_svg(totals))

    print(f"Generated stats for {repo_count} public repos, {star_count} stars, {fork_count} forks.")
    print(f"Top languages: {sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:6]}")


if __name__ == "__main__":
    main()
