#!/usr/bin/env python3
"""Generate a checklist comment on a newly opened GitHub Issue.

Reads from environment variables set by the GitHub Actions workflow:
    TITLE, REPO, ISSUE_NUMBER, GH_TOKEN
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

title = os.environ.get('TITLE', '')
repo = os.environ.get('REPO', '')
issue_number = os.environ.get('ISSUE_NUMBER', '')
token = os.environ.get('GH_TOKEN', '')

MIN_TITLE_LENGTH = 10
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
_NO_RETRY_CODES = {401, 403, 404, 422}

if len(title) <= MIN_TITLE_LENGTH:
    print(f'Title too short ({len(title)} chars). Skipping.')
    sys.exit(0)


def api_request(url, *, data=None):
    """POST (if data) or GET with timeout, exponential backoff, and error classification."""
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                url, data=data,
                method='POST' if data else 'GET',
            )
            req.add_header('Authorization', f'Bearer {token}')
            req.add_header('Accept', 'application/vnd.github+json')
            req.add_header('X-GitHub-Api-Version', '2022-11-28')
            if data:
                req.add_header('Content-Type', 'application/json')
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code in _NO_RETRY_CODES:
                raise
            if exc.code == 429:
                wait = int(exc.headers.get('Retry-After', 60))
                print(f'[rate-limit] 429 — waiting {wait}s before retry {attempt}/{MAX_RETRIES}')
                time.sleep(wait)
                last_exc = exc
                continue
            last_exc = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = exc
        if attempt < MAX_RETRIES:
            delay = 2 ** (attempt - 1)  # 1s, 2s, 4s
            print(f'[retry] attempt {attempt}/{MAX_RETRIES} failed ({last_exc}), retrying in {delay}s...')
            time.sleep(delay)
    raise RuntimeError(f'API request failed after {MAX_RETRIES} attempts: {last_exc}') from last_exc


# Conventional Commits allow an optional scope and a breaking-change marker.
# Normalize "feat(api)!:" → "feat:" so prefix rules only match the type.
SCOPE_RE = re.compile(r'^([a-z]+)\([^)]*\)(!?):', re.I)


def generate_tasks(title):
    """Return task list items based on the issue title prefix."""
    title_lower = SCOPE_RE.sub(r'\1:', title.strip().lower())

    if re.match(r'^(fix|fixes|bug|bugs|bugfix|patch|hotfix)[:\s]', title_lower):
        return [
            'Reproduce the issue with a minimal test case',
            'Identify the root cause',
            'Implement the fix',
            'Add a regression test',
            'Verify the fix in staging',
        ]

    if re.match(r'^(add|implement|create|build|develop|make|feat|feature)[:\s]', title_lower):
        return [
            'Define requirements and acceptance criteria',
            'Design the solution',
            'Implement the feature',
            'Write tests',
            'Update documentation',
        ]

    if re.match(r'^(refactor|cleanup|reorganize|rename|move|migrate|restructure)[:\s]', title_lower):
        return [
            'Identify the scope of the refactor',
            'Ensure tests cover the current behavior',
            'Apply the refactor incrementally',
            'Confirm no behavioral changes (tests green)',
            'Update documentation if affected',
        ]

    if re.match(r'^(improve|enhance|optimize|upgrade|update|perf|performance|speed|faster)[:\s]', title_lower):
        return [
            'Measure the current baseline',
            'Identify the bottleneck',
            'Implement the improvement',
            'Measure again and confirm the gain',
            'Document the change',
        ]

    if re.match(r'^(docs|doc|documentation)[:\s]', title_lower):
        return [
            'Identify which docs are out of date or missing',
            'Write / update the content',
            'Verify all examples and links still work',
            'Proofread and review',
        ]

    if re.match(r'^(test|tests|testing|spec)[:\s]', title_lower):
        return [
            'Identify the uncovered paths / scenarios',
            'Write the test cases',
            'Confirm they fail against the old behavior and pass after',
            'Wire the tests into CI',
        ]

    if re.match(r'^(ci|cd|pipeline|workflow)[:\s]', title_lower):
        return [
            'Describe the desired pipeline behavior',
            'Update the workflow configuration',
            'Validate on a test branch or with a dry run',
            'Document the change for other contributors',
        ]

    if re.match(r'^(chore|maintenance|housekeeping|style|lint)[:\s]', title_lower):
        return [
            'Scope exactly what will change',
            'Apply the change',
            'Confirm no behavioral impact (tests / CI green)',
            'Update docs or config if affected',
        ]

    return [
        'Clarify requirements and acceptance criteria',
        'Design the approach',
        'Implement',
        'Test',
        'Document',
    ]


def main():
    tasks = generate_tasks(title)
    task_list = '\n'.join(f'- [ ] {t}' for t in tasks)
    body = f'## Task List\n\n{task_list}'

    url = f'https://api.github.com/repos/{repo}/issues/{issue_number}/comments'
    data = json.dumps({'body': body}).encode()

    try:
        api_request(url, data=data)
        print('✅ Posted task list comment.')
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            print(f'[error] Authentication failed (HTTP {exc.code}). Task list body follows for log retention:')
        else:
            print(f'[error] Failed to post comment (HTTP {exc.code}). Task list body follows for log retention:')
        print(body)
        sys.exit(0)
    except Exception as exc:
        print(f'[error] Failed to post comment after {MAX_RETRIES} retries: {exc}. Task list body follows for log retention:')
        print(body)
        sys.exit(0)


if __name__ == '__main__':
    main()
