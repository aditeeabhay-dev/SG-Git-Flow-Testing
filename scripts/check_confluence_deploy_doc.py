import requests, sys, os, re

CONFLUENCE_BASE = os.environ['CONFLUENCE_BASE_URL']
PAGE_ID        = os.environ['CONFLUENCE_PAGE_ID']
EMAIL          = os.environ['CONFLUENCE_EMAIL']
API_TOKEN      = os.environ['CONFLUENCE_API_TOKEN']

# ── Edit this list to match your required doc sections ──
REQUIRED_SECTIONS = [
    'Section Testing and Approvals',
]

def fetch_page():
    url = f'{CONFLUENCE_BASE}/wiki/rest/api/content/{PAGE_ID}?expand=body.storage,version,status'
    r = requests.get(url, auth=(EMAIL, API_TOKEN))
    r.raise_for_status()
    return r.json()

def check(data):
    content = data['body']['storage']['value']
    status  = data.get('status', '')
    missing = [s for s in REQUIRED_SECTIONS if s.lower() not in content.lower()]
    errors  = []
    if status == 'draft':
        errors.append('Page is still a DRAFT — publish it before merging')
    if missing:
        errors.append('Missing required sections: ' + ', '.join(missing))
    return errors

if __name__ == '__main__':
    data   = fetch_page()
    errors = check(data)
    if errors:
        print('\n❌ Confluence deployment doc check FAILED:')
        for e in errors:
            print(f'   • {e}')
        sys.exit(1)
    print('✅ Confluence deployment doc looks good.')
