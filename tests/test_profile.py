import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
import xml.etree.ElementTree as ET

SPEC = importlib.util.spec_from_file_location("profile", Path(__file__).resolve().parents[1] / "scripts/update_profile.py")
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)


def repo(identifier=1, name="project", stars=0, created="2026-01-01T00:00:00Z", **extra):
    item = {"id": identifier, "name": name, "html_url": f"https://github.com/tester/{name}",
            "created_at": created, "pushed_at": created, "description": "Real project description",
            "stargazers_count": stars, "language": "Python", "topics": []}
    item.update(extra)
    return item


def notebook(ref="analysis", votes=3, updated="2026-02-01T00:00:00"):
    return {"ref": f"tester/{ref}", "url": f"https://www.kaggle.com/code/tester/{ref}",
            "title": ref, "totalVotes": votes, "lastRunTime": updated}


CONFIG = {"owner": "tester", "exclude_repositories": ["tester"], "max_cards": 4,
          "catalog_url": "https://example.org/catalog.json", "kaggle_url": "https://example.org/kaggle.json"}


def sources(repositories=None, notebooks=None, catalog=None):
    return {"github": profile.github_source(repositories or [], CONFIG),
            "kaggle": profile.kaggle_source({"items": notebooks or []}),
            "catalog": profile.catalog_source({"items": catalog or []})}


