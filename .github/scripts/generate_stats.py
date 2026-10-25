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

# Accent of the figures: the GitHub blue. The browsers without color-mix() get the two fixed variants.
ACCENT = "#0969da"
ACCENT_LIGHT = "#0969da"
ACCENT_DARK = "#58a6ff"
# GitHub language colours.
LANG_COLORS = {
    "PHP": "#4F5D95",
    "TypeScript": "#3178c6",
    "JavaScript": "#f1e05a",
    "Vue": "#41b883",
    "Shell": "#89e051",
    "SCSS": "#c6538c",
    "CSS": "#563d7c",
    "HTML": "#e34c26",
    "Blade": "#f7523f",
    "Python": "#3572A5",
    "Dockerfile": "#384d54",
    "Makefile": "#427819",
}
FALLBACK_COLOR = "#8c8c8c"

# Both cards of a profile have the same size, so they sit side by side.
CARD_WIDTH = 420
CARD_HEIGHT = 112
PADDING = 16


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


# Languages named on the card, the others go in a remainder bucket.
TOP_LANGUAGES = 4
# Name of the remainder bucket in stats.json, read by stanislas-poisson.fr.
REST_NAME = "Autres"
# Label of the same bucket on the cards.
REST_LABEL = "Other"


def bucket_top_n_with_rest(totals, top_n=4):
    """Top N languages by byte weight, plus a bucket for the rest."""
    top = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    total = sum(v for _, v in top) or 1
    result, running = [], 0.0
    for lang, val in top[:top_n]:
        pct = round(100 * val / total, 1)
        result.append({"name": lang, "pct": pct})
        running += pct
    rest = round(100 - running, 1)
    if rest > 0.05:
        result.append({"name": REST_NAME, "pct": rest})
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


# Colours of the cards: they follow the theme of the reader and are derived from two colours, the
# background and the ink, so a change of theme keeps the same contrast. Same approach as the other
# SVGs of the project (light-dark(), color-mix(), prefers-contrast, forced-colors). The browsers
# that do not know color-mix() get the fixed palette of the first two blocks.
# Contrast (WCAG 2.1): text 7:1, figures (large text) 4.5:1, more with prefers-contrast.
# The page can set --card-bg and --card-ink to match a custom theme.
CARD_STYLE = """
    svg {{
      --_bg: #ffffff; --_ink: #1f2328; --_tile: #e2e6ea; --_muted: #59636e;
      --_line: #d0d7de; --_accent: {accent_light};
    }}
    @media (prefers-color-scheme: dark) {{
      svg {{
        --_bg: #0d1117; --_ink: #e6edf3; --_tile: #262c34; --_muted: #9198a1;
        --_line: #30363d; --_accent: {accent_dark};
      }}
    }}
    @supports (color: light-dark(#fff, #000)) {{
      svg {{
        color-scheme: light dark;
        --_bg: var(--card-bg, light-dark(#ffffff, #0d1117));
        --_ink: var(--card-ink, light-dark(#1f2328, #e6edf3));
      }}
    }}
    @supports (color: color-mix(in oklab, red, blue)) {{
      svg {{
        --_tile: color-mix(in oklab, var(--_ink) 12%, var(--_bg));
        --_muted: color-mix(in oklab, var(--_ink) 72%, var(--_bg));
        --_line: color-mix(in oklab, var(--_ink) 15%, var(--_bg));
        --_accent: color-mix(in oklab, var(--_ink) 45%, {accent});
      }}
      @media (prefers-contrast: more) {{
        svg {{
          --_muted: color-mix(in oklab, var(--_ink) 90%, var(--_bg));
          --_line: color-mix(in oklab, var(--_ink) 45%, var(--_bg));
          --_tile: color-mix(in oklab, var(--_ink) 20%, var(--_bg));
          --_accent: color-mix(in oklab, var(--_ink) 70%, {accent});
        }}
      }}
    }}
    .card {{ fill: var(--_bg); stroke: var(--_line); }}
    .tile {{ fill: var(--_tile); }}
    .sep {{ stroke: var(--_bg); stroke-width: 2; }}
    text {{ font-family: -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif; fill: var(--_ink); }}
    .title {{ font-size: 15px; font-weight: 600; }}
    .value {{ font-size: 20px; font-weight: 700; fill: var(--_accent); }}
    .label, .pct {{ font-size: 12px; fill: var(--_muted); }}
    .lang {{ font-size: 12px; font-weight: 600; }}
    /* Forced colours (Windows high contrast): the system palette */
    @media (forced-colors: active) {{
      .card, .tile {{ fill: Canvas; stroke: CanvasText; }}
      .sep {{ stroke: Canvas; }}
      text, .value, .label, .pct {{ fill: CanvasText; }}
    }}
"""


