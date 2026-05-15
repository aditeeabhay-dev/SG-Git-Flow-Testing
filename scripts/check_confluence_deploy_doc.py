import os, re, requests, sys, json
from datetime import datetime, timezone

# ── Environment variables ──
CONFLUENCE_BASE      = os.environ['CONFLUENCE_BASE_URL']
EMAIL                = os.environ['CONFLUENCE_EMAIL']
API_TOKEN            = os.environ['CONFLUENCE_API_TOKEN']
PR_BODY              = os.environ.get('PR_BODY', '')
PR_AUTHOR            = os.environ.get('PR_AUTHOR', '').lower()
TRIGGERED_BY         = os.environ.get('TRIGGERED_BY', 'pull_request')
COMMENT_BODY         = os.environ.get('COMMENT_BODY', '')
COMMENT_AUTHOR       = os.environ.get('COMMENT_AUTHOR', '').lower()
PR_NUMBER            = os.environ.get('PR_NUMBER', '')
PR_SHA               = os.environ.get('PR_SHA', '')
GITHUB_TOKEN         = os.environ.get('GITHUB_TOKEN', '')
REPO_FULL_NAME       = os.environ.get('REPO_FULL_NAME', '')
BYPASS_KEYWORD       = "HOTFIX-BYPASS"

raw_bypass           = os.environ.get('BYPASS_ALLOWED_USERS', '[]')
try:
    BYPASS_ALLOWED_USERS = [u.strip().lower() for u in json.loads(raw_bypass)]
except json.JSONDecodeError:
    BYPASS_ALLOWED_USERS = [u.strip().lower() for u in raw_bypass.split(',')]

# ── Determine source ──
if TRIGGERED_BY == 'issue_comment':
    SOURCE      = 'comment'
    SOURCE_TEXT = COMMENT_BODY
    ACTOR       = COMMENT_AUTHOR
else:
    SOURCE      = 'description'
    SOURCE_TEXT = PR_BODY
    ACTOR       = PR_AUTHOR

TIMESTAMP = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


# ── Post summary comment to PR ──
def post_comment(body):
    if not GITHUB_TOKEN or not PR_NUMBER or not REPO_FULL_NAME:
        print("Skipping summary comment — missing token, PR number or repo name")
        return
    url = f"https://api.github.com/repos/{REPO_FULL_NAME}/issues/{PR_NUMBER}/comments"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    r = requests.post(url, headers=headers, json={"body": body})
    print(f"Summary comment posted: {r.status_code}")


# ── Post commit status to PR head SHA ──
def post_commit_status(state, description):
    if not PR_SHA or not GITHUB_TOKEN or not REPO_FULL_NAME:
        print("Skipping commit status — missing SHA, token or repo name")
        return
    url = f"https://api.github.com/repos/{REPO_FULL_NAME}/statuses/{PR_SHA}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }
    payload = {
        "state": state,
        "context": "Confluence Doc Gate",
        "description": description[:140]
    }
    r = requests.post(url, headers=headers, json=payload)
    print(f"Commit status posted ({state}): {r.status_code}")


# ── Comment trigger: verify PR owner only ──
if TRIGGERED_BY == 'issue_comment':
    if COMMENT_AUTHOR != PR_AUTHOR:
        msg = (
            f"🤖 **Confluence Doc Gate — UNAUTHORISED TRIGGER** ❌\n\n"
            f"**@{COMMENT_AUTHOR}** posted `!rerun-gate` but only the PR owner "
            f"**@{PR_AUTHOR}** can trigger this.\n\n"
            f"*No checks were run.*"
        )
        post_comment(msg)
        post_commit_status("failure", f"Trigger rejected — @{COMMENT_AUTHOR} is not the PR owner")
        print(f"Trigger rejected — comment by @{COMMENT_AUTHOR}, PR owner is @{PR_AUTHOR}")
        sys.exit(1)


# ── Hotfix bypass check ──
if BYPASS_KEYWORD in SOURCE_TEXT.upper():
    if ACTOR in BYPASS_ALLOWED_USERS:
        msg = (
            f"🤖 **Confluence Doc Gate — BYPASSED** ⚠️\n\n"
            f"**Triggered by:** {SOURCE} by @{ACTOR}\n"
            f"**Bypass keyword:** `HOTFIX-BYPASS`\n"
            f"**Authorised by:** @{ACTOR}\n"
            f"**Time:** {TIMESTAMP}\n\n"
            f"*Confluence checks were skipped. This bypass is permanently recorded.*"
        )
        post_comment(msg)
        post_commit_status("success", f"HOTFIX-BYPASS authorised by @{ACTOR}")
        print(f"HOTFIX-BYPASS used by @{ACTOR} via {SOURCE} — skipping Confluence checks")
        sys.exit(0)
    else:
        msg = (
            f"🤖 **Confluence Doc Gate — FAILED** ❌\n\n"
            f"**Triggered by:** {SOURCE} by @{ACTOR}\n"
            f"`HOTFIX-BYPASS` keyword found but **@{ACTOR}** is not authorised to bypass.\n\n"
            f"*Contact your engineering lead to be added to the authorised bypass list.*"
        )
        post_comment(msg)
        post_commit_status("failure", f"Bypass attempted by unauthorised user @{ACTOR}")
        print(f"HOTFIX-BYPASS attempted by @{ACTOR} via {SOURCE} — not authorised")
        sys.exit(1)