class ProfileTests(unittest.TestCase):
    def test_new_zero_star_project_and_older_popular_project_both_appear(self):
        projects = profile.build_projects(sources([repo(1, "old", 12), repo(2, "new", 0, "2026-10-01T00:00:00Z")]), CONFIG)
        selected = profile.select_highlights(projects, 4)
        self.assertEqual(selected[0], {"key": "github:2", "reasons": ["Newest repository"]})
        self.assertIn({"key": "github:1", "reasons": ["Most starred on GitHub"]}, selected)

    def test_zero_counts_never_get_popularity_labels(self):
        projects = profile.build_projects(sources([repo()], [notebook(votes=0)]), CONFIG)
        reasons = str(profile.select_highlights(projects, 4))
        self.assertNotIn("Most starred", reasons)
        self.assertNotIn("Most upvoted", reasons)

    def test_filters_and_catalog_cannot_reintroduce_hidden_repositories(self):
        repos = [repo(1, "private", private=True), repo(2, "fork", fork=True),
                 repo(3, "archived", archived=True), repo(4, "disabled", disabled=True),
                 repo(5, "tester"), repo(6, "hidden", topics=["hide-from-profile"]), repo(7, "visible")]
        catalog = [{"link": "https://github.com/tester/hidden", "title": {"en": "Hidden"}}]
        projects = profile.build_projects(sources(repos, catalog=catalog), CONFIG)
        self.assertEqual([project["key"] for project in projects], ["github:7"])

    def test_cross_platform_duplicates_and_metrics(self):
        config = dict(CONFIG, aliases={"https://www.kaggle.com/code/tester/analysis": "https://github.com/tester/project"})
        projects = profile.build_projects(sources([repo(stars=1)], [notebook(votes=50)]), config)
        self.assertEqual(len(projects), 1)
        self.assertEqual(len(projects[0]["links"]), 2)
        selected = profile.select_highlights(projects, 4)
        self.assertEqual(len(selected), 1)
        self.assertEqual(len(selected[0]["reasons"]), 4)
        self.assertEqual(profile.metrics(projects[0]), "1 GitHub star · 50 Kaggle upvotes")

    def test_platform_popularity_is_ranked_separately(self):
        projects = profile.build_projects(sources([repo(stars=1)], [notebook(votes=100)]), CONFIG)
        selected = profile.select_highlights(projects, 4)
        self.assertIn("Most starred on GitHub", selected[0]["reasons"])
        self.assertIn("Most upvoted on Kaggle", selected[1]["reasons"])

    def test_catalog_and_fresh_description_language_propagate(self):
        catalog = [{"link": "https://github.com/tester/project", "title": {"en": "Updated title"},
                    "desc": {"en": "Catalog description"}, "tools": ["Pandas"]}]
        project = profile.build_projects(sources([repo(description="Fresh GitHub description", language="R")], catalog=catalog), CONFIG)[0]
        self.assertEqual(project["title"], "Updated title")
        self.assertEqual(project["description"], "Fresh GitHub description")
        self.assertEqual(project["tools"], ["Pandas", "R"])

    def test_untrusted_metadata_is_escaped_and_svg_is_valid(self):
        project = profile.build_projects(sources([repo(name='evil', description='<script>alert("x")</script>')]), CONFIG)[0]
        project["title"] = 'Title & <script> "quoted"'
        selected = profile.select_highlights([project], 4)
        svg = profile.render_card(project, selected[0]["reasons"], 1)
        ET.fromstring(svg)
        self.assertIn("&lt;script&gt;", svg)
        html = profile.render_highlights([project], selected)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<script>", profile.render_archive([project]))
        with self.assertRaises(ValueError):
            profile.safe_url("javascript:alert(1)")

    def test_managed_regions_preserve_everything_else(self):
        text = "manual intro\n<!-- PROFILE:HIGHLIGHTS:START -->\nold\n<!-- PROFILE:HIGHLIGHTS:END -->\nmanual footer\n"
        actual = profile.replace_region(text, "HIGHLIGHTS", "new")
        self.assertEqual(actual, text.replace("\nold\n", "\nnew\n"))
        for invalid in ["missing", text + "<!-- PROFILE:HIGHLIGHTS:START -->", text.replace("START", "END", 1)]:
            with self.assertRaises(ValueError):
                profile.replace_region(invalid, "HIGHLIGHTS", "new")

    def test_all_repository_pages_are_fetched(self):
        calls = []
        def fetch(url):
            calls.append(url)
            return [repo(i) for i in range(100)] if parse_qs(urlparse(url).query)["page"] == ["1"] else [repo(101)]
        self.assertEqual(len(profile.fetch_repositories("tester", fetch)), 101)
        self.assertEqual(len(calls), 2)
        self.assertIn("page=2", calls[1])

    def make_root(self, root):
        (root / "profile-config.json").write_text(json.dumps(CONFIG))
        (root / "README.md").write_text("Keep the banner.\n" + "\n".join(
            f"<!-- PROFILE:{name}:START -->\n<!-- PROFILE:{name}:END -->" for name in profile.REGIONS) + "\nKeep the footer.\n")

    def fake_fetch(self, url):
        if "api.github.com" in url:
            return [repo()]
        if url == CONFIG["catalog_url"]:
            return {"updatedAt": "ignored-feed-clock", "items": []}
        return {"items": [notebook()]}

    def test_unchanged_data_is_idempotent_and_real_changes_update(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_root(root)
            self.assertTrue(profile.update(root, self.fake_fetch, "2026-10-20T01:00:00Z"))
            before = (root / profile.SNAPSHOT).read_bytes()
            self.assertEqual(profile.update(root, self.fake_fetch, "2026-10-21T01:00:00Z"), [])
            self.assertEqual((root / profile.SNAPSHOT).read_bytes(), before)
            def changed_fetch(url):
                data = self.fake_fetch(url)
                if "api.github.com" in url:
                    data[0]["description"] = "A newly added description"
                    data[0]["stargazers_count"] = 6
                return data
            self.assertTrue(profile.update(root, changed_fetch))
            self.assertIn("A newly added description", (root / "README.md").read_text())
            self.assertIn("6 GitHub stars", (root / "assets/live/project-1.svg").read_text())

    def test_feed_outage_keeps_cache_and_github_failure_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_root(root)
            profile.update(root, self.fake_fetch)
            def broken_optional(url):
                if "api.github.com" in url:
                    return [repo()]
                raise OSError("Network outage")
            self.assertEqual(profile.update(root, broken_optional), [])
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            def broken_github(url):
                raise OSError("GitHub outage")
            with self.assertRaises(OSError):
                profile.update(root, broken_github)
            self.assertEqual(before, {path: path.read_bytes() for path in root.rglob("*") if path.is_file()})

    def test_token_is_not_sent_to_feed_host(self):
        class Response:
            def __enter__(self):
                import io
                return io.StringIO("{}")
            def __exit__(self, *args):
                pass
        with patch.dict("os.environ", {"GITHUB_TOKEN": "test-token"}), patch.object(profile, "urlopen", return_value=Response()) as request:
            profile.fetch_json("https://raw.githubusercontent.com/tester/repo/main/feed.json")
            self.assertFalse(request.call_args.args[0].has_header("Authorization"))
            profile.fetch_json("https://api.github.com/users/tester/repos")
            self.assertEqual(request.call_args.args[0].get_header("Authorization"), "Bearer test-token")

    def test_new_repository_keeps_its_own_cover_after_ranking_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_root(root)
            config = dict(CONFIG, presentations={"github:1": {"category": "SQL", "visual": "sql"}})
            (root / "profile-config.json").write_text(json.dumps(config))
            def fetch(url):
                if "api.github.com" in url:
                    return [repo(1, "existing-sql", 4), repo(2, "brand-new", 0, "2026-10-20T01:00:00Z")]
                return {"items": []}
            profile.update(root, fetch)
            first = (root / "assets/live/project-1.svg").read_text()
            second = (root / "assets/live/project-2.svg").read_text()
            self.assertIn("brand-new", first)
            self.assertNotIn("Tables &amp; relationships", first)
            self.assertIn("Tables &amp; relationships", second)
            self.assertIn("brand-new", (root / "README.md").read_text())

    def test_project_demo_link_is_kept_in_the_unified_showcase(self):
        projects = profile.build_projects(sources([repo()]), CONFIG)
        selected = profile.select_highlights(projects, 4)
        html = profile.render_highlights(projects, selected, {"github:1": {"demo_url": "https://example.org/demo", "demo_label": "Try the app"}})
        self.assertIn('href="https://example.org/demo">Try the app', html)
        self.assertIn('href="https://github.com/tester/project"', html)


if __name__ == "__main__":
    unittest.main()
