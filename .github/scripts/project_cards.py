#!/usr/bin/env python3
"""Builds one statistics card per project, an overview card and the history in a JSON file.

The projects are listed in `.github/projects.json`. For each one, the script reads the hosting platform (GitHub
or GitLab, public data) and the package registry (Packagist or npm), then writes `<slug>.svg` in OUT_DIR. The
dataset French-Postal-Code gets its own three cards (data.gouv.fr, GitHub and content) from data.gouv.fr and its releases.

Environment:
    PLATFORM         github, gitlab or all (default): the projects to build
    OUT_DIR          directory of the cards and of `stats.json`, which keeps the history (default assets/projects)
    GH_TOKEN         GitHub token. Reading the traffic of a repository needs the Administration permission on it
    GITLAB_API_TOKEN GitLab token that can read the group (open issues are hidden without one)
    PROJECTS         path of the project list (default .github/projects.json)
    GITLAB_MIN_INTERVAL  seconds between two requests to gitlab.com (default 0.8)
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html import escape
from pathlib import Path

PLATFORM = os.environ.get("PLATFORM", "all")
OUT_DIR = Path(os.environ.get("OUT_DIR", "assets/projects"))
GH_TOKEN = os.environ.get("GH_TOKEN", "")
GITLAB_TOKEN = os.environ.get("GITLAB_API_TOKEN", "")
PROJECTS = Path(os.environ.get("PROJECTS", ".github/projects.json"))

# Transient statuses worth retrying: rate limit and server-side errors.
RETRY_STATUSES = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 4
MAX_WAIT = 60
# GitLab.com limits an authenticated user to a burst of 100 requests a minute: stay below it.
GITLAB_MIN_INTERVAL = float(os.environ.get("GITLAB_MIN_INTERVAL", "0.8"))
_last_gitlab_call = 0.0

DATA_GOUV_LOGO_URL = "https://www.data.gouv.fr/nuxt_images/favicon.svg"
GITHUB_ICON = (
    "M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04"
    "-3.338.724-4.042-1.61-4.042-1.61-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.084-.729.084-.729 1.205.084 1.838 "
    "1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-"
    "1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 "
    "2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-"
    "2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 "
    "17.592 24 12.297c0-6.627-5.373-12-12-12"
)
GITLAB_ICON = "M12 22.4 1.5 14.8l1.6-4.9L6.2 2.4l2.2 6.6h7.2l2.2-6.6 3.1 7.5 1.6 4.9z"


def throttle(url: str) -> None:
    """Spaces the requests sent to gitlab.com."""
    global _last_gitlab_call
    if url.startswith("https://gitlab.com/"):
        wait = GITLAB_MIN_INTERVAL - (time.monotonic() - _last_gitlab_call)
        if wait > 0:
            time.sleep(wait)
        _last_gitlab_call = time.monotonic()


def fetch(url: str, headers: dict | None = None, *, binary: bool = False):
    """Returns the decoded JSON (or the raw bytes) of a URL, None when it cannot be read."""
    request = urllib.request.Request(url, headers={"User-Agent": "project-cards", **(headers or {})})
    for attempt in range(1, MAX_ATTEMPTS + 1):
        throttle(url)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
            break
        except urllib.error.HTTPError as error:
            if error.code in RETRY_STATUSES and attempt < MAX_ATTEMPTS:
                retry_after = error.headers.get("Retry-After", "")
                time.sleep(min(int(retry_after) if retry_after.isdigit() else 2**attempt, MAX_WAIT))
                continue
            # The response body of an HTTP error says which permission is missing.
            detail = error.read().decode("utf-8", errors="replace")[:300]
            print(f"warning: cannot read {url}: {error} {detail}", file=sys.stderr)
            return None
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt < MAX_ATTEMPTS:
                time.sleep(min(2**attempt, MAX_WAIT))
                continue
            print(f"warning: cannot read {url}: {error}", file=sys.stderr)
            return None
    try:
        return body if binary else json.loads(body)
    except json.JSONDecodeError:
        return None


def github(path: str):
    headers = {"Accept": "application/vnd.github+json"}
    if GH_TOKEN:
        headers["Authorization"] = f"Bearer {GH_TOKEN}"
    return fetch(f"https://api.github.com{path}", headers)


def gitlab(path: str):
    headers = {"PRIVATE-TOKEN": GITLAB_TOKEN} if GITLAB_TOKEN else {}
    return fetch(f"https://gitlab.com/api/v4{path}", headers)


def compact(value: float | None) -> str:
    """49806 -> 49.8K, 1147 -> 1.1K, 3 -> 3."""
    if value is None:
        return "–"
    value = int(value)
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if value >= limit:
            return f"{value / limit:.1f}".rstrip("0").rstrip(".") + suffix
    return str(value)


def month_year(iso: str | None) -> str:
    if not iso:
        return "–"
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%b %Y")


def data_uri(content: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(content).decode()}"


def registry(spec: str | None) -> dict:
    """Version and downloads of a package: `packagist:vendor/name` or `npm:@scope/name`."""
    if not spec:
        return {}
    kind, name = spec.split(":", 1)
    if kind == "packagist":
        package = (fetch(f"https://packagist.org/packages/{name}.json") or {}).get("package", {})
        releases = ((fetch(f"https://repo.packagist.org/p2/{name}.json") or {}).get("packages", {}).get(name)) or [{}]
        downloads = package.get("downloads", {})
        return {
            "version": releases[0].get("version"),
            "monthly": downloads.get("monthly"),
            "total": downloads.get("total"),
            "stars": package.get("favers"),
            "details": {
                "name": name,
                "downloads": {"daily": downloads.get("daily"), "monthly": downloads.get("monthly"), "total": downloads.get("total")},
                "favers": package.get("favers"),
                "dependents": package.get("dependents"),
                "suggesters": package.get("suggesters"),
                "versions": len(package.get("versions", {})),
                "latest_release_at": (releases[0] or {}).get("time"),
                "language": package.get("language"),
            },
        }
    quoted = urllib.parse.quote(name, safe="@")
    latest = fetch(f"https://registry.npmjs.org/{quoted.replace('/', '%2f')}/latest") or {}
    month = fetch(f"https://api.npmjs.org/downloads/point/last-month/{quoted}") or {}
    year = fetch(f"https://api.npmjs.org/downloads/point/last-year/{quoted}") or {}
    meta = fetch(f"https://registry.npmjs.org/{quoted.replace('/', '%2f')}") or {}
    day = fetch(f"https://api.npmjs.org/downloads/point/last-day/{quoted}") or {}
    week = fetch(f"https://api.npmjs.org/downloads/point/last-week/{quoted}") or {}
    return {
        "version": latest.get("version"),
        "monthly": month.get("downloads"),
        "total": year.get("downloads"),
        "total_label": "downloads / year",
        "details": {
            "name": name,
            "downloads": {"daily": day.get("downloads"), "weekly": week.get("downloads"), "monthly": month.get("downloads"), "yearly": year.get("downloads")},
            "versions": len(meta.get("versions", {})),
            "created_at": meta.get("time", {}).get("created"),
            "latest_release_at": meta.get("time", {}).get(latest.get("version", "")),
            "license": latest.get("license"),
            "maintainers": len(meta.get("maintainers", [])),
            "unpacked_size": (latest.get("dist") or {}).get("unpackedSize"),
            "dependencies": len(latest.get("dependencies", {})),
        },
    }


def merge_traffic(history: dict, kind: str, payload: dict | None) -> None:
    """Merges the daily values of a traffic endpoint into the history, one entry per day."""
    if not payload:
        return
    days = history.setdefault(kind, {})
    for entry in payload.get(kind, []):
        days[entry["timestamp"][:10]] = {"count": entry["count"], "uniques": entry["uniques"]}


def read_traffic(repo: str, history: dict) -> tuple[dict, dict | None]:
    """Adds the last 14 days of views and clones to the history, returns it with the raw views of the period."""
    views = github(f"/repos/{repo}/traffic/views")
    merge_traffic(history, "views", views)
    merge_traffic(history, "clones", github(f"/repos/{repo}/traffic/clones"))
    return history, views


def since_at(history: dict) -> str | None:
    """Datetime of the first day of the traffic history."""
    first = min(history.get("views", {}) | history.get("clones", {}), default=None)
    return f"{first}T00:00:00Z" if first else None


def fetch_total(url: str, headers: dict | None = None) -> int | None:
    """Number of items of a paginated list: reads the last page of `per_page=1` (GitHub) or `X-Total` (GitLab)."""
    request = urllib.request.Request(url, headers={"User-Agent": "project-cards", **(headers or {})})
    throttle(url)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.headers.get("X-Total"):
                return int(response.headers["X-Total"])
            last = re.search(r'[?&]page=(\d+)[^>]*>; rel="last"', response.headers.get("Link", ""))
            if last:
                return int(last.group(1))
            body = json.loads(response.read())
            return len(body) if isinstance(body, list) else None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def github_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if GH_TOKEN:
        headers["Authorization"] = f"Bearer {GH_TOKEN}"
    return headers


def share(languages: dict) -> dict:
    """Bytes of code by language -> {language: {"bytes": n, "percent": p}}."""
    total = sum(languages.values()) or 1
    return {name: {"bytes": size, "percent": round(size * 100 / total, 1)} for name, size in sorted(languages.items(), key=lambda i: -i[1])}


def github_details(repo: str, info: dict, releases: list, history: dict | None) -> dict:
    """Everything useful that GitHub says about a repository, for the JSON file (the cards use a part of it)."""
    api = f"https://api.github.com/repos/{repo}"
    headers = github_headers()
    community = github(f"/repos/{repo}/community/profile") or {}
    details = {
        "id": f"github:{repo}",
        "description": info.get("description"),
        "homepage": info.get("homepage") or None,
        "topics": info.get("topics", []),
        "license": (info.get("license") or {}).get("spdx_id"),
        "visibility": info.get("visibility"),
        "default_branch": info.get("default_branch"),
        "is_fork": info.get("fork"),
        "is_archived": info.get("archived"),
        "created_at": info.get("created_at"),
        "pushed_at": info.get("pushed_at"),
        "updated_at": info.get("updated_at"),
        "size_kb": info.get("size"),
        "language": info.get("language"),
        "languages": share(github(f"/repos/{repo}/languages") or {}),
        "counts": {
            "stars": info.get("stargazers_count"),
            "forks": info.get("forks_count"),
            "watchers": info.get("subscribers_count"),
            "open_issues_and_prs": info.get("open_issues_count"),
            "open_pull_requests": fetch_total(f"{api}/pulls?state=open&per_page=1", headers),
            "pull_requests": fetch_total(f"{api}/pulls?state=all&per_page=1", headers),
            "commits": fetch_total(f"{api}/commits?per_page=1", headers),
            "contributors": fetch_total(f"{api}/contributors?per_page=1&anon=1", headers),
            "branches": fetch_total(f"{api}/branches?per_page=1", headers),
            "tags": fetch_total(f"{api}/tags?per_page=1", headers),
            "releases": len(releases),
        },
        "community_health_percent": community.get("health_percentage"),
        "releases": [
            {
                "tag": r.get("tag_name"),
                "published_at": r.get("published_at"),
                "prerelease": r.get("prerelease"),
                "downloads": sum(a["download_count"] for a in r.get("assets", [])),
                "assets": [{"name": a["name"], "size": a["size"], "downloads": a["download_count"]} for a in r.get("assets", [])],
            }
            for r in releases
        ],
    }
    if history is not None:
        details["traffic"] = {
            "since_at": since_at(history),
            "referrers": github(f"/repos/{repo}/traffic/popular/referrers") or [],
            "paths": github(f"/repos/{repo}/traffic/popular/paths") or [],
        }
    return details


def collect_github(project: dict, previous: dict) -> dict:
    repo = project["repo"]
    info = github(f"/repos/{repo}") or {}
    release = github(f"/repos/{repo}/releases/latest") or {}
    issues = github(f"/search/issues?q=repo:{repo}+is:issue+is:open") or {}
    releases = github(f"/repos/{repo}/releases?per_page=100") or []
    hosted = {
        "version": release.get("tag_name"),
        "stars": info.get("stargazers_count"),
        "forks": info.get("forks_count"),
        "open_issues": issues.get("total_count"),
        "last_activity": info.get("pushed_at"),
        "last_release_at": release.get("published_at"),
        "release_downloads": sum(a["download_count"] for r in releases for a in r.get("assets", [])),
        # People with a GitHub account other than the owner: the several addresses of the owner count for nothing.
        "external_contributors": sum(
            1 for c in (github(f"/repos/{repo}/contributors?per_page=100") or []) if c.get("type") == "User" and c.get("login") != repo.split("/")[0]
        ),
        "url": f"https://github.com/{repo}",
    }
    # The traffic is read for public repositories only, so a private one can never reach a public card.
    # Without a token the history of the previous run is kept as it is.
    history = previous.get("traffic") or {"views": {}, "clones": {}}
    visitors = previous.get("visitors_14d")
    if GH_TOKEN and info.get("private") is False:
        history, views = read_traffic(repo, history)
        visitors = (views or {}).get("uniques", visitors)
    if info.get("private") is not True and (history.get("views") or history.get("clones")):
        hosted |= {
            "traffic": history,
            "visitors_14d": visitors,
            "views_total": sum(d["count"] for d in history.get("views", {}).values()) or None,
            "clones_total": sum(d["count"] for d in history.get("clones", {}).values()) or None,
            "traffic_since_at": since_at(history),
        }
    # Nothing of a private repository goes into the JSON file, which is public.
    if info.get("private") is not True:
        hosted["details"] = github_details(repo, info, releases, history if GH_TOKEN else None)
    return hosted


def gitlab_details(encoded: str, info: dict) -> dict:
    """Everything useful that GitLab says about a project, for the JSON file."""
    api = f"https://gitlab.com/api/v4/projects/{encoded}"
    headers = {"PRIVATE-TOKEN": GITLAB_TOKEN} if GITLAB_TOKEN else {}
    statistics = (gitlab(f"/projects/{encoded}?statistics=true") or {}).get("statistics") or {}
    pipeline = (gitlab(f"/projects/{encoded}/pipelines?ref={info.get('default_branch', 'main')}&per_page=1") or [{}]) or [{}]
    return {
        "id": f"gitlab:{info.get('path_with_namespace')}",
        "description": info.get("description"),
        "topics": info.get("topics", []),
        "visibility": info.get("visibility"),
        "default_branch": info.get("default_branch"),
        "is_fork": bool(info.get("forked_from_project")),
        "is_archived": info.get("archived"),
        "created_at": info.get("created_at"),
        "pushed_at": info.get("last_activity_at"),
        "size_kb": round(statistics["repository_size"] / 1024) if statistics.get("repository_size") is not None else None,
        "languages": {name: {"percent": percent} for name, percent in (gitlab(f"/projects/{encoded}/languages") or {}).items()},
        "counts": {
            "stars": info.get("star_count"),
            "forks": info.get("forks_count"),
            "open_issues": info.get("open_issues_count"),
            "open_merge_requests": fetch_total(f"{api}/merge_requests?state=opened&per_page=1", headers),
            "merged_merge_requests": fetch_total(f"{api}/merge_requests?state=merged&per_page=1", headers),
            "commits": statistics.get("commit_count"),
            "contributors": fetch_total(f"{api}/repository/contributors?per_page=1", headers),
            "branches": fetch_total(f"{api}/repository/branches?per_page=1", headers),
            "tags": fetch_total(f"{api}/repository/tags?per_page=1", headers),
            "releases": fetch_total(f"{api}/releases?per_page=1", headers),
        },
        "last_pipeline": {"status": pipeline[0].get("status"), "at": pipeline[0].get("updated_at")} if pipeline and pipeline[0] else None,
    }


def collect_gitlab(project: dict) -> dict:
    encoded = urllib.parse.quote(project["repo"], safe="")
    info = gitlab(f"/projects/{encoded}") or {}
    releases = gitlab(f"/projects/{encoded}/releases?per_page=1") or [{}]
    tags = gitlab(f"/projects/{encoded}/repository/tags?per_page=1") or [{}]
    return {
        "version": (releases or [{}])[0].get("tag_name") or (tags or [{}])[0].get("name"),
        "stars": info.get("star_count"),
        "forks": info.get("forks_count"),
        "open_issues": info.get("open_issues_count"),
        "last_activity": info.get("last_activity_at"),
        "last_release_at": (releases or [{}])[0].get("released_at"),
        "url": f"https://gitlab.com/{project['repo']}",
        "details": gitlab_details(encoded, info) if info.get("visibility") == "public" else None,
    }


def collect(project: dict, previous: dict) -> dict:
    hosted = collect_github(project, previous) if project["platform"] == "github" else collect_gitlab(project)
    package = registry(project.get("registry"))
    return {**project, **hosted, "package": package, "version": package.get("version") or hosted.get("version")}


STYLE = """
  .card { fill:#ffffff; stroke:#d0d7de; }
  .tile { fill:#f6f8fa; }
  text { font-family: -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif; fill:#1f2328; }
  .title { font-size:15px; font-weight:600; }
  .value { font-size:20px; font-weight:700; }
  .label, .note { fill:#656d76; }
  .label { font-size:11.5px; }
  .note  { font-size:11px; }
  .icon { fill:#1f2328; }
  .frame { fill:#ffffff; stroke:#d0d7de; }
  .bar { fill:#000091; }
  @media (prefers-color-scheme: dark) {
    .card { fill:#0d1117; stroke:#30363d; }
    .tile { fill:#161b22; }
    text { fill:#e6edf3; }
    .label, .note { fill:#8b949e; }
    .icon { fill:#e6edf3; }
    .frame { stroke:#30363d; }
    .bar { fill:#8585f6; }
  }
"""


def tile(x: int, y: int, value: str, label: str) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="120" height="62" rx="8" class="tile"/>'
        f'<text x="{x + 14}" y="{y + 30}" class="value">{escape(value)}</text>'
        f'<text x="{x + 14}" y="{y + 48}" class="label">{escape(label)}</text>'
    )


def tiles(x: int, items: list[tuple[str, str]]) -> str:
    return "".join(
        tile(x + 20 + (index % 3) * 128, 62 + (index // 3) * 70, value, label) for index, (value, label) in enumerate(items)
    )


def panel(x: int, title: str, icon: str, body: str, note: str, height: int = 238) -> str:
    return (
        f'<rect x="{x}" y="0" width="408" height="{height}" rx="10" class="card"/>'
        f'{icon}<text x="{x + 56}" y="35" class="title">{escape(title)}</text>{body}'
        + (f'<text x="{x + 20}" y="{height - 20}" class="note">{escape(note)}</text>' if note else "")
    )


def svg(width: int, label: str, content: str, height: int = 238) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-1 -1 {width + 2} {height + 2}" width="{width + 2}" height="{height + 2}" '
        f'role="img" aria-label="{escape(label)}"><style>{STYLE}</style>{content}</svg>\n'
    )


def platform_icon(platform: str, x: int = 20) -> str:
    path = GITHUB_ICON if platform == "github" else GITLAB_ICON
    return f'<g transform="translate({x} 16) scale(1.08)"><path class="icon" d="{path}"/></g>'


MAX_TILES = 6
# Colors of GitHub for the usual languages.
LANGUAGE_COLORS = {
    "PHP": "#4f5d95", "JavaScript": "#f1e05a", "TypeScript": "#3178c6", "Vue": "#41b883", "SCSS": "#c6538c",
    "CSS": "#563d7c", "HTML": "#e34c26", "Shell": "#89e051", "Makefile": "#427819", "Blade": "#f7523f",
    "Dockerfile": "#384d54", "Python": "#3572a5",
}
FALLBACK_LANGUAGE_COLOR = "#8b949e"


def project_tiles(data: dict) -> list[tuple[str, str]]:
    """The figures worth showing, in order of interest: a figure that says nothing (zero, unknown) is left out."""
    package = data["package"]
    details = data.get("details") or {}
    counts = details.get("counts") or {}
    monthly = package.get("monthly") or 0
    stars = data.get("stars") if data.get("stars") is not None else package.get("stars")
    created = details.get("created_at")
    old = created and (datetime.now(timezone.utc) - datetime.fromisoformat(created.replace("Z", "+00:00"))).days > 3 * 365
    candidates = [
        (data.get("version"), "latest version"),
        (month_year(data["last_release_at"]) if data.get("last_release_at") else None, "last release"),
        (compact(monthly) if monthly > 0 else None, "downloads / month"),
        (compact(package["total"]) if (package.get("total") or 0) > monthly else None, package.get("total_label", "downloads (total)")),
        (compact(data["release_downloads"]) if (data.get("release_downloads") or 0) > 0 else None, "release downloads"),
        (compact(package["details"]["dependents"]) if ((package.get("details") or {}).get("dependents") or 0) > 0 else None, "dependents"),
        (compact(stars) if (stars or 0) > 0 else None, "stars"),
        (compact(data["forks"]) if (data.get("forks") or 0) > 0 else None, "forks"),
        (compact(data["external_contributors"]) if (data.get("external_contributors") or 0) > 0 else None, "contributors"),
        (compact(data["open_issues"]) if (data.get("open_issues") or 0) > 0 else None, "open issues"),
        (compact(data["visitors_14d"]) if (data.get("visitors_14d") or 0) >= 10 else None, "visitors / 14 days"),
        (created[:4] if old else None, "since"),
    ]
    return [(value, label) for value, label in candidates if value][:MAX_TILES]


def languages_bar(x: int, y: int, languages: dict) -> str:
    """A thin bar of the main languages, with the three first named under it."""
    top = [(name, entry["percent"]) for name, entry in list(languages.items())[:3]]
    rest = round(100 - sum(percent for _, percent in top), 1)
    segments = top + ([("Other", rest)] if rest >= 0.5 else [])
    width, offset, bar, legend = 368, 0.0, "", ""
    for name, percent in segments:
        color = LANGUAGE_COLORS.get(name, FALLBACK_LANGUAGE_COLOR)
        size = width * percent / 100
        bar += f'<rect x="{x + offset:.1f}" y="{y}" width="{size:.1f}" height="6" fill="{color}"/>'
        offset += size
    cursor = x
    for name, percent in top:
        label = f"{name} {round(percent, 1):g}%"
        legend += (
            f'<circle cx="{cursor + 4}" cy="{y + 18}" r="4" fill="{LANGUAGE_COLORS.get(name, FALLBACK_LANGUAGE_COLOR)}"/>'
            f'<text x="{cursor + 14}" y="{y + 22}" class="note">{escape(label)}</text>'
        )
        cursor += 34 + len(label) * 6.3
    return f'<clipPath id="bar"><rect x="{x}" y="{y}" width="{width}" height="6" rx="3"/></clipPath><g clip-path="url(#bar)">{bar}</g>{legend}'


def project_card(data: dict) -> str:
    items = project_tiles(data)
    rows = max(1, -(-len(items) // 3))
    languages = (data.get("details") or {}).get("languages")
    package = data["package"]
    if languages:
        height = 104 + 70 * rows
        body = tiles(0, items) + languages_bar(20, 68 + 70 * rows, languages)
        note = ""
    else:
        height = 98 + 70 * rows
        body = tiles(0, items)
        host = "GitHub" if data["platform"] == "github" else "GitLab"
        names = {"packagist": "Packagist", "npm": "npm"}
        note = host + (f" and {names[data['registry'].split(':')[0]]}" if package else "") + ", public figures."
    return svg(408, f"Statistics of {data['name']}", panel(0, data["name"], platform_icon(data["platform"]), body, note, height), height)


def totals(collected: list[dict]) -> dict:
    return {
        "projects": len(collected),
        "packages": sum(1 for p in collected if p["package"]),
        "downloads_month": sum(p["package"].get("monthly") or 0 for p in collected),
        "downloads_total": sum(p["package"].get("total") or 0 for p in collected if p["package"].get("total_label") is None),
        "release_downloads": sum(p.get("release_downloads") or 0 for p in collected),
        "stars": sum(p.get("stars") or 0 for p in collected),
        "open_issues": sum(p.get("open_issues") or 0 for p in collected),
    }


def detailed_totals(collected: list[dict]) -> dict:
    """The figures of every project added up, with the languages of the whole page."""
    sums: dict[str, int] = {}
    # Bytes of code on GitHub, percentages on GitLab: a page built from one platform keeps the same unit.
    languages: dict[str, float] = {}
    any_bytes = False
    for project in collected:
        details = project.get("details") or {}
        for key, value in (details.get("counts") or {}).items():
            sums[key] = sums.get(key, 0) + (value or 0)
        for name, entry in (details.get("languages") or {}).items():
            any_bytes = any_bytes or "bytes" in entry
            languages[name] = languages.get(name, 0) + (entry.get("bytes") or entry.get("percent") or 0)
    return {
        "projects": len(collected),
        "counts": sums,
        "languages": share(languages) if any_bytes else {name: {"percent": entry["percent"]} for name, entry in share(languages).items()},
        "views": sum(p.get("views_total") or 0 for p in collected),
        "clones": sum(p.get("clones_total") or 0 for p in collected),
        "downloads_month": sum((p["package"] or {}).get("monthly") or 0 for p in collected),
        "release_downloads": sum(p.get("release_downloads") or 0 for p in collected),
    }


def overview_card(collected: list[dict], title: str, platform: str) -> str:
    sums = totals(collected)
    items = [
        (str(sums["projects"]), "projects"),
        (str(sums["packages"]), "published packages"),
        (compact(sums["downloads_month"]), "downloads / month"),
        (compact(sums["downloads_total"] + sums["release_downloads"]), "downloads (total)"),
        (compact(sums["stars"]), "stars"),
        (compact(sums["open_issues"]), "open issues"),
    ]
    return svg(408, title, panel(0, title, platform_icon(platform), tiles(0, items), "Sum of the cards of this page."))


def collect_data_gouv(dataset_id: str) -> dict:
    dataset = fetch(f"https://www.data.gouv.fr/api/1/datasets/{dataset_id}/") or {}
    metrics = dataset.get("metrics", {})
    quality = dataset.get("quality", {}).get("score")
    views_by_format: dict[str, int] = {}
    for resource in dataset.get("resources", []):
        name = resource.get("format")
        if name:
            views_by_format[name] = views_by_format.get(name, 0) + (resource.get("metrics", {}).get("views") or 0)
    return {
        "views_by_format": views_by_format,
        "downloads": metrics.get("resources_downloads"),
        "views": metrics.get("views"),
        "reuses": metrics.get("reuses"),
        "discussions": metrics.get("discussions_open"),
        "quality": None if quality is None else round(quality * 100),
        "last_update": dataset.get("last_update"),
    }


def collect_statistics(repo: str) -> dict | None:
    """Reads `statistics.json`, attached to the latest release of the builder by `make export`."""
    release = github(f"/repos/{repo}/releases/latest") or {}
    for asset in release.get("assets", []):
        if asset["name"] == "statistics.json":
            return fetch(asset["browser_download_url"])
    return None


def collect_dataset_github(repo: str, previous: dict) -> tuple[dict, dict]:
    history, views = read_traffic(repo, previous.get("traffic") or {"views": {}, "clones": {}})
    info = github(f"/repos/{repo}") or {}
    releases = github(f"/repos/{repo}/releases?per_page=100") or []
    issues = github(f"/search/issues?q=repo:{repo}+is:issue+is:open") or {}
    commits = github(f"/repos/{repo}/commits?per_page=1") or [{}]
    stats = {
        "release_downloads": sum(a["download_count"] for r in releases for a in r.get("assets", [])),
        "stars": info.get("stargazers_count"),
        "forks": info.get("forks_count"),
        "visitors_14d": (views or {}).get("uniques", previous.get("github", {}).get("visitors_14d")),
        "views_total": sum(d["count"] for d in history.get("views", {}).values()) or None,
        "clones_total": sum(d["count"] for d in history.get("clones", {}).values()) or None,
        "traffic_since_at": since_at(history),
        "open_issues": issues.get("total_count"),
        "last_commit": commits[0].get("commit", {}).get("committer", {}).get("date"),
        "details": github_details(repo, info, releases, history) if info.get("private") is False else None,
    }
    return stats, history


def worth_showing(candidates: list[tuple[str | None, str]]) -> list[tuple[str, str]]:
    """Keeps the figures that say something, at most MAX_TILES."""
    return [(value, label) for value, label in candidates if value][:MAX_TILES]


def single_card(title: str, label: str, icon: str, items: list[tuple[str, str]], note: str = "") -> str:
    rows = max(1, -(-len(items) // 3))
    height = (98 if note else 70) + 70 * rows
    return svg(408, label, panel(0, title, icon, tiles(0, items), note, height), height)


def data_gouv_card(data_gouv: dict, logo: bytes | None) -> str:
    logo_icon = (
        '<rect x="19.5" y="15.5" width="27" height="27" rx="6.5" class="frame"/>'
        '<clipPath id="logo"><rect x="20" y="16" width="26" height="26" rx="6"/></clipPath>'
        f'<image x="20" y="16" width="26" height="26" clip-path="url(#logo)" href="{data_uri(logo, "image/svg+xml")}"/>'
        if logo
        else ""
    )
    quality = data_gouv["quality"]
    items = worth_showing([
        (compact(data_gouv["downloads"]) if (data_gouv["downloads"] or 0) > 0 else None, "downloads"),
        (compact(data_gouv["views"]) if (data_gouv["views"] or 0) > 0 else None, "views"),
        (compact(data_gouv["reuses"]) if (data_gouv["reuses"] or 0) > 0 else None, "reuses"),
        (compact(data_gouv["discussions"]) if (data_gouv["discussions"] or 0) > 0 else None, "open discussions"),
        (None if quality is None else f"{quality}%", "metadata quality"),
        (month_year(data_gouv["last_update"]) if data_gouv["last_update"] else None, "last update"),
    ])
    return single_card("data.gouv.fr", "Statistics of the dataset on data.gouv.fr", logo_icon, items)


def github_card(hub: dict) -> str:
    items = worth_showing([
        (compact(hub["release_downloads"]) if (hub["release_downloads"] or 0) > 0 else None, "release downloads"),
        (compact(hub["stars"]) if (hub.get("stars") or 0) > 0 else None, "stars"),
        (compact(hub["forks"]) if (hub.get("forks") or 0) > 0 else None, "forks"),
        (compact(hub["visitors_14d"]) if (hub["visitors_14d"] or 0) >= 10 else None, "visitors (14 days)"),
        (compact(hub["views_total"]) if (hub["views_total"] or 0) > 0 else None, "repository views"),
        (compact(hub["open_issues"]) if (hub["open_issues"] or 0) > 0 else None, "open issues"),
        (month_year(hub["last_commit"]) if hub["last_commit"] else None, "last commit"),
    ])
    return single_card("GitHub", "Statistics of the dataset repository on GitHub", platform_icon("github"), items)


def content_card(dataset: dict | None) -> str:
    box_icon = (
        '<g transform="translate(20 15) scale(1.1)"><path class="icon" '
        'd="M12 1 3 5.5v9L12 19l9-4.5v-9zm0 2.2 5.9 2.9L12 9 6.1 6.1zM5 7.7l6 3v6.5l-6-3zm14 0v6.5l-6 3v-6.5z"/></g>'
    )
    labels = ("regions", "departments", "communes", "postal entries", "code successions", "BAN points")
    if dataset:
        by_source = dataset.get("cities_by_source", {})
        total = sum(by_source.values()) or 1
        items = [
            (compact(dataset.get("regions")), labels[0]),
            (compact(dataset.get("departments")), labels[1]),
            (compact(dataset.get("communes")), labels[2]),
            (compact(dataset.get("cities")), labels[3]),
            (compact(dataset.get("successions")), labels[4]),
            (f"{round(100 * by_source.get('ban', 0) / total)}%", labels[5]),
        ]
        note = f"INSEE COG {dataset.get('cog_vintage') or '?'}, La Poste {dataset.get('laposte_version') or '?'}."
    else:
        items = [("–", label) for label in labels]
        note = "Figures appear with the next release."
    return single_card("Dataset", "Content of the dataset", box_icon, items, note)


def write_dataset(config: dict, previous: dict) -> dict:
    data_gouv = collect_data_gouv(config["data_gouv"])
    hub, traffic = collect_dataset_github(config["repo"], previous)
    logo = fetch(DATA_GOUV_LOGO_URL, binary=True)
    dataset = collect_statistics(config["repo"])
    slug = config["slug"]
    (OUT_DIR / f"{slug}-datagouv.svg").write_text(data_gouv_card(data_gouv, logo), encoding="utf-8")
    (OUT_DIR / f"{slug}-github.svg").write_text(github_card(hub), encoding="utf-8")
    (OUT_DIR / f"{slug}-dataset.svg").write_text(content_card(dataset), encoding="utf-8")
    # The two wide cards of the former layout.
    (OUT_DIR / f"{slug}-usage.svg").unlink(missing_ok=True)
    return {"data_gouv": data_gouv, "github": hub, "dataset": dataset, "traffic": traffic}


def main() -> None:
    config = json.loads(PROJECTS.read_text())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stats_file = OUT_DIR / f"stats-{PLATFORM}.json"
    previous = json.loads(stats_file.read_text()) if stats_file.exists() else {}

    wanted = [p for p in config["projects"] if PLATFORM in ("all", p["platform"])]
    collected = [collect(project, previous.get("projects", {}).get(project["slug"], {})) for project in wanted]
    for data in collected:
        (OUT_DIR / f"{data['slug']}.svg").write_text(project_card(data), encoding="utf-8")

    title = {"github": "GitHub projects", "gitlab": "GitLab projects"}.get(PLATFORM, "All projects")
    overview = "github" if PLATFORM != "gitlab" else "gitlab"
    (OUT_DIR / f"overview-{PLATFORM}.svg").write_text(overview_card(collected, title, overview), encoding="utf-8")

    result = {"dataset": write_dataset(config["dataset"], previous.get("dataset_data", {}))} if PLATFORM in ("all", "github") else {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history = previous.get("history", {})
    history[today] = totals(collected)
    stats_file.write_text(
        json.dumps(
            {
                "updated_at": today,
                "totals": totals(collected),
                "totals_detailed": detailed_totals(collected),
                "projects": {p["slug"]: {k: p.get(k) for k in ("version", "stars", "forks", "open_issues", "last_activity", "package", "visitors_14d", "views_total", "clones_total", "traffic_since_at", "release_downloads", "last_release_at", "external_contributors", "traffic", "details")} for p in collected},
                "dataset_data": result.get("dataset", previous.get("dataset_data", {})),
                "history": history,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(collected)} cards written to {OUT_DIR}")


if __name__ == "__main__":
    main()
