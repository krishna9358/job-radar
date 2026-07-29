# Job Radar

Pulls entry-level / intern / new-grad roles straight from company hiring APIs.
No scraping, no API keys, no server, no dependencies — just Python's standard library.

`radar.py` writes `jobs.json` + `index.html`. GitHub Actions runs it every 6 hours
and publishes the page to GitHub Pages.

## Run it

```bash
cd ~/job-radar
python3 radar.py      # takes ~3 min, hits 54 job boards in parallel
open index.html       # macOS. Linux: xdg-open index.html
```

That's the whole thing. Re-run any time; postings you've already seen keep their
original `first_seen` date, and anything that appeared since the last run gets a
red **NEW** badge.

## Automate it (free, no server)

```bash
cd ~/job-radar
git init && git add -A && git commit -m "init"
gh repo create job-radar --public --source=. --push
```

Then in the repo: **Settings → Pages → Source: GitHub Actions**.

It now runs every 6 hours and your board is live at
`https://<your-username>.github.io/job-radar/`. Bookmark it on your phone.

To run it on demand: repo → **Actions → update job radar → Run workflow**.

## Companies covered (54)

| Platform | Companies |
|---|---|
| Greenhouse | Stripe, Databricks, Anthropic, DoorDash, Airbnb, Lyft, Instacart, Coinbase, Figma, Dropbox, Pinterest, Reddit, Discord, Robinhood, Affirm, Brex, Chime, SoFi, Asana, Samsara, MongoDB, Elastic, Cloudflare, Twilio, Datadog, Scale AI, Duolingo, Flexport, Zscaler, Okta, Rubrik, CockroachDB |
| Ashby | OpenAI, Ramp, Perplexity, Cursor, Linear, Supabase, ClickHouse, Zip, Sardine |
| Lever | Palantir, Match Group |
| Workday | NVIDIA, Salesforce, Adobe, Autodesk, Workday |
| Custom | Amazon, Uber, Atlassian, Oracle, Netflix, Microsoft |

**Microsoft** may fail on some networks — its API host (`gcsservices.careers.microsoft.com`)
serves a certificate that doesn't match the hostname from every location. The run
survives it and lists it under "feeds that failed" on the page. Try it from your machine.

**Not included:** Google, Rippling, Cisco, D. E. Shaw, Meta, Apple. Each runs a fully
JavaScript-rendered board with no reachable public JSON endpoint — I probed all of them.
See "Adding a company" for how to add one if you find its endpoint.

## Adding a company

Most companies rent their hiring software from one of ~6 vendors, all with public JSON
APIs. Find which one in 30 seconds: open the careers page, click a job, look at the URL.

| URL contains | ATS | Add to `companies.json` |
|---|---|---|
| `boards.greenhouse.io/<token>` or `gh_jid=` | greenhouse | `{"name":"X","ats":"greenhouse","token":"<token>"}` |
| `jobs.lever.co/<token>` | lever | `{"name":"X","ats":"lever","token":"<token>"}` |
| `jobs.ashbyhq.com/<token>` | ashby | `{"name":"X","ats":"ashby","token":"<token>"}` |
| `jobs.smartrecruiters.com/<token>` | smartrecruiters | `{"name":"X","ats":"smartrecruiters","token":"<token>"}` |
| `<tenant>.wdN.myworkdayjobs.com/<site>` | workday | `{"name":"X","ats":"workday","host":"<tenant>.wdN.myworkdayjobs.com","tenant":"<tenant>","site":"<site>"}` |
| `<host>/careers/job/...` (Eightfold) | eightfold | `{"name":"X","ats":"eightfold","host":"<host>","domain":"<company>.com"}` |

Verify before committing:

```bash
curl -s "https://boards-api.greenhouse.io/v1/boards/<token>/jobs" | head -c 200
```

For a fully custom site: open it with DevTools → Network → Fetch/XHR, find the request
returning the job list as JSON, then write a small function in `radar.py` that returns
`[(title, location, url), ...]` and register it in the `ATS` dict at the bottom of the
fetchers. The existing `uber`, `atlassian` and `oracle` functions are 6-line examples.

## Tuning what matches

Four knobs at the top of `radar.py`:

- `ROLE` — the kind of work (software, applied scientist, data, quant...)
- `LEVEL` — the entry-ness (intern, new grad, junior, associate...). **Both must match.**
- `BLOCK` — kills senior / staff / manager / PhD-only titles that slip past `LEVEL`.
- `LOCATIONS` — `[]` means anywhere. Set `["india","bangalore","bengaluru","remote"]` to narrow.

Missing roles you'd have wanted? Drop the `LEVEL.search(title)` check in `wanted()` — you'll
see far more rows, including untitled-level postings that are secretly entry-level.
