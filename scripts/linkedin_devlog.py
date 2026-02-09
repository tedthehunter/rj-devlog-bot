import json, os, subprocess, sys, urllib.parse, urllib.request

def sh(*cmd):
    return subprocess.check_output(cmd, text=True).strip()

before = os.getenv("BEFORE_SHA", "")
after  = os.getenv("AFTER_SHA", "")
repo   = os.getenv("GITHUB_REPO", "")
author = os.getenv("LINKEDIN_AUTHOR_URN", "")
vis    = os.getenv("LINKEDIN_VISIBILITY", "PUBLIC")

if not (before and after and repo and author):
    print("Missing env vars."); sys.exit(1)

compare_url = f"https://github.com/{repo}/compare/{before}...{after}"
rev_range = f"{before}..{after}"

# Collect push summary
subjects = sh("git", "log", "--format=%s", rev_range).splitlines()
files = sh("git", "diff", "--name-only", rev_range).splitlines()

# Meaningful-change filter (baseline)
doc_only = all(f.lower().endswith(".md") or f.startswith("docs/") for f in files) if files else True
merge_only = all(s.lower().startswith("merge") for s in subjects) if subjects else True

if doc_only or merge_only or not files:
    print("Skipping: not postable by rules."); sys.exit(0)

# Build post text
top = subjects[:3]
bullets = "\n".join([f"• {s}" for s in top])
text = (
    f"Dev update from {repo}:\n\n"
    f"{bullets}\n\n"
    f"Compare: {compare_url}"
)

def refresh_access_token():
    rt = os.getenv("LINKEDIN_REFRESH_TOKEN", "")
    cid = os.getenv("LINKEDIN_CLIENT_ID", "")
    cs  = os.getenv("LINKEDIN_CLIENT_SECRET", "")
    if not (rt and cid and cs):
        return None

    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": rt,
        "client_id": cid,
        "client_secret": cs,
    }).encode()

    req = urllib.request.Request(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    return payload.get("access_token")

token = refresh_access_token() or os.getenv("LINKEDIN_ACCESS_TOKEN", "")
if not token:
    print("No LinkedIn token available."); sys.exit(1)

# Post via UGC API (documented for personal shares)
body = {
    "author": author,
    "lifecycleState": "PUBLISHED",
    "specificContent": {
        "com.linkedin.ugc.ShareContent": {
            "shareCommentary": {"text": text},
            "shareMediaCategory": "ARTICLE",
            "media": [{"status": "READY", "originalUrl": compare_url}],
        }
    },
    "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": vis},
}

req = urllib.request.Request(
    "https://api.linkedin.com/v2/ugcPosts",
    data=json.dumps(body).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    },
    method="POST",
)

with urllib.request.urlopen(req, timeout=30) as r:
    print("Posted:", r.status)