def card_svg(kind, title, description, body, height, generated_at):
    """generated_at is embedded as an SVG comment so the file content always
    changes on every run, even when the underlying numbers are identical -
    otherwise git sees no diff and silently skips committing this file.
    The title and the description are what a screen reader announces."""
    style = CARD_STYLE.format(accent=ACCENT, accent_light=ACCENT_LIGHT, accent_dark=ACCENT_DARK)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" xml:lang="en" lang="en" width="{CARD_WIDTH}" height="{height}" viewBox="0 0 {CARD_WIDTH} {height}" role="img" aria-labelledby="{kind}-title {kind}-desc">
  <!-- generated_at: {generated_at} -->
  <title id="{kind}-title">{title}</title>
  <desc id="{kind}-desc">{description}</desc>
  <style>{style}</style>
  <rect x="0.5" y="0.5" width="{CARD_WIDTH - 1}" height="{height - 1}" rx="8" class="card" />
{body}
</svg>"""


def render_stats_svg(title, rows_data, height, generated_at):
    """One tile per figure, side by side, with the value and its label centred."""
    gap = 8
    inner = CARD_WIDTH - 2 * PADDING
    width = (inner - gap * (len(rows_data) - 1)) / len(rows_data)
    top, tile_height = 44, height - 44 - 12
    middle = top + tile_height / 2
    tiles = []
    for i, (label, value) in enumerate(rows_data):
        x = PADDING + i * (width + gap)
        tiles.append(f"""  <rect x="{x:.1f}" y="{top}" width="{width:.1f}" height="{tile_height}" rx="6" class="tile" />
  <text x="{x + width / 2:.1f}" y="{middle - 2:.1f}" text-anchor="middle" class="value">{value}</text>
  <text x="{x + width / 2:.1f}" y="{middle + 16:.1f}" text-anchor="middle" class="label">{label}</text>""")
    body = f'  <text x="{PADDING}" y="28" class="title">{title}</text>\n' + "\n".join(tiles)
    description = ", ".join(f"{label} {value}" for label, value in rows_data)
    return card_svg("stats", title, description, body, height, generated_at)


def legend_layout(top_langs):
    """Places the legend entries one after the other and goes to the next line
    when one does not fit. Returns the entries with their position, and the
    number of lines."""
    inner = CARD_WIDTH - 2 * PADDING
    x, row, items = 0.0, 0, []
    for entry in top_langs:
        label = REST_LABEL if entry["name"] == REST_NAME else entry["name"]
        pct = f'{entry["pct"]:.1f}%'
        label_width = 7.2 * len(label)
        width = 16 + label_width + 8 + 6.4 * len(pct)
        if x and x + width > inner:
            row, x = row + 1, 0.0
        items.append((entry, label, pct, x, row, label_width))
        x += width + 20
    return items, row + 1


def card_height(top_langs):
    """Height of both cards of a group: they always match, whatever the legend needs."""
    lines = legend_layout(top_langs)[1] if top_langs else 1
    return max(CARD_HEIGHT, 72 + 22 * (lines - 1) + 20)


def render_langs_svg(top_langs, height, generated_at, title="Languages"):
    """One bar split in proportion to each language, with the legend below.
    The names and the percentages carry the information: the colours only
    help, so they never have to be told apart on their own."""
    inner = CARD_WIDTH - 2 * PADDING
    body = [f'  <text x="{PADDING}" y="28" class="title">{title}</text>']
    if not top_langs:
        body.append(f'  <text x="{PADDING}" y="64" class="label">No language data</text>')
        return card_svg("langs", title, "No language data", "\n".join(body), height, generated_at)

    # The segments are clipped to the rounded bar, a gap in the card colour separates them.
    body.append(f'  <clipPath id="bar"><rect x="{PADDING}" y="40" width="{inner}" height="8" rx="4" /></clipPath>')
    body.append('  <g clip-path="url(#bar)">')
    x = float(PADDING)
    for entry in top_langs:
        color = LANG_COLORS.get(entry["name"], FALLBACK_COLOR)
        w = inner * entry["pct"] / 100.0
        body.append(f'    <rect x="{x:.2f}" y="40" width="{w:.2f}" height="8" fill="{color}" />')
        x += w
        if x < PADDING + inner - 1:
            body.append(f'    <line x1="{x:.2f}" y1="40" x2="{x:.2f}" y2="48" class="sep" />')
    body.append("  </g>")

    names = []
    for entry, label, pct, lx, row, label_width in legend_layout(top_langs)[0]:
        color = LANG_COLORS.get(entry["name"], FALLBACK_COLOR)
        names.append(f"{label} {pct}")
        cx, cy = PADDING + lx, 72 + row * 22
        body.append(f'  <circle cx="{cx + 5:.1f}" cy="{cy - 4}" r="5" fill="{color}" />')
        body.append(f'  <text x="{cx + 16:.1f}" y="{cy}" class="lang">{label}</text>')
        body.append(f'  <text x="{cx + 16 + label_width + 8:.1f}" y="{cy}" class="pct">{pct}</text>')
    return card_svg("langs", title, ", ".join(names), "\n".join(body), height, generated_at)


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
    top_langs = bucket_top_n_with_rest(lang_totals, top_n=TOP_LANGUAGES)

    height = card_height(top_langs)
    rows = [
        ("Repos", str(profile["public_repos"])),
        ("Gists", str(profile["public_gists"])),
        ("Stars", format_count(star_total)),
        ("Followers", str(profile["followers"])),
    ]
    with open("assets/stats.svg", "w", encoding="utf-8") as f:
        f.write(render_stats_svg(f"{USERNAME} \u00b7 GitHub", rows, height, generated_at))

    with open("assets/top-langs.svg", "w", encoding="utf-8") as f:
        f.write(render_langs_svg(top_langs, height, generated_at, "Most used languages (public repos)"))

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
