#!/usr/bin/env python3
"""Builds one statistics card per project, an overview card and the history in a JSON file.

The projects are listed in `.github/projects.json`. For each one, the script reads the hosting platform (GitHub
or GitLab, public data) and the package registry (Packagist or npm), then writes `<slug>.svg` in OUT_DIR. The
dataset French-Postal-Code gets its own two wide cards (usage and content) from data.gouv.fr and its releases.

Environment:
    PLATFORM         github, gitlab or all (default): the projects to build
    OUT_DIR          directory of the cards and of `stats.json`, which keeps the history (default assets/projects)
    GH_TOKEN         GitHub token. Reading the traffic of the dataset repository needs push access
    GITLAB_API_TOKEN GitLab token that can read the group (open issues are hidden without one)
    PROJECTS         path of the project list (default .github/projects.json)
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
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


def fetch(url: str, headers: dict | None = None, *, binary: bool = False):
    """Returns the decoded JSON (or the raw bytes) of a URL, None when it cannot be read."""
    request = urllib.request.Request(url, headers={"User-Agent": "project-cards", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
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
        }
    quoted = urllib.parse.quote(name, safe="@")
    latest = fetch(f"https://registry.npmjs.org/{quoted.replace('/', '%2f')}/latest") or {}
    month = fetch(f"https://api.npmjs.org/downloads/point/last-month/{quoted}") or {}
    year = fetch(f"https://api.npmjs.org/downloads/point/last-year/{quoted}") or {}
    return {"version": latest.get("version"), "monthly": month.get("downloads"), "total": year.get("downloads"), "total_label": "downloads / year"}


def collect_github(project: dict) -> dict:
    repo = project["repo"]
    info = github(f"/repos/{repo}") or {}
    release = github(f"/repos/{repo}/releases/latest") or {}
    issues = github(f"/search/issues?q=repo:{repo}+is:issue+is:open") or {}
    releases = github(f"/repos/{repo}/releases?per_page=100") or []
    return {
        "version": release.get("tag_name"),
        "stars": info.get("stargazers_count"),
        "forks": info.get("forks_count"),
        "open_issues": issues.get("total_count"),
        "last_activity": info.get("pushed_at"),
        "release_downloads": sum(a["download_count"] for r in releases for a in r.get("assets", [])),
        "url": f"https://github.com/{repo}",
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
        "url": f"https://gitlab.com/{project['repo']}",
    }


def collect(project: dict) -> dict:
    hosted = collect_github(project) if project["platform"] == "github" else collect_gitlab(project)
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


def panel(x: int, title: str, icon: str, body: str, note: str) -> str:
    return (
        f'<rect x="{x}" y="0" width="408" height="238" rx="10" class="card"/>'
        f'{icon}<text x="{x + 56}" y="35" class="title">{escape(title)}</text>{body}'
        f'<text x="{x + 20}" y="218" class="note">{escape(note)}</text>'
    )


def svg(width: int, label: str, content: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-1 -1 {width + 2} 240" width="{width + 2}" height="240" '
        f'role="img" aria-label="{escape(label)}"><style>{STYLE}</style>{content}</svg>\n'
    )


def platform_icon(platform: str, x: int = 20) -> str:
    path = GITHUB_ICON if platform == "github" else GITLAB_ICON
    return f'<g transform="translate({x} 16) scale(1.08)"><path class="icon" d="{path}"/></g>'


def project_card(data: dict) -> str:
    package = data["package"]
    if package:
        items = [
            (data.get("version") or "–", "latest version"),
            (compact(package.get("monthly")), "downloads / month"),
            (compact(package.get("total")), package.get("total_label", "downloads (total)")),
        ]
    else:
        items = [
            (data.get("version") or "–", "latest version"),
            (compact(data.get("release_downloads")), "release downloads"),
            (compact(data.get("forks")), "forks"),
        ]
    stars = data.get("stars") if data.get("stars") is not None else package.get("stars")
    items += [
        (compact(stars), "stars"),
        (compact(data.get("open_issues")), "open issues"),
        (month_year(data.get("last_activity")), "last activity"),
    ]
    host = "GitHub" if data["platform"] == "github" else "GitLab"
    names = {"packagist": "Packagist", "npm": "npm"}
    note = host + (f" and {names[data['registry'].split(':')[0]]}" if package else "") + ", public figures."
    return svg(408, f"Statistics of {data['name']}", panel(0, data["name"], platform_icon(data["platform"]), tiles(0, items), note))


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
    formats: dict[str, int] = {}
    for resource in dataset.get("resources", []):
        match = re.match(r"\[(\w+)", resource.get("title", ""))
        if match:
            formats[match.group(1)] = formats.get(match.group(1), 0) + (resource.get("metrics", {}).get("views") or 0)
    return {
        "formats": formats,
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


def merge_traffic(history: dict, kind: str, payload: dict | None) -> None:
    """Merges the daily values of a traffic endpoint into the history, one entry per day."""
    if not payload:
        return
    days = history.setdefault(kind, {})
    for entry in payload.get(kind, []):
        days[entry["timestamp"][:10]] = {"count": entry["count"], "uniques": entry["uniques"]}


def collect_dataset_github(repo: str, previous: dict) -> tuple[dict, dict]:
    history = previous.get("traffic", {"views": {}, "clones": {}})
    views = github(f"/repos/{repo}/traffic/views")
    merge_traffic(history, "views", views)
    merge_traffic(history, "clones", github(f"/repos/{repo}/traffic/clones"))
    releases = github(f"/repos/{repo}/releases?per_page=100") or []
    issues = github(f"/search/issues?q=repo:{repo}+is:issue+is:open") or {}
    commits = github(f"/repos/{repo}/commits?per_page=1") or [{}]
    stats = {
        "release_downloads": sum(a["download_count"] for r in releases for a in r.get("assets", [])),
        "visitors_14d": (views or {}).get("uniques", previous.get("github", {}).get("visitors_14d")),
        "views_total": sum(d["count"] for d in history.get("views", {}).values()) or None,
        "clones_total": sum(d["count"] for d in history.get("clones", {}).values()) or None,
        "tracked_since": min(history.get("views", {}) | history.get("clones", {}), default=None),
        "open_issues": issues.get("total_count"),
        "last_commit": commits[0].get("commit", {}).get("committer", {}).get("date"),
    }
    return stats, history


def usage_card(data_gouv: dict, hub: dict, logo: bytes | None) -> str:
    logo_icon = (
        '<rect x="19.5" y="15.5" width="27" height="27" rx="6.5" class="frame"/>'
        '<clipPath id="logo"><rect x="20" y="16" width="26" height="26" rx="6"/></clipPath>'
        f'<image x="20" y="16" width="26" height="26" clip-path="url(#logo)" href="{data_uri(logo, "image/svg+xml")}"/>'
        if logo
        else ""
    )
    left = panel(
        0,
        "data.gouv.fr",
        logo_icon,
        tiles(0, [
            (compact(data_gouv["downloads"]), "downloads"),
            (compact(data_gouv["views"]), "views"),
            (compact(data_gouv["reuses"]), "reuses"),
            (compact(data_gouv["discussions"]), "open discussions"),
            ("–" if data_gouv["quality"] is None else f'{data_gouv["quality"]}%', "metadata quality"),
            (month_year(data_gouv["last_update"]), "last update"),
        ]),
        "Public figures of the dataset page.",
    )
    since = month_year(hub["tracked_since"]) if hub["tracked_since"] else None
    right = panel(
        428,
        "GitHub",
        platform_icon("github", 448),
        tiles(428, [
            (compact(hub["release_downloads"]), "release downloads"),
            (compact(hub["views_total"]), "repository views"),
            (compact(hub["clones_total"]), "git clones"),
            (compact(hub["open_issues"]), "open issues"),
            (compact(hub["visitors_14d"]), "visitors (14 days)"),
            (month_year(hub["last_commit"]), "last commit"),
        ]),
        f"Views and clones counted since {since}." if since else "Views and clones need a token with push access.",
    )
    return svg(836, "Usage statistics", left + right)


def content_card(dataset: dict | None, formats: dict[str, int]) -> str:
    box_icon = (
        '<g transform="translate(20 15) scale(1.1)"><path class="icon" '
        'd="M12 1 3 5.5v9L12 19l9-4.5v-9zm0 2.2 5.9 2.9L12 9 6.1 6.1zM5 7.7l6 3v6.5l-6-3zm14 0v6.5l-6 3v-6.5z"/></g>'
    )
    bars_icon = '<g transform="translate(448 15) scale(1.1)"><path class="icon" d="M4 20V10h4v10zm6 0V4h4v16zm6 0v-7h4v7z"/></g>'
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

    peak = max(formats.values(), default=0) or 1
    rows = []
    for index, (name, count) in enumerate(sorted(formats.items(), key=lambda item: -item[1])[:4]):
        y = 66 + index * 36
        width = max(4, round(250 * count / peak))
        rows.append(
            f'<text x="448" y="{y + 14}" class="label">{escape(name)}</text>'
            f'<rect x="500" y="{y}" width="{width}" height="18" rx="4" class="bar"/>'
            f'<text x="{506 + width}" y="{y + 14}" class="label">{compact(count)}</text>'
        )
    left = panel(0, "Dataset", box_icon, tiles(0, items), note)
    right = panel(428, "Downloads by format", bars_icon, "".join(rows), "All archives of a format together, from data.gouv.fr.")
    return svg(836, "Dataset content and downloads by format", left + right)


def write_dataset(config: dict, previous: dict) -> dict:
    data_gouv = collect_data_gouv(config["data_gouv"])
    hub, traffic = collect_dataset_github(config["repo"], previous)
    logo = fetch(DATA_GOUV_LOGO_URL, binary=True)
    dataset = collect_statistics(config["repo"])
    slug = config["slug"]
    (OUT_DIR / f"{slug}-usage.svg").write_text(usage_card(data_gouv, hub, logo), encoding="utf-8")
    (OUT_DIR / f"{slug}-dataset.svg").write_text(content_card(dataset, data_gouv["formats"]), encoding="utf-8")
    return {"data_gouv": data_gouv, "github": hub, "dataset": dataset, "traffic": traffic}


def main() -> None:
    config = json.loads(PROJECTS.read_text())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stats_file = OUT_DIR / f"stats-{PLATFORM}.json"
    previous = json.loads(stats_file.read_text()) if stats_file.exists() else {}

    wanted = [p for p in config["projects"] if PLATFORM in ("all", p["platform"])]
    collected = [collect(project) for project in wanted]
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
                "projects": {p["slug"]: {k: p.get(k) for k in ("version", "stars", "forks", "open_issues", "last_activity", "package")} for p in collected},
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
