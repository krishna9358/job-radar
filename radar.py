#!/usr/bin/env python3
"""Fetch entry-level jobs from company ATS APIs. Stdlib only, no deps, no keys.

Usage: python3 radar.py   ->  writes jobs.json + index.html
"""
import json, re, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"

# --- what counts as "a job for me" -------------------------------------------
ROLE = re.compile(r"""
    software|sde\b|swe\b|full.?stack|backend|back.end|frontend|front.end|
    applied.scientist|research.scientist|machine.learning|\bml\b|\bai\b|
    data.scien|data.engineer|data.analyst|analytics|
    developer|engineer|programmer|quant
""", re.I | re.X)

LEVEL = re.compile(r"""
    intern|internship|new.grad|new.graduate|graduate\b|university|campus|
    entry.level|early.career|apprentice|trainee|junior|\bjr\b|associate|
    \bi\b|\b1\b|\b2026\b|\b2027\b
""", re.I | re.X)

# Kills senior/manager/PhD-only titles that sneak past LEVEL
BLOCK = re.compile(r"senior|staff|principal|lead\b|manager|director|\bphd\b|"
                   r"vp\b|head of|architect|\bii+\b|\b[3-9]\b", re.I)

# Empty = anywhere. Example: ["india", "bangalore", "bengaluru", "remote"]
LOCATIONS: list[str] = []


def http(url, data=None, headers=None):
    req = urllib.request.Request(
        url, data=json.dumps(data).encode() if data is not None else None,
        headers={"User-Agent": UA, "Accept": "application/json",
                 **({"Content-Type": "application/json"} if data is not None else {}),
                 **(headers or {})})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.load(r)


# --- one fetcher per ATS; each returns [(title, location, url), ...] ---------
def greenhouse(c):
    return [(j["title"], j["location"]["name"], j["absolute_url"])
            for j in http(f"https://boards-api.greenhouse.io/v1/boards/{c['token']}/jobs")["jobs"]]


def lever(c):
    return [(j["text"], j["categories"].get("location") or "", j["hostedUrl"])
            for j in http(f"https://api.lever.co/v0/postings/{c['token']}?mode=json")]


def ashby(c):
    return [(j["title"], j.get("location") or "", j["jobUrl"])
            for j in http(f"https://api.ashbyhq.com/posting-api/job-board/{c['token']}")["jobs"]]


def smartrecruiters(c):
    d = http(f"https://api.smartrecruiters.com/v1/companies/{c['token']}/postings?limit=100")
    return [(j["name"], (j.get("location") or {}).get("city") or "",
             f"https://jobs.smartrecruiters.com/{c['token']}/{j['id']}") for j in d["content"]]


def workday(c):
    url = f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}/jobs"
    base = f"https://{c['host']}/{c['site']}"
    out, offset = [], 0
    while offset < 600:  # ponytail: 600 is plenty post-filter; raise if you miss postings
        posts = http(url, {"appliedFacets": {}, "limit": 20, "offset": offset,
                           "searchText": ""}).get("jobPostings") or []
        if not posts:
            break
        # some tenants return postings with no title/path — skip the row, keep the feed
        out += [(p.get("title") or "", p.get("locationsText") or "",
                 base + p["externalPath"]) for p in posts if p.get("externalPath")]
        offset += 20
    return out


def amazon(c):
    # amazon.jobs ANDs the words in base_query, so run one query per role family
    out = {}
    for q in ("software+development+engineer", "intern", "applied+scientist",
              "data+scientist", "front+end+engineer"):
        offset = 0
        while offset < 300:
            jobs = http(f"https://www.amazon.jobs/en/search.json"
                        f"?result_limit=100&offset={offset}&base_query={q}").get("jobs") or []
            if not jobs:
                break
            for j in jobs:
                out[j["job_path"]] = (j["title"], j.get("location") or "",
                                      "https://www.amazon.jobs" + j["job_path"])
            offset += 100
    return list(out.values())


def uber(c):
    out, page = [], 0
    while page < 8:
        d = http("https://www.uber.com/api/loadSearchJobsResults?localeCode=en",
                 {"params": {}, "page": page, "limit": 100}, {"x-csrf-token": "x"})
        res = (d.get("data") or {}).get("results") or []
        if not res:
            break
        for j in res:
            loc = j.get("location") or {}
            out.append((j["title"],
                        ", ".join(filter(None, [loc.get("city"), loc.get("countryName")])),
                        f"https://www.uber.com/global/en/careers/list/{j['id']}/"))
        page += 1
    return out


