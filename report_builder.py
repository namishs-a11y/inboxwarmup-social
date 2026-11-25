"""Create accurate downloadable HTML/CSV reports from stored local publish events.
This is status analytics, NOT social engagement analytics.
"""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import csv, html, json, sqlite3


def build(db_path, report_folder):
    report_folder=Path(report_folder);report_folder.mkdir(parents=True, exist_ok=True)
    db_path=Path(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory=sqlite3.Row
        rows=[dict(r) for r in conn.execute('SELECT day,platform,topic,title,body,status,remote_id,error,created FROM posts ORDER BY day DESC,id DESC LIMIT 500')]
    total=len(rows)
    by_status=Counter(r['status'] for r in rows)
    by_platform=Counter(r['platform'] for r in rows)
    attempted=sum(by_status.get(s,0) for s in ('queued','notification_queued','verify','submitting'))
    # Neither a provider queue ID nor Reddit notification is proof of publication.
    lines=['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
           '<title>InboxWarmup Social — Publishing Report</title><style>body{font:15px system-ui;background:#f5f7fc;color:#14233e;margin:0;padding:28px}main{max-width:1150px;margin:auto}h1{margin-bottom:4px}.small{color:#62728c}.stats{display:flex;gap:14px;flex-wrap:wrap;margin:20px 0}.stat{background:white;border:1px solid #dde6f0;border-radius:13px;padding:16px;min-width:135px}.stat strong{display:block;font-size:28px}table{border-collapse:collapse;width:100%;background:#fff}th,td{border-bottom:1px solid #e5eaf3;padding:12px;text-align:left;vertical-align:top}th{background:#e9f0fa}td{overflow-wrap:anywhere}code{font-size:12px} .notice{padding:14px;border:1px solid #cfdaeb;border-radius:10px;background:white;margin:16px 0}</style></head><body><main>',
           '<h1>InboxWarmup Daily Social Report</h1><div class="small">Generated '+html.escape(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))+'</div>',
           '<div class="notice"><strong>Important:</strong> Queued is NOT confirmed published. Reddit review is NOT posted. Views, replies, likes and clicks are unavailable here until a verified analytics integration is connected. This report contains only locally recorded automation activity.</div>',
           '<div class="stats">']
    for label,value in [('Saved posts',total),('API attempts / accepted',attempted),('Queued',by_status['queued']),('Needs verification',by_status['verify']),('Reddit drafts',by_status['review'])]:
        lines.append('<div class="stat"><strong>'+str(value)+'</strong>'+html.escape(label)+'</div>')
    lines.extend(['</div><h2>Platform coverage</h2><p>'+', '.join(html.escape(k)+': '+str(v) for k,v in sorted(by_platform.items()))+'</p>',
                 '<h2>Recent activity</h2><table><thead><tr><th>Date</th><th>Platform</th><th>Topic / Content</th><th>Status</th><th>Provider ID</th><th>Error</th></tr></thead><tbody>'])
    for r in rows:
        cells=[r['day'],r['platform'],(r['title'] or '')+' — '+(r['body'] or '')[:300],r['status'],r['remote_id'] or '',r['error'] or '']
        lines.append('<tr>'+''.join('<td>'+html.escape(str(c or ''))+'</td>' for c in cells)+'</tr>')
    lines.extend(['</tbody></table><p class="small">History is limited to the latest 500 saved posts. Engagement and website conversion analytics require additional verified integrations.</p></main></body></html>'])
    html_path=report_folder/'dashboard.html';html_path.write_text('\n'.join(lines),encoding='utf-8')
    csv_path=report_folder/'post-history.csv'
    with csv_path.open('w',encoding='utf-8-sig',newline='') as f:
        fields=['day','platform','topic','title','body','status','remote_id','error','created']
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    return {'posts':total,'counts_by_status':dict(by_status),'counts_by_platform':dict(by_platform),'html':str(html_path),'csv':str(csv_path)}
