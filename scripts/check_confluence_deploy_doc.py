import os, re, requests, sys, json

CONFLUENCE_BASE = os.environ['CONFLUENCE_BASE_URL']
EMAIL           = os.environ['CONFLUENCE_EMAIL']
API_TOKEN       = os.environ['CONFLUENCE_API_TOKEN']
PR_BODY         = os.environ.get('PR_BODY', '')
PR_AUTHOR       = os.environ.get('PR_AUTHOR', '').lower()
BYPASS_KEYWORD  = "HOTFIX-BYPASS"

print(f"DEBUG PR_BODY: '{PR_BODY[:200]}'")

# Only these GitHub usernames can use the bypass
BYPASS_ALLOWED_USERS = [
    "aditeeabhay-dev",
    "jane-doe",
    "engineering-lead",
]

if BYPASS_KEYWORD in PR_BODY.upper():
    if PR_AUTHOR in [u.lower() for u in BYPASS_ALLOWED_USERS]:
        print(f"⚠️  HOTFIX-BYPASS used by @{PR_AUTHOR} — skipping Confluence checks")
        sys.exit(0)
    else:
        print(f"❌ HOTFIX-BYPASS keyword found but @{PR_AUTHOR} is not authorised to bypass")
        print(f"   Authorised users: {', '.join(BYPASS_ALLOWED_USERS)}")
        sys.exit(1)  # fails the check — bypass attempt is blocked and logged

# ── 1. Extract page ID from PR body ──
match = re.search(r'/pages/(\d+)', PR_BODY)
if not match:
    print("❌ No Confluence URL found in PR description!")
    sys.exit(1)

PAGE_ID = match.group(1)
print(f"DEBUG PAGE_ID: {PAGE_ID}")

# ── 2. Fetch page in ADF (JSON) format ──
def fetch_page():
    url = f'{CONFLUENCE_BASE}/wiki/rest/api/content/{PAGE_ID}?expand=body.atlas_doc_format,status'
    r = requests.get(url, auth=(EMAIL, API_TOKEN))
    r.raise_for_status()
    return r.json()

# ── 3. Helper: extract plain text from any ADF node ──
def get_text(node):
    text = ''
    for child in node.get('content', []):
        if child.get('type') == 'text':
            text += child.get('text', '')
        else:
            text += get_text(child)
    return text

# ── 4. Helper: recursively extract all taskItems from a node ──
def extract_tasks(node, tasks=None):
    if tasks is None:
        tasks = []
    if isinstance(node, dict):
        if node.get('type') == 'taskItem':
            state = node.get('attrs', {}).get('state', 'TODO')
            text = get_text(node).strip()
            tasks.append({'text': text, 'done': state == 'DONE'})
        for value in node.values():
            if isinstance(value, (dict, list)):
                extract_tasks(value, tasks)
    elif isinstance(node, list):
        for item in node:
            extract_tasks(item, tasks)
    return tasks

# ── 5. Navigate: Testing and Approvals → Approvals heading → table ──
def find_approval_tasks(adf_json):
    top_level = adf_json.get('content', [])

    # Step 1: Find the 'Testing and Approvals' expand block
    testing_section = None
    for node in top_level:
        if node.get('type') == 'expand':
            title = node.get('attrs', {}).get('title', '')
            if 'testing and approvals' in title.lower():
                testing_section = node
                break

    if not testing_section:
        return None, "Could not find 'Testing and Approvals' section"

    # Step 2: Inside that section, find the 'Approvals' heading
    section_content = testing_section.get('content', [])
    capture_next_table = False

    for node in section_content:
        if node.get('type') == 'heading':
            heading_text = get_text(node).strip()
            if heading_text.lower() == 'approvals':
                capture_next_table = True
                continue

        # Step 3: Grab the table immediately after that heading
        if capture_next_table and node.get('type') == 'table':
            tasks = extract_tasks(node)
            return tasks, None

        # Stop if we hit another heading before finding a table
        if capture_next_table and node.get('type') == 'heading':
            break

    return None, "Could not find 'Approvals' table inside 'Testing and Approvals' section"

# ── 6. Run all checks ──
def check(data):
    errors = []

    if data.get('status') == 'draft':
        errors.append('Page is still a DRAFT — publish it before merging')

    adf_body = data.get('body', {}).get('atlas_doc_format', {}).get('value', '{}')
    adf_json = json.loads(adf_body) if isinstance(adf_body, str) else adf_body

    approval_tasks, err = find_approval_tasks(adf_json)

    if err:
        errors.append(f'Structure error: {err}')
        return errors

    print(f"DEBUG: Tasks found in Approvals table: {approval_tasks}")

    REQUIRED_APPROVALS = ["BE", "QA"]  # add "FE", "QA" etc. when needed

    for platform in REQUIRED_APPROVALS:
        matches = [t for t in approval_tasks if t['text'].strip().lower() == platform.lower()]
        if not matches:
            errors.append(f'Approvals table: "{platform}" checkbox not found')
        elif not any(t['done'] for t in matches):
            errors.append(f'Approvals table: "{platform}" is NOT ticked')
        else:
            print(f"✅ Approvals table: '{platform}' is ticked")

    return errors

# ── 7. Main ──
if __name__ == '__main__':
    data   = fetch_page()
    errors = check(data)

    if errors:
        print('\n❌ Confluence deployment doc check FAILED:')
        for e in errors:
            print(f'   • {e}')
        sys.exit(1)

    print('✅ All checks passed. Merge is unblocked.')
