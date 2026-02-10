#!/usr/bin/env python3
"""
LinkedIn Devlog poster (GitHub Actions friendly, stdlib only)

Required env:
  LINKEDIN_AUTHOR_URN      e.g. urn:li:person:xxxx
  LINKEDIN_ACCESS_TOKEN    OAuth access token

Optional env:
  LINKEDIN_VERSION         YYYYMM, default 202601 (needed for /rest/posts)
  LINKEDIN_VISIBILITY      PUBLIC (default) or CONNECTIONS
  LINKEDIN_POST_MODE       auto|posts|ugc   (default auto)
  DRY_RUN                  1 to not post
  GITHUB_REPO              owner/name (for links)
  BEFORE_SHA               push "before" SHA
  AFTER_SHA                push "after" SHA
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from typing import List, Tuple, Optional


SECRET_PATTERNS = re.compile(
    r"(AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|BEGIN (RSA |EC )?PRIVATE KEY)",
    re.IGNORECASE,
)

DEP_ONLY_FILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "requirements.txt",
}


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr, flush=True)


def sh(cmd: List[str], cwd: str) -> str:
    return subprocess.check_output(cmd, cwd=cwd, text=True).strip()


def git_has_commit(sha: str, cwd: str) -> bool:
    try:
        subprocess.check_call(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def is_all_zeros_sha(sha: str) -> bool:
    return bool(sha) and set(sha) == {"0"}


def collect_push_summary(repo_path: str, before: str, after: str) -> Tuple[List[str], List[str], str]:
    """Return (commit_subjects, changed_files, shortstat)."""

    after_ok = bool(after) and re.fullmatch(r"[0-9a-f]{40}", after or "") and git_has_commit(after, repo_path)
    before_ok = (
        bool(before)
        and re.fullmatch(r"[0-9a-f]{40}", before or "")
        and (not is_all_zeros_sha(before))
        and git_has_commit(before, repo_path)
    )

    if after_ok and before_ok:
        rev_range = f"{before}..{after}"
        subjects = sh(["git", "log", "--format=%s", rev_range], repo_path).splitlines()
        files = sh(["git", "diff", "--name-only", rev_range], repo_path).splitlines()
        shortstat = sh(["git", "diff", "--shortstat", rev_range], repo_path)
        return subjects, files, shortstat

    # Fallback for first push / shallow history / weird before SHA:
    if after_ok:
        subjects = [sh(["git", "log", "-1", "--format=%s", after], repo_path)]
        files = sh(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", after], repo_path).splitlines()
        # shortstat for a single commit
        shortstat = sh(["git", "show", "--shortstat", "--format=", after], repo_path).splitlines()[-1] if files else ""
        return subjects, files, shortstat

    # Last resort: HEAD
    subjects = [sh(["git", "log", "-1", "--format=%s"], repo_path)]
    files = sh(["git", "show", "--name-only", "--format=", "HEAD"], repo_path).splitlines()
    shortstat = sh(["git", "show", "--shortstat", "--format=", "HEAD"], repo_path).splitlines()[-1] if files else ""
    return subjects, files, shortstat


def looks_doc_only(files: List[str]) -> bool:
    if not files:
        return True
    def is_doc(f: str) -> bool:
        lf = f.lower()
        return lf.endswith(".md") or lf.startswith("docs/") or lf in {"license", "license.md", "readme", "readme.md"}
    return all(is_doc(f) for f in files)


def looks_dependency_only(files: List[str]) -> bool:
    if not files:
        return False
    basenames = {os.path.basename(f) for f in files}
    return basenames.issubset(DEP_ONLY_FILES)


def looks_merge_only(subjects: List[str]) -> bool:
    if not subjects:
        return False
    return all(s.lower().startswith("merge") for s in subjects)


def build_post_text(repo: str, subjects: List[str], shortstat: str, link_url: str) -> str:
    top = [s.strip() for s in subjects if s.strip()][:3]
    bullets = "\n".join(f"• {s}" for s in top) if top else "• Updates"
    stat_line = f"\n\n{shortstat}" if shortstat else ""
    return (
        f"Dev update from {repo}:\n\n"
        f"{bullets}"
        f"{stat_line}\n\n"
        f"{link_url}"
    )


def summarize_with_openai(style: str, repo: str, subjects: List[str], files: List[str], shortstat: str, link_url: str, diff_excerpt: str) -> str:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return ""

    model = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()

    prompt = {
        "repo": repo,
        "commit_subjects": subjects[:10],
        "changed_files": files[:30],
        "shortstat": shortstat,
        "diff_excerpt": diff_excerpt[:12000],
        "link": link_url,
    }

    payload = {
        "model": model,
        "input": [
            {"role": "system", "content": f"You are rewriting a LinkedIn devlog post.\n\nSTYLE GUIDE:\n{style}"},
            {"role": "user", "content": f"Rewrite this as a short LinkedIn post.\n\nDATA:\n{json.dumps(prompt, indent=2)}"},
        ],
    }

    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        out = json.load(r)

    # Responses API returns output text in a structured way; this covers common cases.
    for item in out.get("output", []):
        for c in item.get("content", []):
            if c.get("type") == "output_text" and c.get("text"):
                return c["text"].strip()
    return ""


def request_json(url: str, token: str, method: str = "GET", body: Optional[dict] = None, headers: Optional[dict] = None) -> Tuple[int, dict, str]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    h = {"Authorization": f"Bearer {token}"}
    if headers:
        h.update(headers)
    if body is not None:
        h.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode("utf-8", "replace")
            return r.status, dict(r.headers), txt
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", "replace")
        eprint(f"DEBUG HTTPError {method} {url}: {e.code} {e.reason}")
        eprint("DEBUG response body:", err)
        raise


def post_via_rest_posts(token: str, author: str, text: str, visibility: str, version: str) -> None:
    # Posts API requires Linkedin-Version + X-Restli-Protocol-Version headers
    # POST https://api.linkedin.com/rest/posts
    body = {
        "author": author,
        "commentary": text,
        "visibility": visibility,
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    status, headers, _ = request_json(
        "https://api.linkedin.com/rest/posts",
        token,
        method="POST",
        body=body,
        headers={
            "X-Restli-Protocol-Version": "2.0.0",
            "Linkedin-Version": version,
        },
    )
    eprint("Posted via /rest/posts:", status, "x-restli-id:", headers.get("x-restli-id"))


def post_via_v2_ugc(token: str, author: str, text: str, link_url: str, visibility: str, version: str) -> None:
    # Share-on-LinkedIn guide uses UGC API:
    # POST https://api.linkedin.com/v2/ugcPosts
    body = {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "ARTICLE",
                "media": [{"status": "READY", "originalUrl": link_url}],
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": visibility},
    }

    headers = {
        "X-Restli-Protocol-Version": "2.0.0",
        # Not always required for v2 endpoints, but harmless and helps with versioned APIs.
        "Linkedin-Version": version,
    }

    status, headers_out, _ = request_json(
        "https://api.linkedin.com/v2/ugcPosts",
        token,
        method="POST",
        body=body,
        headers=headers,
    )
    eprint("Posted via /v2/ugcPosts:", status, "x-restli-id:", headers_out.get("x-restli-id"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-path", default=".", help="Path to checked-out target repo")
    args = ap.parse_args()
    repo_path = os.path.abspath(args.repo_path)

    author = (os.getenv("LINKEDIN_AUTHOR_URN") or "").strip()
    token = (os.getenv("LINKEDIN_ACCESS_TOKEN") or "").strip()
    if not author or not token:
        eprint("Missing LINKEDIN_AUTHOR_URN or LINKEDIN_ACCESS_TOKEN")
        return 1

    repo = (os.getenv("GITHUB_REPO") or "").strip() or os.path.basename(repo_path)
    before = (os.getenv("BEFORE_SHA") or "").strip()
    after = (os.getenv("AFTER_SHA") or "").strip()
    visibility = (os.getenv("LINKEDIN_VISIBILITY") or "PUBLIC").strip()
    version = (os.getenv("LINKEDIN_VERSION") or "202601").strip()
    mode = (os.getenv("LINKEDIN_POST_MODE") or "ugc").strip().lower()
    dry_run = (os.getenv("DRY_RUN") or "").strip() in {"1", "true", "yes"}

    # Safe diagnostics (no token leak)
    fp = hashlib.sha256(token.encode("utf-8")).hexdigest()[:10]
    eprint("DEBUG token_len:", len(token), "sha256_prefix:", fp)
    eprint("DEBUG author:", author)
    eprint("DEBUG mode:", mode, "visibility:", visibility, "version:", version)

    # Token sanity check (you said local /userinfo works; this confirms it in Actions too)
    status, _, _ = request_json(
        "https://api.linkedin.com/v2/userinfo",
        token,
        method="GET",
        headers={},  # Authorization already applied
    )
    eprint("DEBUG /v2/userinfo status:", status)

    subjects, files, shortstat = collect_push_summary(repo_path, before, after)

    if SECRET_PATTERNS.search("\n".join(subjects)):
        eprint("Skip: possible secret-like pattern found in commit subjects.")
        return 0

    if looks_doc_only(files):
        eprint("Skip: doc-only change set.")
        return 0

    if looks_merge_only(subjects):
        eprint("Skip: merge-only commits.")
        return 0

    if looks_dependency_only(files):
        eprint("Skip: dependency-only changes.")
        return 0

    if not files:
        eprint("Skip: no changed files detected.")
        return 0

    # Build link: compare when possible, otherwise commit link
    if before and after and not is_all_zeros_sha(before):
        link_url = f"https://github.com/{repo}/compare/{before}...{after}"
    elif after:
        link_url = f"https://github.com/{repo}/commit/{after}"
    else:
        link_url = f"https://github.com/{repo}"

    fallback = build_post_text(repo, subjects, shortstat, link_url)

    style_path = os.getenv("STYLE_GUIDE_PATH") or os.path.join(os.path.dirname(__file__), "..", "prompts", "style_guide.txt")
    try:
        with open(style_path, "r", encoding="utf-8") as f:
            style = f.read()
    except FileNotFoundError:
        style = ""
    
    ai = summarize_with_openai(style, repo, subjects, files, shortstat, link_url, diff_excerpt="")
    text = ai or fallback


    if dry_run:
        eprint("DRY_RUN=1, not posting. Text would be:\n", text)
        return 0

    # Try posting
    try:
        if mode == "posts":
            post_via_rest_posts(token, author, text, visibility, version)
        elif mode == "ugc":
            post_via_v2_ugc(token, author, text, link_url, visibility, version)
        else:
            # auto: try Posts API first, then UGC share
            try:
                post_via_rest_posts(token, author, text, visibility, version)
            except urllib.error.HTTPError:
                eprint("DEBUG fallback: /rest/posts failed, trying /v2/ugcPosts...")
                post_via_v2_ugc(token, author, text, link_url, visibility, version)
    except urllib.error.HTTPError:
        # request_json already printed the response body
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
