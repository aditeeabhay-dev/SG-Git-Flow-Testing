import os, re, requests, sys, json

CONFLUENCE_BASE = os.environ['CONFLUENCE_BASE_URL']
EMAIL           = os.environ['CONFLUENCE_EMAIL']
API_TOKEN       = os.environ['CONFLUENCE_API_TOKEN']
PR_BODY         = os.environ.get('PR_BODY', '')

print(f"DEBUG PR_BODY: '{PR_BODY[:200]}'")

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

# ── 3. Recursively extract all taskItems from ADF JSON ──
def extract_tasks(node, tasks=[]):
    if isinstance(node, dict):
        if node.get('type') == 'taskItem':
            state = node.get('attrs', {}).get('state', 'TODO')
            # Extract text from content
            text = ''
            for child in node.get('content', []):
                if child.get('type') == 'text':
                    text += child.get('text', '')
            tasks.append({'text': text.strip(), 'done': state == 'DONE'})
        for value in node.values():
            extract_tasks(value, tasks)
    elif isinstance(node, list):
        for item in node:
            extract_tasks(item, tasks)
    return tasks

# ── 4. Define required checked checkboxes ──
# These are the label texts of checkboxes that MUST be ticked
REQUIRED_CHECKED = [
    "BE",   # Approvals table — BE checkbox must be ticked
]

# ── 5. Run checks ──
def check(data):
    errors = []

    if data.get('status') == 'draft':
        errors.append('Page is still a DRAFT — publish it before merging')

    adf_body = data.get('body', {}).get('atlas_doc_format', {}).get('value', '{}')
    adf_json = json.loads(adf_body) if isinstance(adf_body, str) else adf_body

    tasks = extract_tasks(adf_json, [])
    print(f"DEBUG found {len(tasks)} tasks")

    for required in REQUIRED_CHECKED:
        # Find the task with matching text
        matches = [t for t in tasks if required.lower() in t['text'].lower()]
        if not matches:
            errors.append(f'Checkbox "{required}" not found in the page')
        elif not any(t['done'] for t in matches):
            errors.append(f'Checkbox "{required}" exists but is NOT ticked')
        else:
            print(f"✅ Checkbox '{required}' is ticked")

    return errors

# ── 6. Main ──
if __name__ == '__main__':
    data   = fetch_page()
    errors = check(data)

    if errors:
        print('\n❌ Confluence deployment doc check FAILED:')
        for e in errors:
            print(f'   • {e}')
        sys.exit(1)

    print('✅ All checks passed. Merge is unblocked.')
