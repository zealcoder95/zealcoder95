#!/usr/bin/env python3
"""Refresh bounded profile highlights from public metadata; no third-party packages."""

import argparse
import base64
from datetime import datetime, timezone
from html import escape
import json
import os
from pathlib import Path
import re
import sys
import textwrap
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = Path("assets/profile-live.json")
REGIONS = ("HIGHLIGHTS", "ARCHIVE")


def clean(value):
    """Remove control characters and collapse untrusted metadata to one line."""
    return " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", str(value or "")).split())


def english(value):
    return clean(value.get("en", "") if isinstance(value, dict) else value)


def safe_url(value):
    value = clean(value)
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Project links must be HTTPS URLs without credentials")
    return value.rstrip("/")


def date(value):
    """Normalize both Kaggle's naive UTC times and GitHub's Z times."""
    if not value:
        return ""
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def number(value):
    return max(0, int(value or 0))


def fetch_json(url):
    headers = {"User-Agent": "zealcoder95-profile-refresh", "Accept": "application/json"}
    # The workflow token is only ever sent to the GitHub API, never to feed hosts.
    if urlparse(url).hostname == "api.github.com" and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        return json.load(response)


def fetch_repositories(owner, fetch=fetch_json):
    if not re.fullmatch(r"[A-Za-z0-9-]+", owner):
        raise ValueError("Invalid GitHub owner")
    repositories = []
    for page in range(1, 101):
        batch = fetch(f"https://api.github.com/users/{owner}/repos?type=owner&sort=created&per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ValueError("GitHub returned an invalid repository list")
        repositories.extend(batch)
        if len(batch) < 100:
            return repositories
    raise ValueError("Repository pagination exceeded its safety limit")


def github_source(repositories, config):
    result = []
    excluded = {name.casefold() for name in config.get("exclude_repositories", [])}
    for repo in repositories:
        if (repo.get("private") or repo.get("fork") or repo.get("archived") or repo.get("disabled")
                or repo["name"].casefold() in excluded
                or config.get("hide_topic", "hide-from-profile") in repo.get("topics", [])):
            continue
        result.append({
            "key": "github:" + str(repo["id"]), "name": clean(repo["name"]),
            "url": safe_url(repo["html_url"]), "description": clean(repo.get("description")),
            "language": {"TSQL": "T-SQL"}.get(clean(repo.get("language")), clean(repo.get("language"))),
            "stars": number(repo.get("stargazers_count")),
            "created_at": date(repo.get("created_at")), "updated_at": date(repo.get("pushed_at")),
            "topics": sorted(clean(topic) for topic in repo.get("topics", [])),
        })
    return sorted(result, key=lambda item: item["key"])


def catalog_source(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("Invalid portfolio catalog")
    result = []
    for item in payload["items"]:
        result.append({
            "url": safe_url(item["link"]), "title": english(item.get("title")),
            "description": english(item.get("desc")),
            "method": english(item.get("method")), "result": english(item.get("result")),
            "tools": [clean(tool) for tool in item.get("tools", [])],
            "updated_at": date(item.get("updatedAt")),
        })
    return sorted(result, key=lambda item: item["url"])


def kaggle_source(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("Invalid Kaggle feed")
    result = []
    for item in payload["items"]:
        result.append({
            "key": "kaggle:" + clean(item["ref"]), "url": safe_url(item["url"]),
            "title": clean(item["title"]), "votes": number(item.get("totalVotes")),
            "language": clean(item.get("language")), "updated_at": date(item.get("lastRunTime")),
        })
    return sorted(result, key=lambda item: item["key"])


def optional_source(name, url, normalize, cached, fetch):
    try:
        return normalize(fetch(url))
    except Exception as error:
        # A feed outage must never erase working project cards or their metadata.
        if name not in cached:
            raise RuntimeError(f"No valid {name} data is available") from error
        print(f"Warning: {name} unavailable; retaining the last valid public snapshot ({type(error).__name__}).", file=sys.stderr)
        return cached[name]


def collect_sources(config, previous, fetch=fetch_json):
    cached = previous.get("sources", {})
    # Required: if GitHub fails, stop before writing anything.
    github = github_source(fetch_repositories(config["owner"], fetch), config)
    catalog = optional_source("catalog", config["catalog_url"], catalog_source, cached, fetch)
    kaggle = optional_source("kaggle", config["kaggle_url"], kaggle_source, cached, fetch)
    return {"github": github, "catalog": catalog, "kaggle": kaggle}


def build_projects(sources, config):
    aliases = config.get("aliases", {})
    catalog = {aliases.get(item["url"], item["url"]): item for item in sources["catalog"]}
    projects = {}
    by_url = {}
    for repo in sources["github"]:
        details = catalog.get(repo["url"], {})
        fallback = config.get("fallbacks", {}).get(repo["name"], {})
        tools = details.get("tools") or fallback.get("tools") or ([repo["language"]] if repo["language"] else [])
        project = {
            "key": repo["key"], "title": details.get("title") or fallback.get("title") or repo["name"],
            "description": repo["description"] or details.get("description") or fallback.get("description", ""),
            "method": english(details.get("method") or fallback.get("method")),
            "result": english(details.get("result") or fallback.get("result")),
            "tools": list(tools), "links": [{"label": "GitHub", "url": repo["url"]}],
            "stars": repo["stars"], "created_at": repo["created_at"], "updated_at": repo["updated_at"],
        }
        # Show fresh GitHub language metadata even when curated tools also exist.
        if repo["language"] and repo["language"] not in project["tools"]:
            project["tools"].append(repo["language"])
        projects[repo["key"]] = project
        by_url[repo["url"]] = project
    for notebook in sources["kaggle"]:
        canonical = aliases.get(notebook["url"], notebook["url"])
        if urlparse(canonical).hostname == "github.com" and canonical not in by_url:
            # A hidden/archived GitHub project must not reappear through an alias.
            continue
        details = catalog.get(canonical, {})
        project = by_url.get(canonical)
        if project is None:
            project = {
                "key": notebook["key"], "title": details.get("title") or notebook["title"],
                "description": details.get("description", ""), "tools": list(details.get("tools", [])),
                "method": english(details.get("method")), "result": english(details.get("result")),
                "links": [], "created_at": "", "updated_at": notebook["updated_at"],
            }
            projects[project["key"]] = project
            by_url[canonical] = project
        project["links"].append({"label": "Kaggle", "url": notebook["url"]})
        project["votes"] = notebook["votes"]
        project["notebook_updated_at"] = notebook["updated_at"]
        project["updated_at"] = max(project["updated_at"], notebook["updated_at"])
        if notebook["language"] and notebook["language"] not in project["tools"]:
            project["tools"].append(notebook["language"])
    for url, details in catalog.items():
        if urlparse(url).hostname == "github.com" and url not in by_url:
            continue
        if url not in by_url:
            key = "catalog:" + url
            projects[key] = {
                "key": key, "title": details["title"], "description": details["description"],
                "method": english(details.get("method")), "result": english(details.get("result")),
                "tools": details["tools"], "links": [{"label": "Project", "url": details["url"]}],
                "created_at": "", "updated_at": details["updated_at"],
            }
    return sorted(projects.values(), key=lambda project: project["key"])


def select_highlights(projects, limit):
    if not 1 <= limit <= 4:
        raise ValueError("max_cards must be between 1 and 4")
    selected = []
    by_key = {}

    def add(project, reason):
        if project is None:
            return
        if project["key"] in by_key:
            by_key[project["key"]]["reasons"].append(reason)
        elif len(selected) < limit:
            item = {"key": project["key"], "reasons": [reason]}
            selected.append(item)
            by_key[item["key"]] = item

    def best(items, metric):
        return max(items, key=lambda item: (item.get(metric, ""), item["updated_at"], item["key"]), default=None)

    github = [project for project in projects if "stars" in project]
    kaggle = [project for project in projects if "votes" in project]
    add(best(github, "created_at"), "Newest repository")
    add(best([project for project in github if project["stars"] > 0], "stars"), "Most starred on GitHub")
    add(best([project for project in kaggle if project["votes"] > 0], "votes"), "Most upvoted on Kaggle")
    add(best(kaggle, "notebook_updated_at"), "Latest notebook")
    for project in sorted(projects, key=lambda item: (item["updated_at"], item["key"]), reverse=True):
        if project["key"] not in by_key:
            add(project, "Recently updated")
    return selected


def shorten(value, length):
    return textwrap.shorten(clean(value), width=length, placeholder="…") if value else ""


def metrics(project, include_zero=True):
    parts = []
    if "stars" in project and (include_zero or project["stars"] > 0):
        parts.append(f'{project["stars"]} GitHub ' + ("star" if project["stars"] == 1 else "stars"))
    if "votes" in project and (include_zero or project["votes"] > 0):
        parts.append(f'{project["votes"]} Kaggle ' + ("upvote" if project["votes"] == 1 else "upvotes"))
    return " · ".join(parts) or " · ".join(link["label"] for link in project["links"])


def svg_text(x, y, size, value, extra=""):
    return f'<text x="{x}" y="{y}" font-size="{size}" {extra}>{escape(str(value))}</text>'


def project_counts(projects):
    """Platform totals overlap when one project is published on both platforms."""
    return {"projects": len(projects), "notebooks": sum("votes" in item for item in projects),
            "repositories": sum("stars" in item for item in projects)}


def overview_description(projects):
    counts = project_counts(projects)
    total = f'{counts["projects"]} unique public ' + ("project" if counts["projects"] == 1 else "projects")
    notebooks = f'{counts["notebooks"]} Kaggle ' + ("notebook" if counts["notebooks"] == 1 else "notebooks")
    repositories = f'{counts["repositories"]} GitHub ' + ("repository" if counts["repositories"] == 1 else "repositories")
    return total + "; " + notebooks + "; " + repositories + ". Linked versions count as one project."


def render_overview(projects):
    counts = project_counts(projects)
    body = ""
    for index, (key, label, note, accent) in enumerate((
            ("projects", "PUBLIC PROJECTS", "Linked work counted once", "#59d9ff"),
            ("notebooks", "KAGGLE NOTEBOOKS", "Data, methods & findings", "#b995ff"),
            ("repositories", "GITHUB REPOS", "Code & applications", "#59d9ff"))):
        x = 28 + index * 373
        body += svg_text(x, 73, 44, str(counts[key]).zfill(2), f'fill="{accent}" font-weight="700"')
        body += svg_text(x + 82, 48, 15, label, 'font-weight="700" letter-spacing=".8"')
        body += svg_text(x + 82, 74, 14, note, 'fill="#a9bad5"')
        if index:
            body += f'<path d="M{x-14} 25v62" stroke="#354465"/>'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="112" viewBox="0 0 1120 112" role="img" aria-labelledby="title desc">
  <title id="title">Portfolio at a glance</title>
  <desc id="desc">{escape(overview_description(projects))}</desc>
  <defs><linearGradient id="surface"><stop stop-color="#0c1930"/><stop offset=".5" stop-color="#1b2141"/><stop offset="1" stop-color="#0d1930"/></linearGradient><linearGradient id="edge"><stop stop-color="#32cde6"/><stop offset="1" stop-color="#a68afa"/></linearGradient></defs>
  <rect x="1" y="1" width="1118" height="110" rx="15" fill="url(#surface)" stroke="#354465" stroke-width="2"/>
  <path d="M26 2h1068" stroke="url(#edge)" stroke-width="2"/>
  <g font-family="Arial,sans-serif" fill="#eef2ff">{body}</g>
</svg>
'''


def card_mark(visual, accent):
    """Small original line symbols, consistent with the profile badge system."""
    paths = {
        "ai": "M10 1c0 5-4 9-9 9 5 0 9 4 9 9 0-5 4-9 9-9-5 0-9-4-9-9Z",
        "nyc": "M2 2v16h16M5 13l4-5 4 2 5-6",
        "sales": "M2 18V9h4v9m3 0V3h4v15m3 0V6h4v12",
        "sql": "M2 4c0-4 16-4 16 0s-16 4-16 0v12c0 4 16 4 16 0V4M2 10c0 4 16 4 16 0",
        "adana": "M10 18V9m0 2C0 12 1 3 1 3s8-1 9 8m0 2c0-9 9-10 9-10s2 9-9 10",
    }
    path = paths.get(visual, "M7 4 1 10l6 6m6-12 6 6-6 6")
    return (f'<g transform="translate(26 21)" fill="none" stroke="{accent}" '
            f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="{path}"/></g>')


def card_art(visual, evidence, accent, platform):
    """Real chart data or descriptive project diagrams; never invented chart values."""
    muted = 'fill="#9eafca"'
    if visual == "nyc":
        body = svg_text(26, 79, 17, "Student-reported safety by borough", 'fill="#b9c8e0"')
        left, width = 166, 318
        for tick in (0, 4, 8):
            x = left + tick / 8 * width
            body += f'<path d="M{x} 94v134" stroke="#24334d"/>'
            body += svg_text(x, 246, 14, tick, muted + ' text-anchor="middle"')
        for index, item in enumerate(evidence["nyc"]["values"]):
            y = 107 + index * 26
            length = item["score"] / 8 * width
            body += svg_text(26, y + 6, 16, item["borough"], muted)
            body += f'<rect x="{left}" y="{y-9}" width="{length:.2f}" height="17" rx="4" fill="url(#accent)"/>'
            body += svg_text(left + length + 8, y + 5, 16, f'{item["score"]:.2f}', 'font-weight="600"')
        return body + svg_text(26, 268, 13, "Saved notebook · mean safety scores", muted)
    if visual == "ai":
        body = svg_text(26, 79, 17, "A bilingual, portfolio-aware companion", 'fill="#b9c8e0"')
        for index, (label, detail) in enumerate((("Chat", "TR / EN"), ("API", "Context + history"), ("Gemini", "Responses"))):
            x = 28 + index * 182
            border = accent if label == "API" else "#46597b"
            body += f'<rect x="{x}" y="112" width="140" height="80" rx="13" fill="#111f37" stroke="{border}" stroke-width="1.5"/>'
            body += svg_text(x + 70, 147, 24, label, 'text-anchor="middle" font-weight="600"')
            body += svg_text(x + 70, 173, 14, detail, muted + ' text-anchor="middle"')
            if index < 2:
                body += f'<path d="M{x+150} 151h22m-7-6 7 6-7 6" stroke="{accent}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        body += svg_text(280, 230, 17, "Interface → context → model", muted + ' text-anchor="middle"')
        return body + svg_text(26, 268, 13, "Application flow · external model API", muted)
    if visual == "sales":
        body = svg_text(26, 79, 17, "Total sales by region", 'fill="#b9c8e0"')
        baseline, height, maximum = 222, 122, 300_000_000
        for tick in (0, 150, 300):
            y = baseline - tick / 300 * height
            body += f'<path d="M64 {y}h456" stroke="#293751"/>'
            body += svg_text(52, y + 4, 14, tick, muted + ' text-anchor="end"')
        for index, item in enumerate(evidence["adidas"]["values"]):
            x, value = 91 + index * 86, item["sales"]
            bar_height = value / maximum * height
            body += f'<rect x="{x}" y="{baseline-bar_height:.2f}" width="44" height="{bar_height:.2f}" rx="5" fill="url(#accent)"><title>{escape(item["region"])}: {value:,}</title></rect>'
            body += svg_text(x + 22, baseline - bar_height - 9, 16, f'{value / 1_000_000:.1f}', 'text-anchor="middle" font-weight="600"')
            body += svg_text(x + 22, 242, 14, item["region"], muted + ' text-anchor="middle"')
        return body + svg_text(26, 268, 13, "Sales in millions · saved notebook", muted)
    if visual == "sql":
        body = svg_text(26, 79, 17, "Customers → orders ← products", 'fill="#b9c8e0"')
        for x, y, width, height, label, fields in (
                (26, 110, 155, 91, "Customers", (("customer_id", "PK"),)),
                (209, 98, 142, 130, "Orders", (("order_id", "PK"), ("customer_id", "FK"), ("product_id", "FK"))),
                (378, 110, 155, 91, "Products", (("product_id", "PK"),))):
            body += f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="11" fill="#111f37" stroke="#46597b"/>'
            body += svg_text(x + 13, y + 26, 18, label, 'font-weight="600"')
            body += f'<path d="M{x+1} {y+38}h{width-2}" stroke="#354766"/>'
            for offset, (field, key) in enumerate(fields):
                field_y = y + 60 + offset * 23
                body += svg_text(x + 13, field_y, 14, field, 'font-family="monospace"')
                body += svg_text(x + width - 12, field_y, 11, key, f'fill="{accent}" text-anchor="end"')
        for x in (181, 351):
            body += f'<path d="M{x} 165h27" stroke="{accent}" stroke-width="2"/>'
        body += svg_text(187, 156, 11, "1", muted) + svg_text(200, 156, 13, "*", muted)
        body += svg_text(356, 156, 13, "*", muted) + svg_text(368, 156, 11, "1", muted)
        body += svg_text(280, 246, 16, "Customer totals · date-range queries", muted + ' text-anchor="middle"')
        return body + svg_text(26, 268, 13, "Key relationships · SQL Server", muted)
    if visual == "adana":
        return (f'<image x="22" y="66" width="516" height="186" preserveAspectRatio="xMidYMid meet" href="{evidence["adana"]["image_data"]}"/>'
                + svg_text(26, 268, 13, "Original monthly generation · 2018–2022", muted))
    body = '<rect x="219" y="88" width="122" height="122" rx="20" fill="#111f37" stroke="#354766"/>'
    body += f'<path d="M258 123l-18 25 18 25m44-50 18 25-18 25m-17-52-12 60" stroke="{accent}" stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
    return body + svg_text(280, 243, 19, platform, muted + ' text-anchor="middle"')


def render_card(project, reasons, index, presentation=None, evidence=None):
    presentation, evidence = presentation or {}, evidence or {}
    visual = presentation.get("visual")
    accent = {"ai": "#b995ff", "nyc": "#59d9ff", "sales": "#b995ff",
              "sql": "#59d9ff", "adana": "#59d9ff"}.get(visual, "#59d9ff" if index % 2 else "#b995ff")
    platform = " · ".join(link["label"] for link in project["links"])
    category = presentation.get("category", "NOTEBOOK" if "votes" in project and "stars" not in project else "PROJECT")
    label = {"Newest repository": "LATEST REPOSITORY", "Most starred on GitHub": "MOST STARRED",
             "Most upvoted on Kaggle": "MOST UPVOTED", "Latest notebook": "LATEST NOTEBOOK",
             "Recently updated": "RECENTLY UPDATED"}[reasons[0]]
    if "Most starred on GitHub" in reasons and reasons[0] == "Newest repository":
        label = "LATEST + MOST STARRED"
    body = card_art(visual, evidence, accent, platform)
    body += svg_text(534, 268, 12, "zealcoder", 'fill="#8d9fbe" text-anchor="end" letter-spacing=".5"')
    description = " · ".join(reasons) + ". " + metrics(project) + ". Updated " + (project["updated_at"][:10] or "date not provided")
    cover_title = shorten(presentation.get("cover_title") or project["title"], 33)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="560" height="336" viewBox="0 0 560 336" role="img" aria-labelledby="title desc">
  <title id="title">{escape(project['title'])}</title>
  <desc id="desc">{escape(description)}</desc>
  <defs>
    <linearGradient id="accent"><stop stop-color="#32cde6"/><stop offset="1" stop-color="#a68afa"/></linearGradient>
    <radialGradient id="surface" cx="90%" cy="0%" r="100%"><stop stop-color="#1d2547"/><stop offset="1" stop-color="#0a1224"/></radialGradient>
    <pattern id="grid" width="28" height="28" patternUnits="userSpaceOnUse"><path d="M28 0H0V28" fill="none" stroke="#8da4dc" stroke-opacity=".07"/></pattern>
  </defs>
  <rect x="1" y="1" width="558" height="334" rx="17" fill="url(#surface)" stroke="#354465" stroke-width="2"/>
  <rect x="2" y="2" width="556" height="332" rx="16" fill="url(#grid)"/>
  <path d="M26 2h136" stroke="url(#accent)" stroke-width="3" stroke-linecap="round"/>
  <g font-family="Arial,sans-serif" fill="#eef2ff">
    {card_mark(visual, accent)}
    <text x="56" y="35" font-size="13" font-weight="700" letter-spacing="1" fill="{accent}">{escape(category)}</text>
    <text x="534" y="35" font-size="11" letter-spacing=".5" text-anchor="end" fill="#b0bed7">{escape(label)}</text>
    <path d="M26 52h508" stroke="#354465"/>
    <text x="26" y="88" font-size="27" font-weight="700">{escape(cover_title)}</text>
    <g transform="translate(0 42)">{body}</g>
  </g>
</svg>
'''


def render_highlights(projects, highlights, presentations=None, focus_links=None):
    presentations = presentations or {}
    lookup = {project["key"]: project for project in projects}
    overview_alt = escape(overview_description(projects), quote=True)
    rows = ["## Project Spotlight", "", "From data exploration to applied AI — methods, outputs and working code.", "",
            f'<p><img src="./assets/live/overview.svg" width="100%" alt="{overview_alt}" /></p>', "", "<table>"]
    for offset in range(0, len(highlights), 2):
        rows.append("  <tr>")
        for index, highlight in enumerate(highlights[offset:offset + 2], start=offset + 1):
            project = lookup[highlight["key"]]
            link = escape(project["links"][0]["url"], quote=True)
            alt = escape(project["title"] + " — " + "; ".join(highlight["reasons"]) + "; " + metrics(project), quote=True)
            rows.extend(["    <td width=\"50%\" valign=\"top\">",
                         f'      <a href="{link}"><img src="./assets/live/project-{index}.svg" width="100%" alt="{alt}" /></a>'])
            rows.append(f'      <p><a href="{link}"><strong>{escape(project["title"])}</strong></a></p>')
            details = []
            for field, label, length in (("method", "Approach", 115), ("result", "Output", 140)):
                if project.get(field):
                    details.append(f'<strong>{label}</strong> {escape(shorten(project[field], length))}')
            if details:
                rows.append("      <p>" + "<br/>".join(details) + "</p>")
            elif project["description"]:
                rows.append(f'      <p>{escape(shorten(project["description"], 145))}</p>')
            if project["tools"]:
                rows.append("      <p>" + " ".join(f"<code>{escape(tool)}</code>" for tool in project["tools"][:4]) + "</p>")
            links = list(project["links"])
            presentation = presentations.get(project["key"], {})
            if presentation.get("demo_url"):
                links.insert(0, {"label": presentation.get("demo_label", "Live demo"), "url": safe_url(presentation["demo_url"])})
            rows.append("      <p>" + " · ".join(f'<a href="{escape(item["url"], quote=True)}">{escape(item["label"])} ↗</a>' for item in links) + "</p>")
            rows.append(f'      <p><sub>{escape(metrics(project, include_zero=False))} · Updated {project["updated_at"][:10] or "date not provided"}</sub></p>')
            rows.append("    </td>")
        rows.append("  </tr>")
    rows.append("</table>")
    selected = {item["key"] for item in highlights}
    focus = []
    for item in (focus_links or [])[:2]:
        project = lookup.get(item["key"])
        if project and project["key"] not in selected:
            title = escape(clean(item.get("label")) or project["title"])
            link = escape(safe_url(project["links"][0]["url"]), quote=True)
            focus.append(f'<a href="{link}">{title} ↗</a>')
    if focus:
        rows.extend(["", '<p><strong>ML &amp; forecasting</strong> · ' + " · ".join(focus) + "</p>"])
    return "\n".join(rows)


def render_archive(projects):
    rows = ["## More to Explore", "", "<details>", f"<summary><strong>Browse all {len(projects)} public projects & notebooks</strong></summary>", "", "<ul>"]
    for project in sorted(projects, key=lambda item: (item["updated_at"], item["key"]), reverse=True):
        description = f'<br/><sub>{escape(project["description"])}</sub>' if project["description"] else ""
        for field, label in (("method", "Approach"), ("result", "Output")):
            if project.get(field):
                description += f'<br/><sub>{label}: {escape(project[field])}</sub>'
        links = " · ".join(f'<a href="{escape(item["url"], quote=True)}">{escape(item["label"])}</a>' for item in project["links"])
        rows.append(f'  <li><p><strong>{escape(project["title"])}</strong> · {links}{description}<br/><sub>{escape(metrics(project))} · Updated {project["updated_at"][:10] or "date not provided"}</sub></p></li>')
    rows.extend(["</ul>", "", "</details>"])
    return "\n".join(rows)


def replace_region(readme, name, content):
    start, end = f"<!-- PROFILE:{name}:START -->", f"<!-- PROFILE:{name}:END -->"
    if readme.count(start) != 1 or readme.count(end) != 1 or readme.index(start) >= readme.index(end):
        raise ValueError(f"Missing, duplicate or invalid {name} markers; README was not changed")
    before, rest = readme.split(start, 1)
    _, after = rest.split(end, 1)
    return before + start + "\n" + content + "\n" + end + after


def update(root=ROOT, fetch=fetch_json, now=None):
    config = json.loads((root / "profile-config.json").read_text())
    readme = (root / "README.md").read_text()
    # Validate every marker before fetching or writing anything.
    for name in REGIONS:
        replace_region(readme, name, "")
    previous = json.loads((root / SNAPSHOT).read_text()) if (root / SNAPSHOT).exists() else {}
    sources = collect_sources(config, previous, fetch)
    projects = build_projects(sources, config)
    highlights = select_highlights(projects, config["max_cards"])
    snapshot = {"schema_version": 1, "sources": sources, "projects": projects, "highlights": highlights}
    old_content = {key: value for key, value in previous.items() if key != "updated_at"}
    if snapshot == old_content:
        snapshot["updated_at"] = previous["updated_at"]
    else:
        snapshot["updated_at"] = date(now or datetime.now(timezone.utc).isoformat())
    readme = replace_region(readme, "HIGHLIGHTS", render_highlights(projects, highlights, config.get("presentations"), config.get("focus_links")))
    readme = replace_region(readme, "ARCHIVE", render_archive(projects))
    # Construct all outputs before making any changes to the working tree.
    outputs = {Path("README.md"): readme, SNAPSHOT: json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
               Path("assets/live/overview.svg"): render_overview(projects)}
    lookup = {project["key"]: project for project in projects}
    presentations = config.get("presentations", {})
    evidence = {}
    if any(presentations.get(item["key"], {}).get("visual") in ("nyc", "adana", "sales") for item in highlights):
        evidence = json.loads((root / "assets/profile-project-sources.json").read_text())
    if any(presentations.get(item["key"], {}).get("visual") == "adana" for item in highlights):
        image = (root / evidence["adana"]["localFigure"]).read_bytes()
        evidence["adana"]["image_data"] = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
    for index, highlight in enumerate(highlights, start=1):
        outputs[Path(f"assets/live/project-{index}.svg")] = render_card(lookup[highlight["key"]], highlight["reasons"], index, presentations.get(highlight["key"]), evidence)
    changed = []
    for relative, content in outputs.items():
        path = root / relative
        if not path.exists() or path.read_text() != content:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            changed.append(str(relative))
    live_dir = root / "assets/live"
    if live_dir.exists():
        for path in live_dir.glob("project-*.svg"):
            if path.relative_to(root) not in outputs:
                path.unlink()
                changed.append(str(path.relative_to(root)))
    print(f"{len(projects)} public projects, {len(highlights)} highlights; " + ("updated " + ", ".join(changed) if changed else "already current"))
    return changed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    update(args.root)