def atlassian(c):
    return [(j["title"], "; ".join(j.get("locations") or []),
             (j.get("portalJobPost") or {}).get("portalUrl") or j.get("applyUrl") or "")
            for j in http("https://www.atlassian.com/endpoint/careers/listings")]


def oracle(c):
    out, offset = [], 0
    while offset < 800:
        d = http("https://eeho.fa.us2.oraclecloud.com/hcmRestApi/resources/latest/"
                 "recruitingCEJobRequisitions?onlyData=true&expand=requisitionList"
                 f"&finder=findReqs;siteNumber=CX_1,limit=200,offset={offset}")
        reqs = (d.get("items") or [{}])[0].get("requisitionList") or []
        if not reqs:
            break
        out += [(r["Title"], r.get("PrimaryLocation") or "",
                 "https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/"
                 + str(r["Id"])) for r in reqs]
        offset += 200
    return out


def eightfold(c):
    """Eightfold AI boards: Netflix, Nutanix, Chegg, Vimeo, ... (host + domain in config)"""
    out, start = [], 0
    while start < 1000:  # ponytail: server caps the page at 10 regardless of num, so step by len
        pos = http(f"https://{c['host']}/api/apply/v2/jobs"
                   f"?domain={c['domain']}&start={start}&num=100").get("positions") or []
        if not pos:
            break
        out += [(p["name"], "; ".join(p.get("locations") or []),
                 p.get("canonicalPositionUrl") or "") for p in pos]
        start += len(pos)
    return out


def microsoft(c):
    out, pg = [], 1
    while pg <= 10:
        d = http("https://gcsservices.careers.microsoft.com/search/api/v1/search"
                 f"?q=&l=en_us&pg={pg}&pgSz=20&o=Relevance&flt=true",
                 headers={"Referer": "https://jobs.careers.microsoft.com/"})
        jobs = ((d.get("operationResult") or {}).get("result") or {}).get("jobs") or []
        if not jobs:
            break
        for j in jobs:
            locs = j.get("properties", {}).get("locations") or [j.get("properties", {}).get("primaryLocation")]
            out.append((j["title"], "; ".join(filter(None, locs)),
                        f"https://jobs.careers.microsoft.com/global/en/job/{j['jobId']}"))
        pg += 1
    return out


ATS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "amazon": amazon,
       "workday": workday, "smartrecruiters": smartrecruiters, "uber": uber,
       "atlassian": atlassian, "oracle": oracle, "eightfold": eightfold,
       "microsoft": microsoft}


def wanted(title, location):
    if not (ROLE.search(title) and LEVEL.search(title)) or BLOCK.search(title):
        return False
    return not LOCATIONS or any(l in location.lower() for l in LOCATIONS)


def kind(title):
    t = title.lower()
    if re.search(r"intern(ship)?\b", t):
        return "Internship"
    if re.search(r"new.grad|new.graduate|university|campus|graduate\b|early.career", t):
        return "New Grad"
    return "Entry Level"


def pull(c):
    """-> (company, rows, error). Never raises."""
    fetch = ATS.get(c["ats"])
    if not fetch:
        return c, [], f"unknown ats '{c['ats']}'"
    try:
        # drop rows missing a title or url before anything downstream trusts them
        return c, [(t, l, u) for t, l, u in fetch(c) if t and u], None
    except Exception as e:
        return c, [], f"{type(e).__name__}: {e}"


def main():
    companies = json.loads((HERE / "companies.json").read_text())
    old = {}
    if (HERE / "jobs.json").exists():
        old = {j["url"]: j for j in json.loads((HERE / "jobs.json").read_text())}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    found, failed = {}, []

    with ThreadPoolExecutor(8) as ex:
        for c, rows, err in ex.map(pull, companies):
            if err:
                failed.append(f"{c['name']} ({err.split(':')[0]})")
                print(f"  {c['name']:<14} FAILED  {err[:70]}", file=sys.stderr)
                continue
            hits = [(t, l, u) for t, l, u in rows if u and wanted(t, l)]
            for t, l, u in hits:
                found[u] = {"company": c["name"], "title": t, "location": l, "url": u,
                            "kind": kind(t),
                            "first_seen": old.get(u, {}).get("first_seen", today)}
            print(f"  {c['name']:<14} {len(hits):>4} / {len(rows)}")

    jobs = sorted(found.values(), key=lambda j: (j["first_seen"], j["company"], j["title"]),
                  reverse=True)
    new = sum(1 for j in jobs if j["url"] not in old)

    (HERE / "jobs.json").write_text(json.dumps(jobs, indent=1))
    (HERE / "index.html").write_text(
        TEMPLATE.replace("__DATA__", json.dumps(jobs, separators=(",", ":")))
                .replace("__NEW__", json.dumps([j["url"] for j in jobs if j["url"] not in old]))
                .replace("__UPDATED__", today)
                .replace("__FAILED__", json.dumps(failed))
                .replace("__COMPANIES__", str(len(companies) - len(failed))))
    print(f"\n{len(jobs)} matching jobs · {new} new · "
          f"{len(companies) - len(failed)}/{len(companies)} feeds OK")
    if failed:
        print("failed: " + ", ".join(failed))


