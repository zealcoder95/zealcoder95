#!/usr/bin/env python3
"""Refresh bounded profile highlights from public metadata; no third-party packages."""

import argparse
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


def render_card(project, reasons, index):
    accent = "#59d9ff" if index % 2 else "#b995ff"
    title = textwrap.wrap(clean(project["title"]), width=29, max_lines=2, placeholder="…") or ["Project"]
    label = {"Newest repository": "NEWEST REPOSITORY", "Most starred on GitHub": "MOST STARRED · GITHUB",
             "Most upvoted on Kaggle": "MOST UPVOTED · KAGGLE", "Latest notebook": "LATEST NOTEBOOK",
             "Recently updated": "RECENTLY UPDATED"}[reasons[0]]
    if "Most starred on GitHub" in reasons and reasons[0] == "Newest repository":
        label = "NEWEST + MOST STARRED · GITHUB"
    title_nodes = "".join(f'<text x="30" y="{96 + line * 36}" font-size="29" font-weight="700" fill="#f5f7ff">{escape(text)}</text>' for line, text in enumerate(title))
    updated = project["updated_at"][:10] or "Date not provided"
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="560" height="216" viewBox="0 0 560 216" role="img" aria-labelledby="title desc">
  <title id="title">{escape(project['title'])}</title>
  <desc id="desc">{escape(' · '.join(reasons) + '. ' + metrics(project) + '. Updated ' + updated)}</desc>
  <rect x="1" y="1" width="558" height="214" rx="18" fill="#101528" stroke="#303951" stroke-width="2"/>
  <path d="M30 25h50" stroke="{accent}" stroke-width="4" stroke-linecap="round"/>
  <text x="30" y="53" font-family="Arial,sans-serif" font-size="13" font-weight="700" letter-spacing="1.1" fill="{accent}">{escape(label)}</text>
  <g font-family="Arial,sans-serif">{title_nodes}
    <text x="30" y="171" font-size="20" fill="{accent}">{escape(metrics(project, include_zero=False))}</text>
    <text x="30" y="195" font-size="13" fill="#abb6d3">Updated {escape(updated)}</text>
  </g>
</svg>
'''


def render_highlights(projects, highlights):
    lookup = {project["key"]: project for project in projects}
    rows = ["## Latest & Popular Projects", "", "<!-- Generated from public GitHub metadata and the portfolio's Kaggle feed. -->", "", "<table>"]
    for offset in range(0, len(highlights), 2):
        rows.append("  <tr>")
        for index, highlight in enumerate(highlights[offset:offset + 2], start=offset + 1):
            project = lookup[highlight["key"]]
            link = escape(project["links"][0]["url"], quote=True)
            alt = escape(project["title"] + " — " + "; ".join(highlight["reasons"]) + "; " + metrics(project), quote=True)
            rows.extend(["    <td width=\"50%\" valign=\"top\">",
                         f'      <a href="{link}"><img src="./assets/live/project-{index}.svg" width="100%" alt="{alt}" /></a>'])
            if project["description"]:
                rows.append(f'      <p>{escape(shorten(project["description"], 155))}</p>')
            if project["tools"]:
                rows.append("      <p>" + " ".join(f"<code>{escape(tool)}</code>" for tool in project["tools"][:4]) + "</p>")
            rows.append("      <p>" + " · ".join(f'<a href="{escape(item["url"], quote=True)}">{escape(item["label"])} ↗</a>' for item in project["links"]) + "</p>")
            rows.append("    </td>")
        rows.append("  </tr>")
    rows.extend(["</table>", "", "<sub>GitHub metadata refreshes hourly; Kaggle follows the portfolio's daily feed. Stars and upvotes are ranked within each platform.</sub>"])
    return "\n".join(rows)


def render_archive(projects):
    rows = ["## Project Archive", "", "<details>", f"<summary>Browse all {len(projects)} public projects & notebooks</summary>", "", "<table>",
            "  <tr><th>Project</th><th>Explore</th><th>Community</th><th>Updated</th></tr>"]
    for project in sorted(projects, key=lambda item: (item["updated_at"], item["key"]), reverse=True):
        description = f'<br/><sub>{escape(project["description"])}</sub>' if project["description"] else ""
        links = " · ".join(f'<a href="{escape(item["url"], quote=True)}">{escape(item["label"])}</a>' for item in project["links"])
        rows.append(f'  <tr><td><strong>{escape(project["title"])}</strong>{description}</td><td>{links}</td><td>{escape(metrics(project))}</td><td>{project["updated_at"][:10] or "—"}</td></tr>')
    rows.extend(["</table>", "", "</details>"])
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
    readme = replace_region(readme, "HIGHLIGHTS", render_highlights(projects, highlights))
    readme = replace_region(readme, "ARCHIVE", render_archive(projects))
    # Construct all outputs before making any changes to the working tree.
    outputs = {Path("README.md"): readme, SNAPSHOT: json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"}
    lookup = {project["key"]: project for project in projects}
    for index, highlight in enumerate(highlights, start=1):
        outputs[Path(f"assets/live/project-{index}.svg")] = render_card(lookup[highlight["key"]], highlight["reasons"], index)
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
