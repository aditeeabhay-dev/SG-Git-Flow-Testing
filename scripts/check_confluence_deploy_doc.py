import os, re, requests, sys
from bs4 import BeautifulSoup

CONFLUENCE_BASE = os.environ['CONFLUENCE_BASE_URL']
EMAIL           = os.environ['CONFLUENCE_EMAIL']
API_TOKEN       = os.environ['CONFLUENCE_API_TOKEN']
PR_BODY         = os.environ.get('PR_BODY', '')

# ── 1. Extract page ID from PR description ──
match = re.search(r'/pages/(\d+)', PR_BODY)
if not match:
    print("❌ No Confluence link found in PR description!")
    print("   Add a line like: Deployment Doc: https://yourcompany.atlassian.net/wiki/.../pages/123456")
    sys.exit(1)

PAGE_ID = match.group(1)

# ── 2. Define what must exist ──
REQUIRED_SECTIONS = [
    'Section Testing and Approvals',
]

REQUIRED_CHECKBOXES = [
    "BE",
]

# ── 3. Fetch the page ──
def fetch_page():
    url = f'{CONFLUENCE_BASE}/wiki/rest/api/content/{PAGE_ID}?expand=body.storage,version,status'
    r = requests.get(url, auth=(EMAIL, API_TOKEN))
    r.raise_for_status()
    return r.json()

# ── 4. Check a specific checkbox by its label ──
def check_checkbox(content, label):
    soup  = BeautifulSoup(content, 'html.parser')
    tasks = soup.find_all('ac:task')

    for task in tasks:
        status = task.find('ac:task-status')
        body   = task.find('ac:task-body')

        if body and label.lower() in body.get_text().lower():
            return status and status.get_text().strip() == 'complete'

    return False  # checkbox not found at all

# ── 5. Run all checks ──
def check(data):
    content = data['body']['storage']['value']
    errors  = []

    if data.get('status') == 'draft':
        errors.append('Page is still a DRAFT — publish it before merging')

    for section in REQUIRED_SECTIONS:
        if section.lower() not in content.lower():
            errors.append(f'Missing section: {section}')

    for checkbox in REQUIRED_CHECKBOXES:
        if not check_checkbox(content, checkbox):
            errors.append(f'Checkbox not ticked: "{checkbox}"')

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

    print('✅ Confluence deployment doc looks good. Merge is unblocked.')