TEMPLATE = r"""<!doctype html><html lang=en><meta charset=utf-8>
<title>Job Radar</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
:root{
  --bg:#fbfbfa; --panel:#fff; --fg:#1a1a19; --dim:#6b6b68; --line:#e6e5e1;
  --accent:#2f6fe4; --new:#d9480f; --newbg:#fff2e8; --chip:#f2f1ee; --shadow:0 1px 2px #0000000d;
}
@media(prefers-color-scheme:dark){:root{
  --bg:#141413; --panel:#1c1c1a; --fg:#eeeeec; --dim:#9a9a95; --line:#2e2e2b;
  --accent:#7ab0ff; --new:#ff8a4c; --newbg:#3a220f; --chip:#262624; --shadow:none;
}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
 -webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:0 1rem 4rem}
header{border-bottom:1px solid var(--line);background:var(--panel);margin-bottom:1.5rem}
header .wrap{padding-top:1.75rem;padding-bottom:1.5rem}
h1{margin:0;font-size:1.6rem;letter-spacing:-.02em;font-weight:650}
h1 span{color:var(--accent)}
.sub{color:var(--dim);font-size:.85rem;margin-top:.3rem}
.stats{display:flex;gap:1.75rem;margin-top:1.1rem;flex-wrap:wrap}
.stat b{display:block;font-size:1.5rem;font-weight:650;letter-spacing:-.02em;line-height:1.1}
.stat span{font-size:.72rem;color:var(--dim);text-transform:uppercase;letter-spacing:.06em}
.controls{display:flex;gap:.6rem;flex-wrap:wrap;margin-bottom:1.1rem}
input,select{font:inherit;font-size:.9rem;padding:.55rem .7rem;border:1px solid var(--line);
 border-radius:8px;background:var(--panel);color:var(--fg);box-shadow:var(--shadow)}
input{flex:1 1 260px;min-width:0}
input:focus,select:focus{outline:2px solid var(--accent);outline-offset:-1px;border-color:transparent}
.chips{display:flex;gap:.4rem;flex-wrap:wrap;margin-bottom:1.25rem}
.chip{font:inherit;font-size:.8rem;padding:.35rem .75rem;border-radius:99px;cursor:pointer;
 border:1px solid var(--line);background:var(--chip);color:var(--dim)}
.chip[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:550}
ul{list-style:none;margin:0;padding:0;border:1px solid var(--line);border-radius:12px;
 overflow:hidden;background:var(--panel);box-shadow:var(--shadow)}
li{display:grid;grid-template-columns:1fr auto;gap:.15rem 1rem;padding:.8rem 1rem;
 border-bottom:1px solid var(--line);align-items:baseline}
li:last-child{border-bottom:0}
li:hover{background:var(--chip)}
.t{font-weight:550;color:var(--fg);text-decoration:none;letter-spacing:-.01em}
.t:hover{color:var(--accent);text-decoration:underline}
.meta{grid-column:1;color:var(--dim);font-size:.82rem;display:flex;gap:.5rem;flex-wrap:wrap}
.co{color:var(--fg);font-weight:500}
.tag{grid-column:2;grid-row:1/3;font-size:.7rem;color:var(--dim);white-space:nowrap;
 border:1px solid var(--line);padding:.15rem .5rem;border-radius:99px;align-self:center}
.badge{background:var(--newbg);color:var(--new);border:0;font-weight:650;letter-spacing:.04em;
 padding:.1rem .4rem;border-radius:4px;font-size:.65rem;margin-right:.4rem}
.empty{padding:3rem 1rem;text-align:center;color:var(--dim)}
.warn{font-size:.78rem;color:var(--dim);margin-top:1.5rem}
@media(max-width:560px){.tag{display:none}li{grid-template-columns:1fr}}
</style>
<header><div class=wrap>
  <h1>Job <span>Radar</span></h1>
  <div class=sub>Entry-level roles pulled straight from company hiring APIs · updated __UPDATED__</div>
  <div class=stats>
    <div class=stat><b id=sTotal>0</b><span>Open roles</span></div>
    <div class=stat><b id=sNew>0</b><span>New today</span></div>
    <div class=stat><b>__COMPANIES__</b><span>Companies</span></div>
    <div class=stat><b id=sShown>0</b><span>Showing</span></div>
  </div>
</div></header>
<div class=wrap>
  <div class=controls>
    <input id=q placeholder="Search role, company or city — e.g. applied scientist bangalore" autofocus>
    <select id=co><option value="">All companies</option></select>
    <select id=lo><option value="">All locations</option></select>
  </div>
  <div class=chips>
    <button class=chip data-k="" aria-pressed=true>All</button>
    <button class=chip data-k="Internship" aria-pressed=false>Internship</button>
    <button class=chip data-k="New Grad" aria-pressed=false>New Grad</button>
    <button class=chip data-k="Entry Level" aria-pressed=false>Entry Level</button>
    <button class=chip id=onlyNew data-k="new" aria-pressed=false>New today</button>
  </div>
  <ul id=list></ul>
  <p class=warn id=warn></p>
</div>
<script>
const JOBS=__DATA__, NEW=new Set(__NEW__), FAILED=__FAILED__;
JOBS.forEach(j=>j._s=(j.company+' '+j.title+' '+j.location+' '+j.kind).toLowerCase());

const REGIONS=[['India','india|bangalore|bengaluru|hyderabad|pune|gurgaon|gurugram|chennai|mumbai|delhi|noida'],
 ['United States','united states|, ca|, wa|, ny|, tx|usa|seattle|new york|san francisco|austin|boston'],
 ['Remote','remote'],['Canada','canada|toronto|vancouver|montreal'],
 ['Europe','united kingdom|london|ireland|dublin|germany|berlin|netherlands|amsterdam|poland|warsaw'],
 ['APAC','singapore|japan|tokyo|australia|sydney|korea|china|taiwan']];
const region=l=>(REGIONS.find(([,re])=>new RegExp(re,'i').test(l))||[''])[0];

for(const c of [...new Set(JOBS.map(j=>j.company))].sort())
  co.add(new Option(c+' ('+JOBS.filter(j=>j.company===c).length+')',c));
for(const [r] of REGIONS) if(JOBS.some(j=>region(j.location)===r))
  lo.add(new Option(r+' ('+JOBS.filter(j=>region(j.location)===r).length+')',r));

let kind='', newOnly=false;
const esc=s=>s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]);

function draw(){
  const w=q.value.toLowerCase().split(/\s+/).filter(Boolean);
  const hits=JOBS.filter(j=>
    (!kind||j.kind===kind) && (!newOnly||NEW.has(j.url)) &&
    (!co.value||j.company===co.value) && (!lo.value||region(j.location)===lo.value) &&
    w.every(t=>j._s.includes(t)));
  sShown.textContent=hits.length;
  list.innerHTML = hits.length ? hits.map(j=>
    `<li><a class=t href="${esc(j.url)}" target=_blank rel=noopener>`+
    (NEW.has(j.url)?'<span class=badge>NEW</span>':'')+esc(j.title)+`</a>`+
    `<div class=meta><span class=co>${esc(j.company)}</span><span>${esc(j.location||'—')}</span>`+
    `<span>${j.first_seen}</span></div><span class=tag>${j.kind}</span></li>`).join('')
    : '<li class=empty>No roles match. Try clearing a filter.</li>';
}
for(const b of document.querySelectorAll('.chip')) b.onclick=()=>{
  if(b.id==='onlyNew'){newOnly=!newOnly;b.ariaPressed=newOnly;}
  else{kind=b.dataset.k;
    document.querySelectorAll('.chip:not(#onlyNew)').forEach(x=>x.ariaPressed=x===b);}
  draw();
};
q.oninput=co.onchange=lo.onchange=draw;
sTotal.textContent=JOBS.length; sNew.textContent=NEW.size;
if(FAILED.length) warn.textContent='Feeds that failed this run: '+FAILED.join(', ');
draw();
</script>
</html>"""


if __name__ == "__main__":
    main()
