"""
Generates two SVG cards (assets/stats.svg, assets/top-langs.svg) and a JSON
file (assets/stats.json) for the profile README and for
stanislas-poisson.fr, using the GitHub REST API plus the public npm and
Packagist registries.

Auto-discovery: scans each owned public repo's root for a package.json
(counted if not "private") and a composer.json, then pulls real download
counts from npm and Packagist. No manual package list to maintain.
"""

import os
import json
import base64
import urllib.request
import urllib.error
from urllib.parse import quote
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


def gh_get_raw_file(owner, repo, path, ref):
    """Returns the raw text content of a file via the Contents API, or None."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={ref}"
    req = urllib.request.Request(url, headers=GH_HEADERS)
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    if isinstance(data, dict) and data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8", errors="ignore")
    return None


def public_get_json(url):
    """For npm / Packagist: no auth needed. Returns None on 404 or error."""
    req = urllib.request.Request(url, headers={"User-Agent": "zairakai-stats-bot"})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    except Exception:
        return None


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


def fetch_follower_count():
    profile = gh_get(f"https://api.github.com/users/{USERNAME}")
    return profile.get("followers", 0)


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
# Auto-discovery: npm / Packagist
# ---------------------------------------------------------------------------

def discover_packages(repos):
    npm_packages, packagist_packages = [], []
    for repo in repos:
        owner = repo["owner"]["login"]
        name = repo["name"]
        ref = repo.get("default_branch") or "main"

        pkg_json_raw = gh_get_raw_file(owner, name, "package.json", ref)
        if pkg_json_raw:
            try:
                data = json.loads(pkg_json_raw)
                pkg_name = data.get("name")
                if pkg_name and not data.get("private", False):
                    npm_packages.append(pkg_name)
            except json.JSONDecodeError:
                pass

        composer_json_raw = gh_get_raw_file(owner, name, "composer.json", ref)
        if composer_json_raw:
            try:
                data = json.loads(composer_json_raw)
                pkg_name = data.get("name")
                if pkg_name and "/" in pkg_name:
                    packagist_packages.append(pkg_name)
            except json.JSONDecodeError:
                pass

    return npm_packages, packagist_packages


def fetch_npm_downloads_total(packages):
    total, details = 0, []
    for name in packages:
        encoded = quote(name, safe="")
        data = public_get_json(f"https://api.npmjs.org/downloads/point/last-month/{encoded}")
        downloads = data.get("downloads", 0) if data else 0
        total += downloads
        details.append({"name": name, "downloads_last_month": downloads})
    return total, details


def fetch_packagist_downloads_total(packages):
    total, details = 0, []
    for name in packages:
        data = public_get_json(f"https://packagist.org/packages/{name}.json")
        downloads = 0
        if data:
            downloads = data.get("package", {}).get("downloads", {}).get("total", 0)
        total += downloads
        details.append({"name": name, "downloads_total": downloads})
    return total, details


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def format_count(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def render_stats_svg(npm_total, packagist_total, followers, height):
    """height is passed in so this card always matches the languages card."""
    rows_data = [
        ("npm (30 derniers jours) :", format_count(npm_total)),
        ("Packagist (total) :", format_count(packagist_total)),
        ("Followers :", str(followers)),
    ]
    usable_height = height - TOP_MARGIN - BOTTOM_MARGIN
    step = usable_height / len(rows_data)
    rows = []
    for i, (label, value) in enumerate(rows_data):
        y = TOP_MARGIN + 10 + i * step
        rows.append(f"""
  <text x="20" y="{y:.1f}" class="label">{label}</text>
  <text x="260" y="{y:.1f}" class="value">{value}</text>""")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="420" height="{height}" viewBox="0 0 420 {height}">
  <style>
    .title {{ font: 600 16px sans-serif; fill: #333; }}
    .label {{ font: 14px sans-serif; fill: #555; }}
    .value {{ font: 600 14px sans-serif; fill: #0969da; }}
  </style>
  <rect width="420" height="{height}" rx="8" fill="#fff" stroke="#e1e4e8" />
  <text x="20" y="28" class="title">{USERNAME} · Packages &amp; Community</text>
  {''.join(rows)}
</svg>"""


def render_top_langs_svg(top_langs):
    height = TOP_MARGIN + ROW_HEIGHT * len(top_langs) + BOTTOM_MARGIN
    rows, y = [], TOP_MARGIN + 10
    for entry in top_langs:
        lang, pct = entry["name"], entry["pct"]
        color = LANG_COLORS.get(lang, FALLBACK_COLOR)
        bar_width = 2.2 * pct
        rows.append(f"""
  <text x="20" y="{y}" class="label">{lang}</text>
  <rect x="20" y="{y + 6}" width="{bar_width:.1f}" height="8" rx="4" fill="{color}" />
  <text x="{20 + bar_width + 10:.1f}" y="{y + 13}" class="pct">{pct:.1f}%</text>""")
        y += ROW_HEIGHT

    rows_svg = "".join(rows)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="420" height="{height}" viewBox="0 0 420 {height}">
  <style>
    .title {{ font: 600 16px sans-serif; fill: #333; }}
    .label {{ font: 13px sans-serif; fill: #555; }}
    .pct {{ font: 12px sans-serif; fill: #555; }}
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

    repos = fetch_public_repos()

    lang_totals = language_totals(repos)
    top_langs = bucket_top_n_with_rest(lang_totals, top_n=3)

    npm_packages, packagist_packages = discover_packages(repos)
    npm_total, npm_details = fetch_npm_downloads_total(npm_packages)
    packagist_total, packagist_details = fetch_packagist_downloads_total(packagist_packages)
    followers = fetch_follower_count()

    langs_svg = render_top_langs_svg(top_langs)
    lang_height = TOP_MARGIN + ROW_HEIGHT * len(top_langs) + BOTTOM_MARGIN

    with open("assets/stats.svg", "w", encoding="utf-8") as f:
        f.write(render_stats_svg(npm_total, packagist_total, followers, height=lang_height))

    with open("assets/top-langs.svg", "w", encoding="utf-8") as f:
        f.write(langs_svg)

    stats_json = {
        "public_repos": len(repos),
        "npm_downloads_last_month": npm_total,
        "npm_packages": npm_details,
        "packagist_downloads_total": packagist_total,
        "packagist_packages": packagist_details,
        "followers": followers,
        "languages": top_langs,
    }
    with open("assets/stats.json", "w", encoding="utf-8") as f:
        json.dump(stats_json, f, indent=2, ensure_ascii=False)

    print(f"{len(repos)} public repos scanned")
    print(f"npm: {len(npm_packages)} packages, {npm_total} downloads (30d)")
    print(f"Packagist: {len(packagist_packages)} packages, {packagist_total} downloads (total)")
    print(f"Followers: {followers}")
    print(f"Top languages: {top_langs}")


if __name__ == "__main__":
    main()