# ── Extract Confluence page ID ──
match = re.search(r'/pages/(\d+)', SOURCE_TEXT)
if not match:
    msg = (
        f"🤖 **Confluence Doc Gate — FAILED** ❌\n\n"
        f"**Triggered by:** {SOURCE}\n"
        f"**Issues found:**\n"
        f"- ❌ No Confluence deployment doc URL found in {SOURCE}\n\n"
        f"*Paste your Confluence page URL in the PR description or post a "
        f"`!rerun-gate` comment with the URL.*"
    )
    post_comment(msg)
    post_commit_status("failure", f"No Confluence URL found in {SOURCE}")
    print(f"No Confluence URL found in {SOURCE}")
    sys.exit(1)

PAGE_ID = match.group(1)
confluence_url_match = re.search(r'https://[^\s]+/pages/\d+[^\s]*', SOURCE_TEXT)
CONFLUENCE_URL = confluence_url_match.group(0) if confluence_url_match else f"Page ID: {PAGE_ID}"


# ── Fetch Confluence page ──
def fetch_page():
    url = f'{CONFLUENCE_BASE}/wiki/rest/api/content/{PAGE_ID}?expand=body.atlas_doc_format,status'
    r = requests.get(url, auth=(EMAIL, API_TOKEN))
    r.raise_for_status()
    return r.json()


# ── ADF helpers ──
def get_text(node):
    text = ''
    for child in node.get('content', []):
        if child.get('type') == 'text':
            text += child.get('text', '')
        else:
            text += get_text(child)
    return text


def extract_tasks(node, tasks=None):
    if tasks is None:
        tasks = []
    if isinstance(node, dict):
        if node.get('type') == 'taskItem':
            state = node.get('attrs', {}).get('state', 'TODO')
            text  = get_text(node).strip()
            tasks.append({'text': text, 'done': state == 'DONE'})
        for value in node.values():
            if isinstance(value, (dict, list)):
                extract_tasks(value, tasks)
    elif isinstance(node, list):
        for item in node:
            extract_tasks(item, tasks)
    return tasks


def find_approval_tasks(adf_json):
    top_level = adf_json.get('content', [])
    testing_section = None
    for node in top_level:
        if node.get('type') == 'expand':
            title = node.get('attrs', {}).get('title', '')
            if 'testing and approvals' in title.lower():
                testing_section = node
                break
    if not testing_section:
        return None, "Could not find 'Testing and Approvals' section"
    section_content    = testing_section.get('content', [])
    capture_next_table = False
    for node in section_content:
        if node.get('type') == 'heading':
            if get_text(node).strip().lower() == 'approvals':
                capture_next_table = True
                continue
        if capture_next_table and node.get('type') == 'table':
            return extract_tasks(node), None
        if capture_next_table and node.get('type') == 'heading':
            break
    return None, "Could not find 'Approvals' table inside 'Testing and Approvals' section"


# ── Run checks ──
def check(data):
    errors  = []
    passing = []
    if data.get('status') == 'draft':
        errors.append('Page is still a DRAFT — publish it before merging')
    adf_body = data.get('body', {}).get('atlas_doc_format', {}).get('value', '{}')
    adf_json = json.loads(adf_body) if isinstance(adf_body, str) else adf_body
    approval_tasks, err = find_approval_tasks(adf_json)
    if err:
        errors.append(f'Structure error: {err}')
        return errors, passing
    REQUIRED_APPROVALS = ["BE", "QA"]
    for platform in REQUIRED_APPROVALS:
        matches = [t for t in approval_tasks if t['text'].strip().lower() == platform.lower()]
        if not matches:
            errors.append(f'"{platform}" checkbox not found in Approvals table')
        elif not any(t['done'] for t in matches):
            errors.append(f'"{platform}" approval is NOT ticked')
        else:
            passing.append(f'"{platform}" approval ticked')
    return errors, passing


# ── Main ──
if __name__ == '__main__':
    data            = fetch_page()
    errors, passing = check(data)

    check_lines = ""
    for p in passing:
        check_lines += f"- ✅ {p}\n"
    for e in errors:
        check_lines += f"- ❌ {e}\n"

    if errors:
        msg = (
            f"🤖 **Confluence Doc Gate — FAILED** ❌\n\n"
            f"**Triggered by:** {SOURCE} by @{ACTOR}\n"
            f"**Confluence doc:** {CONFLUENCE_URL}\n"
            f"**Checks:**\n{check_lines}\n"
            f"*Fix the above, then post `!rerun-gate` with your Confluence URL "
            f"as a comment to rerun.*"
        )
        post_comment(msg)
        post_commit_status("failure", "Confluence doc checks failed")
        print('Confluence deployment doc check FAILED:')
        for e in errors:
            print(f'  - {e}')
        sys.exit(1)

    msg = (
        f"🤖 **Confluence Doc Gate — PASSED** ✅\n\n"
        f"**Triggered by:** {SOURCE} by @{ACTOR}\n"
        f"**Confluence doc:** {CONFLUENCE_URL}\n"
        f"**Checks:**\n{check_lines}\n"
        f"*Merge is unblocked.*"
    )
    post_comment(msg)
    post_commit_status("success", "All Confluence doc checks passed")
    print('All checks passed. Merge is unblocked.')
