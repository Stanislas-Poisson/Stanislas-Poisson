"""
Generates two SVG cards (assets/stats.svg, assets/top-langs.svg) and a JSON
file (assets/stats.json) for the profile README and for
stanislas-poisson.fr, using only the GitHub REST API.

GitHub-native metrics only (repos, gists, stars, followers) - no npm or
Packagist here, since nothing is published from GitHub for this account.
"""

import os
import json
from datetime import datetime, timezone
import urllib.request
import urllib.error
from collections import defaultdict

USERNAME = os.environ["GH_USERNAME"]
TOKEN = os.environ["GH_TOKEN"]

GH_HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "User-Agent": USERNAME,
}

LANG_COLORS = {
    "PHP": "#777bb4",
    "HTML": "#e34c26",
    "JavaScript": "#f7df1e",
    "TypeScript": "#3178c6",
    "CSS": "#563d7c",
    "Python": "#3572A5",
}
FALLBACK_COLOR = "#8c8c8c"

# Shared layout constants so the stats card and the languages card always
# end up with the exact same height, with a real bottom margin (no clipping).
TOP_MARGIN = 45
ROW_HEIGHT = 30
BOTTOM_MARGIN = 20


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def gh_get(url):
    req = urllib.request.Request(url, headers=GH_HEADERS)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------------------
# GitHub data
# ---------------------------------------------------------------------------

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


def fetch_profile():
    """Single call for public_repos, public_gists and followers - all
    GitHub-native counters, no external registries involved."""
    profile = gh_get(f"https://api.github.com/users/{USERNAME}")
    return {
        "public_repos": profile.get("public_repos", 0),
        "public_gists": profile.get("public_gists", 0),
        "followers": profile.get("followers", 0),
    }


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


def bucket_top_n_with_rest(totals, top_n=3):
    """Top N languages by byte weight, plus an 'Autres' bucket for the rest."""
    top = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    total = sum(v for _, v in top) or 1
    result, running = [], 0.0
    for lang, val in top[:top_n]:
        pct = round(100 * val / total, 1)
        result.append({"name": lang, "pct": pct})
        running += pct
    rest = round(100 - running, 1)
    if rest > 0.05:
        result.append({"name": "Autres", "pct": rest})
    return result


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def format_count(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def render_stats_svg(repo_count, gist_count, star_total, followers, height, generated_at):
    """height is passed in so this card always matches the languages card.
    generated_at is embedded as an SVG comment so the file content always
    changes on every run, even when the underlying numbers are identical -
    otherwise git sees no diff and silently skips committing this file."""
    rows_data = [
        ("Repos publics :", str(repo_count)),
        ("Gists publics :", str(gist_count)),
        ("Total stars :", format_count(star_total)),
        ("Followers :", str(followers)),
    ]
    usable_height = height - TOP_MARGIN - BOTTOM_MARGIN
    step = usable_height / len(rows_data)
    rows = []
    for i, (label, value) in enumerate(rows_data):
        y = TOP_MARGIN + 10 + i * step
        rows.append(f"""
  <text x="20" y="{y:.1f}" font-family="sans-serif" font-size="14" fill="#555">{label}</text>
  <text x="260" y="{y:.1f}" font-family="sans-serif" font-size="14" font-weight="600" fill="#0969da">{value}</text>""")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="420" height="{height}" viewBox="0 0 420 {height}">
  <!-- generated_at: {generated_at} -->
  <style>
    .title {{ font: 600 16px sans-serif; fill: #333; }}
  </style>
  <rect width="420" height="{height}" rx="8" fill="#fff" stroke="#e1e4e8" />
  <text x="20" y="28" class="title">{USERNAME} · GitHub Stats</text>
  {''.join(rows)}
</svg>"""


def render_top_langs_svg(top_langs, generated_at):
    height = TOP_MARGIN + ROW_HEIGHT * len(top_langs) + BOTTOM_MARGIN
    rows, y = [], TOP_MARGIN + 10
    for entry in top_langs:
        lang, pct = entry["name"], entry["pct"]
        color = LANG_COLORS.get(lang, FALLBACK_COLOR)
        bar_width = 2.2 * pct
        rows.append(f"""
  <text x="20" y="{y}" font-family="sans-serif" font-size="13" fill="#555">{lang}</text>
  <rect x="20" y="{y + 6}" width="{bar_width:.1f}" height="8" rx="4" fill="{color}" />
  <text x="{20 + bar_width + 10:.1f}" y="{y + 13}" font-family="sans-serif" font-size="12" fill="#555">{pct:.1f}%</text>""")
        y += ROW_HEIGHT

    rows_svg = "".join(rows)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="420" height="{height}" viewBox="0 0 420 {height}">
  <!-- generated_at: {generated_at} -->
  <style>
    .title {{ font: 600 16px sans-serif; fill: #333; }}
  </style>
  <rect width="420" height="{height}" rx="8" fill="#fff" stroke="#e1e4e8" />
  <text x="20" y="30" class="title">Most used languages (public repos)</text>
  {rows_svg}
</svg>"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    os.makedirs("assets", exist_ok=True)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    repos = fetch_public_repos()
    profile = fetch_profile()
    star_total = sum(r.get("stargazers_count", 0) for r in repos)

    lang_totals = language_totals(repos)
    top_langs = bucket_top_n_with_rest(lang_totals, top_n=3)

    langs_svg = render_top_langs_svg(top_langs, generated_at)
    lang_height = TOP_MARGIN + ROW_HEIGHT * len(top_langs) + BOTTOM_MARGIN

    with open("assets/stats.svg", "w", encoding="utf-8") as f:
        f.write(render_stats_svg(
            profile["public_repos"], profile["public_gists"], star_total,
            profile["followers"], height=lang_height, generated_at=generated_at))

    with open("assets/top-langs.svg", "w", encoding="utf-8") as f:
        f.write(langs_svg)

    stats_json = {
        "generated_at": generated_at,
        "public_repos": profile["public_repos"],
        "public_gists": profile["public_gists"],
        "total_stars": star_total,
        "followers": profile["followers"],
        "languages": top_langs,
    }
    with open("assets/stats.json", "w", encoding="utf-8") as f:
        json.dump(stats_json, f, indent=2, ensure_ascii=False)

    print(f"{profile['public_repos']} public repos, {profile['public_gists']} gists, "
          f"{star_total} stars, {profile['followers']} followers")
    print(f"Top languages: {top_langs}")


if __name__ == "__main__":
    main()
