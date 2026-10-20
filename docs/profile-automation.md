# Profile updates

The `Refresh profile` GitHub Actions workflow checks public project metadata every hour at minute 17 (UTC). GitHub may delay scheduled runs. It also runs when its script/configuration changes and can be started from **Actions → Refresh profile → Run workflow**.

## What updates

- New public, original, active GitHub repositories; their descriptions, language, star counts and last code-push dates.
- Kaggle notebook titles, upvotes and last-run dates from the portfolio's existing daily `assets/kaggle-feed.json`.
- Project titles, descriptions and tools from the portfolio's `assets/projects.json` catalog.
- Up to four compact homepage highlights, plus a collapsed archive of all eligible projects.

Highlights prioritize the newest GitHub repository, most-starred GitHub project, most-upvoted Kaggle notebook and latest Kaggle notebook. A project can qualify for multiple labels but appears only once. Remaining slots show recently updated projects. Zero-star or zero-upvote projects are never labeled most popular. GitHub stars and Kaggle upvotes are ranked separately.

The banner, case studies, personal introduction and technical toolkit are maintained manually. Adding a library to a repository does not automatically claim proficiency on the profile. Repository descriptions take priority over catalog descriptions; known projects have configured fallback descriptions when GitHub has none.

## Configuration

Edit `profile-config.json` to change the owner, public catalog/feed URLs, fallback descriptions or exclusions. Add the topic `hide-from-profile` to hide a repository. Forks, private, archived and disabled repositories, and this profile repository itself, are excluded. `aliases` maps a Kaggle URL to an equivalent GitHub URL to avoid duplicates. Keep the two `PROFILE:HIGHLIGHTS` and two `PROFILE:ARCHIVE` comment markers in `README.md`; only their contents are regenerated.

Run locally with Python 3.10+:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/update_profile.py
```

No packages, personal access token or new Kaggle secret are needed. Actions uses its repository-scoped `GITHUB_TOKEN`. Only public source fields are saved in `assets/profile-live.json`. An unavailable optional feed retains its last valid data; a GitHub API failure leaves the profile untouched. An unchanged response does not create a commit. There is no force-push; if a concurrent change prevents publishing, the next run reads the latest branch and retries.

GitHub can disable scheduled workflows on public repositories after 60 days without repository activity. If this happens, re-enable this workflow in the Actions tab. Ordinary profile refresh commits count as activity only when source data has actually changed; the workflow does not create artificial daily activity.

Official references: [scheduled events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), [automatic workflow disabling](https://docs.github.com/en/actions/managing-workflow-runs/disabling-and-enabling-a-workflow), [GITHUB_TOKEN](https://docs.github.com/en/actions/concepts/security/github_token).
