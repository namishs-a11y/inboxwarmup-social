"""One-off GitHub Actions entrypoint. No server or Windows process required."""
import json
import os
import sys
from pathlib import Path

import app
from app import daily_once, db, today
from report_builder import build


def main():
    required = ('GROQ_API_KEY', 'POSTPONE_API_KEY', 'BUFFER_API_KEY')
    missing = [name for name in required if not os.environ.get(name, '').strip()]
    if missing:
        print('Missing GitHub Actions secret(s): ' + ', '.join(missing), file=sys.stderr)
        return 2
    if os.environ.get('AUTO_QUEUE_SOCIAL', '').lower() != 'true':
        print('AUTO_QUEUE_SOCIAL is not true; no automatic queueing will occur', file=sys.stderr)
        return 2
    daily_once()
    c = db()
    rows = [dict(x) for x in c.execute('SELECT platform,status,remote_id,error FROM posts WHERE day=? ORDER BY platform', (today(),))]
    c.close()
    analysis=build(app.DB, app.REPORTS)
    print("LOCAL ACTIVITY REPORT:", json.dumps(analysis, ensure_ascii=False))
    print('DAILY STATUS (queued != live):', json.dumps(rows, ensure_ascii=False))
    summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary_path:
        with open(summary_path,'a',encoding='utf-8') as f:
            f.write('## InboxWarmup social — '+today()+'\n\n')
            f.write('| Platform | Status | Provider ID | Note |\n|---|---|---|---|\n')
            for row in rows:
                note = (row['error'] or '').replace('|', '/')[:200]
                f.write(f"| {row['platform']} | {row['status']} | {row['remote_id'] or '-'} | {note} |\n")
            f.write('\nQueued is not proof of a published post. Reddit requires manual completion.\n')
    failed = [r for r in rows if r['status'] in ('draft','verify','submitting') and r['platform'] in ('linkedin','x')]
    if not rows or failed:
        print('WARNING: One or more eligible posts were not queued. Inspect logs and provider plan/access.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
