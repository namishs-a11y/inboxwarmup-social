import os, sys, html, sqlite3, threading, datetime as dt, time, secrets, csv, json
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from providers import postpone_accounts, buffer_channels, postpone_schedule, buffer_x_queue, ProviderError
from content import generate, trends, TOPICS, generate_custom, STYLES, GOALS

ROOT=Path(__file__).resolve().parent
load_dotenv(ROOT/'.env',override=False)
DB=ROOT/'data'/'posts.sqlite3'; DB.parent.mkdir(exist_ok=True)
REPORTS=ROOT/'reports'; REPORTS.mkdir(exist_ok=True)
_daily_lock=threading.Lock()
app=FastAPI(title='InboxWarmup Organic Studio',docs_url=None,redoc_url=None,openapi_url=None)
auth=HTTPBasic(); TZ=dt.timezone(dt.timedelta(hours=5,minutes=30))

def db():
 c=sqlite3.connect(DB,timeout=25);c.row_factory=sqlite3.Row
 c.execute('PRAGMA journal_mode=WAL')
 c.execute('CREATE TABLE IF NOT EXISTS posts (id INTEGER PRIMARY KEY, day TEXT,platform TEXT,topic TEXT,title TEXT,body TEXT,status TEXT,remote_id TEXT,error TEXT,subreddit TEXT,created TEXT, UNIQUE(day,platform))')
 c.execute('CREATE TABLE IF NOT EXISTS choices (id INTEGER PRIMARY KEY, platform TEXT, topic TEXT, style TEXT, goal TEXT, title TEXT, body TEXT, created TEXT)')
 c.execute('CREATE TABLE IF NOT EXISTS engagement (id INTEGER PRIMARY KEY, platform TEXT, remote_id TEXT, reactions INTEGER, comments INTEGER, impressions INTEGER, clicks INTEGER, shares INTEGER, notes TEXT, recorded TEXT)')
 c.execute('CREATE TABLE IF NOT EXISTS comment_notes (id INTEGER PRIMARY KEY, post_id INTEGER, author TEXT, text TEXT, response TEXT, created TEXT)')
 c.commit();return c

def guard(x:HTTPBasicCredentials=Depends(auth)):
 user=os.getenv('ADMIN_USER','admin');password=os.getenv('ADMIN_PASSWORD','')
 if not password or not (secrets.compare_digest(x.username,user) and secrets.compare_digest(x.password,password)):
  raise HTTPException(401,'Invalid dashboard credentials',headers={'WWW-Authenticate':'Basic'})

def csrf(req):
 origin=req.headers.get('origin'); allowed=f'{req.url.scheme}://{req.url.netloc}'
 if origin and origin.rstrip('/')!=allowed:raise HTTPException(403,'Invalid request origin')

def today():return dt.datetime.now(TZ).date().isoformat()

def strategy_for_day(day=None):
 day=day or dt.date.fromisoformat(today())
 index=(day-dt.date(2026,1,1)).days
 # Rotate goals and voices independently; avoid identical templates day after day.
 goals=['reach','trust','discussion','traffic','reach','trust','discussion']
 styles=['contrarian','operator','question','checklist','myth','story','operator']
 return goals[index%len(goals)],styles[(index//2+index)%len(styles)]

def generate_day():
 d=today(); c=db(); out=[]
 topic=TOPICS[(dt.date.fromisoformat(d)-dt.date(2026,1,1)).days%len(TOPICS)]
 goal,style=strategy_for_day()
 for platform in ['reddit','linkedin','x']:
  if c.execute('SELECT id FROM posts WHERE day=? AND platform=?',(d,platform)).fetchone():continue
  try:
   # No fictitious experience, no unverified trending claims, and no irrelevant hashtags.
   recent=[r['topic'] for r in c.execute('SELECT topic FROM posts WHERE platform=? ORDER BY id DESC LIMIT 12',(platform,))]
   title,body=generate_custom(platform,topic,style=style,goal=goal,history=recent)
   c.execute('INSERT INTO posts (day,platform,topic,title,body,status,created) VALUES (?,?,?,?,?,?,?)',(d,platform,topic,title,body,'review' if platform=='reddit' else 'draft',dt.datetime.now(dt.timezone.utc).isoformat()))
   c.commit();out.append(platform)
  except Exception as e:print(platform,'generation error:',str(e)[:200],flush=True)
 c.close();return out

def create_report():
 """Local factual status report; queue ≠ published. No invented views or replies."""
 c=db(); rows=[dict(p) for p in c.execute('SELECT day,platform,topic,title,status,remote_id,error,created FROM posts ORDER BY id DESC LIMIT 200')]
 stats={}
 for item in rows:
  stats.setdefault(item['platform'],{})[item['status']]=stats.setdefault(item['platform'],{}).get(item['status'],0)+1
 c.close()
 path=REPORTS/('publishing-'+today()+'.csv')
 with path.open('w',newline='',encoding='utf-8-sig') as f:
  writer=csv.DictWriter(f,fieldnames=['day','platform','topic','title','status','remote_id','error','created']);writer.writeheader();writer.writerows(rows)
 metadata={'created_at':dt.datetime.now(TZ).isoformat(),'report':path.name,'counts_by_platform':stats,'note':'queued is NOT proof of publication; Reddit notifications are NOT submitted Reddit posts.'}
 (REPORTS/'latest.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
 return metadata

def connected():
 result={'postpone':[],'buffer':[],'errors':[]}
 try:result['postpone']=postpone_accounts()
 except Exception as e:result['errors'].append('Postpone: '+str(e)[:160])
 try:result['buffer']=buffer_channels()
 except Exception as e:result['errors'].append('Buffer: '+str(e)[:160])
 return result

def destination(platform):
 if platform in ('reddit','linkedin'):
  accounts=[a for a in postpone_accounts() if a.get('platform')==platform and a.get('isConnected', True) and a.get('isEnabled', True)]
  configured=os.getenv('POSTPONE_'+platform.upper()+'_USERNAME','').strip()
  if configured:accounts=[a for a in accounts if a.get('username')==configured]
  if len(accounts)!=1:raise ProviderError('Unable to select exactly one '+platform+' profile automatically. Verify the Postpone account connection and API permissions.')
  return accounts[0]['username']
 chans=[c for c in buffer_channels() if str(c.get('service','')).lower() in ('twitter','x')]
 configured=os.getenv('BUFFER_X_CHANNEL_ID','').strip()
 if configured:chans=[c for c in chans if c.get('id')==configured]
 if len(chans)!=1:raise ProviderError('Unable to select exactly one X profile automatically. Verify the Buffer account connection and API permissions.')
 return chans[0]['id']

def send(pid):
 c=db();p=c.execute('SELECT * FROM posts WHERE id=?',(pid,)).fetchone()
 if not p: c.close();raise ValueError('Post not found')
 if p['status']!='approved':c.close();raise ValueError('Approve this post before queueing')
 # Explicit pending state stops duplicate API calls on uncertain timeouts.
 c.execute('UPDATE posts SET status="submitting", error=NULL WHERE id=?',(pid,));c.commit()
 try:
  target=destination(p['platform'])
  if p['platform']=='reddit':
   remote=postpone_schedule('reddit',p['title'],p['body'],target,subreddit=p['subreddit'])
   status='notification_queued'
  elif p['platform']=='linkedin':
   remote=postpone_schedule('linkedin',p['title'],p['body'],target);status='queued'
  else:
   remote=buffer_x_queue(p['body'],target);status='queued'
  c.execute('UPDATE posts SET status=?,remote_id=?,error=NULL WHERE id=?',(status,remote,pid));c.commit()
  return status+' remote ID: '+remote
 except Exception as e:
  # An API timeout can mean it succeeded. Never auto retry until checked at provider.
  msg=str(e)[:340];c.execute('UPDATE posts SET status="verify",error=? WHERE id=?',(msg,pid));c.commit()
  return 'Check provider before retrying: '+msg
 finally:c.close()

def autopost():
 if os.getenv('AUTO_QUEUE_SOCIAL','false').lower()!='true':return []
 c=db(); items=[dict(r) for r in c.execute("SELECT * FROM posts WHERE day=? AND status='draft' AND platform IN ('linkedin','x')",(today(),))];c.close()
 results=[]
 for item in items:
  # Reddit isn't automatically posted: manual final submission is required on Postpone.
  c=db(); c.execute('UPDATE posts SET status="approved" WHERE id=? AND status="draft"',(item['id'],));c.commit();c.close()
  results.append({'platform':item['platform'],'result':send(item['id'])})
 return results

def daily_once():
 if not _daily_lock.acquire(blocking=False):return
 try:
  generated=generate_day();queued=autopost();report=create_report()
  print('Daily run:',generated,queued,report['report'],flush=True)
 finally:_daily_lock.release()

def daily():
 daily_once()
 while True:
  now=dt.datetime.now(TZ);nxt=dt.datetime.combine(now.date(),dt.time(9,0),tzinfo=TZ)
  if nxt<=now:nxt+=dt.timedelta(days=1)
  time.sleep(max(5,(nxt-now).total_seconds()))
  try:daily_once()
  except Exception as e:print('Daily task:',type(e).__name__,str(e)[:200],flush=True)

@app.on_event('startup')
def startup():
 if os.getenv('DAILY_GENERATION','true').lower()=='true':threading.Thread(target=daily,daemon=True).start()

def esc(x):return html.escape(str(x or ''))
STYLE='''<style>body{background:#f4f7fc;color:#15253e;font:15px system-ui;margin:0;padding:24px}main{max-width:1030px;margin:auto}.hero{background:linear-gradient(125deg,#142f61,#3774a9);color:white;border-radius:20px;padding:27px;margin-bottom:18px}h1{margin:0 0 8px;font-size:28px}h2{font-size:19px}small,.muted{color:#66758c}.hero small{color:#d6e8fa}.chips{display:flex;gap:10px;flex-wrap:wrap;margin:15px 0}.chip{background:#e9f1fb;border-radius:20px;padding:6px 12px;font-size:12px}.card{background:#fff;border:1px solid #dfe7f2;border-radius:16px;padding:20px;margin:16px 0;box-shadow:0 3px 12px #1623440a}pre{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6;font:inherit}button{border:0;padding:9px 13px;border-radius:9px;background:#2466ce;color:white;cursor:pointer}button.alt{background:#e7effc;color:#234c89}form{display:inline-block;margin:4px 6px 4px 0}input{padding:9px;border:1px solid #bcc9db;border-radius:8px;max-width:200px}.status{font-weight:700;color:#2b5d91;text-transform:capitalize}a{color:#1758b1}.nav{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}.nav a{background:white;padding:9px 13px;border-radius:9px;text-decoration:none;border:1px solid #dfe7f2}.mutednote{color:#66758c}.note{border-left:4px solid #edb95c;padding:10px 14px;background:#fffcf2;margin:12px 0}</style>'''

@app.get('/',response_class=HTMLResponse)
def home(_:bool=Depends(guard)):
 c=db(); posts=c.execute('SELECT * FROM posts ORDER BY day DESC,id DESC LIMIT 100').fetchall();c.close()
 auto=os.getenv('AUTO_QUEUE_SOCIAL','false').lower()=='true'
 head=f'<nav class="nav"><a href="/">Dashboard</a><a href="/studio">Content Studio</a><a href="/daily-report">Daily report</a> <a href="/history">Post History</a><a href="/daily-report">Daily report</a> <a href="/insights">Insights & Replies</a><a href="/accounts">Accounts</a><a href="/topics">Topic Radar</a></nav><div class="hero"><h1>InboxWarmup • Organic Studio</h1><small>Groq content engine · Postpone Reddit + LinkedIn · Buffer X</small></div><div class="chips"><span class="chip">Automatic LinkedIn + X: {"ON" if auto else "OFF — configure .env"}</span><span class="chip">Reddit: approval + notification</span><span class="chip">Daily 09:00 IST while app running</span></div>'
 head+='<div class="card"><h2>Quick actions</h2><form method="post" action="/generate"><button>Generate today\'s content</button></form><form method="post" action="/auto"><button class="alt">Queue eligible LinkedIn + X posts now</button></form> <a href="/accounts">Check connected accounts</a><p class="muted">Posts are queued at provider schedules, not necessarily published immediately. Reddit requires completing the notification natively.</p></div>'
 cards=''
 for p in posts:
  p=dict(p);buttons=''
  if p['status'] in ('draft','review'):
   if p['platform']=='reddit':
    buttons+=f'<form method="post" action="/reddit/{p["id"]}"><input name="subreddit" value="{esc(p["subreddit"])}" placeholder="Approved subreddit" required><button>Approve Reddit notification</button></form>'
   else:buttons+=f'<form method="post" action="/approve/{p["id"]}"><button>Approve</button></form>'
   buttons+=f'<form method="post" action="/reject/{p["id"]}"><button class="alt">Reject</button></form>'
  if p['status']=='approved':buttons+=f'<form method="post" action="/send/{p["id"]}"><button>Queue with provider</button></form>'
  if p['status']=='verify':buttons+='<p class="note">Check the provider dashboard for a possible duplicate before retrying. No automatic retries.</p>'
  cards+=f'<article class="card"><span class="status">{esc(p["platform"])} · {esc(p["status"])}</span> <small>{esc(p["day"])} · {esc(p["topic"])}</small><h2>{esc(p["title"])}</h2><pre>{esc(p["body"])}</pre><div class="muted">{esc(p["error"])}</div>{buttons}</article>'
 return '<!doctype html><html><head><meta name="viewport" content="width=device-width"><title>InboxWarmup Social</title>'+STYLE+'</head><body><main>'+head+cards+'</main></body></html>'

@app.get('/accounts',response_class=HTMLResponse)
def accounts(_:bool=Depends(guard)):
 a=connected();items=''.join('<li>'+esc(x.get('platform'))+': '+esc(x.get('username'))+' — '+('connected' if x.get('isConnected') else 'disconnected')+'</li>' for x in a['postpone'])
 channels=''.join('<li>'+esc(x.get('service'))+': '+esc(x.get('name'))+' (ID '+esc(x.get('id'))+')</li>' for x in a['buffer'])
 errs=''.join('<p class="note">'+esc(e)+'</p>' for e in a['errors'])
 return '<!doctype html><html><head>'+STYLE+'</head><body><main><a href="/">← Dashboard</a><h1>Connected accounts</h1>'+errs+'<div class="card"><h2>Postpone</h2><ul>'+items+'</ul></div><div class="card"><h2>Buffer</h2><ul>'+channels+'</ul></div><p>Only connected accounts are eligible. Keys are never shown here.</p></main></body></html>'

@app.post('/generate')
def gen(req:Request,_:bool=Depends(guard)):
 csrf(req);generate_day();return RedirectResponse('/',303)
@app.post('/auto')
def run_auto(req:Request,_:bool=Depends(guard)):
 csrf(req);autopost();return RedirectResponse('/',303)
@app.post('/approve/{pid}')
def approve(pid:int,req:Request,_:bool=Depends(guard)):
 csrf(req);c=db();c.execute("UPDATE posts SET status='approved' WHERE id=? AND status='draft' AND platform!='reddit'",(pid,));c.commit();c.close();return RedirectResponse('/',303)
@app.post('/reddit/{pid}')
async def approve_reddit(pid:int,req:Request,_:bool=Depends(guard)):
 csrf(req);payload=await req.form();sub=str(payload.get('subreddit','')).strip().removeprefix('r/').strip()
 if not sub.replace('_','').isalnum() or len(sub)>21:raise HTTPException(400,'Enter a valid subreddit name')
 c=db();c.execute("UPDATE posts SET status='approved',subreddit=? WHERE id=? AND status IN ('review','draft') AND platform='reddit'",(sub,pid));c.commit();c.close();return RedirectResponse('/',303)
@app.post('/reject/{pid}')
def reject(pid:int,req:Request,_:bool=Depends(guard)):
 csrf(req);c=db();c.execute("UPDATE posts SET status='rejected' WHERE id=? AND status IN ('review','draft','approved')",(pid,));c.commit();c.close();return RedirectResponse('/',303)
@app.post('/send/{pid}')
def submit(pid:int,req:Request,_:bool=Depends(guard)):
 csrf(req);send(pid);return RedirectResponse('/',303)
@app.get('/daily-report',response_class=HTMLResponse)
def daily_report(_:bool=Depends(guard)):
 metadata=create_report()
 c=db();rows=[dict(p) for p in c.execute('SELECT day,platform,title,status,remote_id,error FROM posts ORDER BY id DESC LIMIT 35')];c.close()
 body='<div class="hero"><h1>Daily publishing report</h1><small>'+esc(metadata['created_at'])+'</small></div>'
 body+='<div class="note">Queued = accepted for scheduling, not confirmed live. Reddit requires manual submission. Instagram excluded.</div>'
 body+='<p><a href="/report-csv">Download full history CSV</a> · <a href="/daily-report">Daily report</a> <a href="/insights">View available metrics and replies</a></p>'
 body+='<div class="card"><table style="width:100%;border-collapse:collapse"><tr><th>Day</th><th>Platform</th><th>Post</th><th>Status</th><th>Provider ID/error</th></tr>'
 for p in rows:
  body+='<tr>'+''.join('<td style="padding:8px;border-bottom:1px solid #ddd">'+esc(p[k])+'</td>' for k in ['day','platform','title','status'])+'<td>'+esc(p['remote_id'] or p['error'])+'</td></tr>'
 body+='</table></div>'
 return page(body,'Daily report')

@app.get('/report-csv')
def report_csv(_:bool=Depends(guard)):
 from fastapi.responses import FileResponse
 create_report()
 return FileResponse(REPORTS/('publishing-'+today()+'.csv'),filename='inboxwarmup-publishing-'+today()+'.csv')

@app.get('/health')
def health():return {'status':'ok'}

if __name__=='__main__':
 if len(sys.argv)>1 and sys.argv[1]=='generate':print('Generated:',generate_day())
 elif len(sys.argv)>1 and sys.argv[1]=='accounts':print(connected())
 elif len(sys.argv)>1 and sys.argv[1]=='auto':generate_day();autopost()
 else:print('Run the Windows launcher or uvicorn app:app --host 127.0.0.1 --port 8765')


# CONTENT STUDIO: Generate three independent candidate hooks and let user choose.
def page(body,title='InboxWarmup Organic Studio'):
 return '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>'+esc(title)+'</title>'+STYLE+'</head><body><main><nav class="nav"><a href="/">Dashboard</a><a href="/studio">Content Studio</a><a href="/daily-report">Daily report</a> <a href="/history">Post History</a><a href="/daily-report">Daily report</a> <a href="/insights">Insights & Replies</a><a href="/accounts">Accounts</a><a href="/topics">Topic Radar</a></nav>'+body+'</main></body></html>'

@app.get('/studio',response_class=HTMLResponse)
def studio(_:bool=Depends(guard)):
 c=db(); options=[dict(x) for x in c.execute('SELECT * FROM choices ORDER BY id DESC LIMIT 24')];c.close()
 form='''<div class="hero"><h1>Content Studio</h1><small>Choose an angle. Generate distinct options. Edit before publishing.</small></div><div class="card"><h2>Make something people actually want to read</h2><form method="post" action="/studio/generate" style="display:block"><label>Platform <select name="platform"><option>reddit</option><option>linkedin</option><option>x</option></select></label> <label>Voice <select name="style">'''+''.join('<option value="'+esc(k)+'">'+esc(k.title())+'</option>' for k in STYLES)+'''</select></label> <label>Goal <select name="goal">'''+''.join('<option value="'+esc(k)+'">'+esc(k.title())+'</option>' for k in GOALS)+'''</select></label><p>Topic <input style="max-width:650px;width:90%" name="topic" maxlength="280" required placeholder="Why a perfectly authenticated email still lands in spam"></p><p>Optional notes <textarea name="context" maxlength="1000" rows="3" style="width:95%" placeholder="A specific example, audience or point of view"></textarea></p><button>Generate 3 ideas with Groq</button><p class="muted">Uses 3 Groq calls. No posts are automatically sent from this screen.</p></form></div>'''
 cards=''
 for o in options:
  cards+='<article class="card"><span class="chip">'+esc(o['platform'])+'</span> <small>'+esc(o['style'])+' · '+esc(o['goal'])+'</small><h2>'+esc(o['title'])+'</h2><pre>'+esc(o['body'])+'</pre><form method="post" action="/studio/select/'+str(o['id'])+'"><button>Use this version as today’s draft</button></form></article>'
 return page(form+cards,'Content Studio')

@app.post('/studio/generate')
async def studio_generate(req:Request,_:bool=Depends(guard)):
 csrf(req);f=await req.form();p=str(f.get('platform','reddit'));topic=str(f.get('topic','')).strip();style=str(f.get('style','operator'));goal=str(f.get('goal','reach'));context=str(f.get('context',''))
 if p not in ('reddit','linkedin','x') or not (5<=len(topic)<=280) or style not in STYLES or goal not in GOALS:raise HTTPException(400,'Invalid content options')
 c=db();prev=[r[0] for r in c.execute('SELECT title FROM posts ORDER BY id DESC LIMIT 15')]
 try:
  for i in range(1,4):
   title,body=generate_custom(p,topic,style,goal,context,i,prev)
   c.execute('INSERT INTO choices (platform,topic,style,goal,title,body,created) VALUES (?,?,?,?,?,?,?)',(p,topic,style,goal,title,body,dt.datetime.now(dt.timezone.utc).isoformat()))
   prev.insert(0,title)
  c.commit()
 finally:c.close()
 return RedirectResponse('/studio',303)

@app.post('/studio/select/{cid}')
def studio_select(cid:int,req:Request,_:bool=Depends(guard)):
 csrf(req);c=db();a=c.execute('SELECT * FROM choices WHERE id=?',(cid,)).fetchone()
 if not a:c.close();raise HTTPException(404,'Draft option not found')
 existing=c.execute('SELECT * FROM posts WHERE day=? AND platform=?',(today(),a['platform'])).fetchone()
 if existing and existing['status'] not in ('draft','review','rejected'):
  c.close();raise HTTPException(409,'Today’s post already queued or submitted. Cannot overwrite. Use tomorrow’s draft.')
 status='review' if a['platform']=='reddit' else 'draft'
 if existing:c.execute('UPDATE posts SET topic=?,title=?,body=?,status=?,subreddit=NULL,error=NULL WHERE id=?',(a['topic'],a['title'],a['body'],status,existing['id']))
 else:c.execute('INSERT INTO posts(day,platform,topic,title,body,status,created) VALUES(?,?,?,?,?,?,?)',(today(),a['platform'],a['topic'],a['title'],a['body'],status,dt.datetime.now(dt.timezone.utc).isoformat()))
 c.commit();c.close();return RedirectResponse('/',303)

@app.get('/history',response_class=HTMLResponse)
def history(_:bool=Depends(guard)):
 c=db();posts=[dict(x) for x in c.execute('SELECT * FROM posts ORDER BY id DESC LIMIT 400')];c.close()
 counts={}
 for p in posts:counts[p['status']]=counts.get(p['status'],0)+1
 cards='<div class="hero"><h1>Publishing history</h1><small>Local posts only; a queue acceptance is not proof a post went live.</small></div><div class="chips">'+''.join('<span class="chip">'+esc(k)+': '+str(n)+'</span>' for k,n in counts.items())+'</div>'
 for x in posts:
  cards+='<div class="card"><b>'+esc(x['platform'])+'</b> · '+esc(x['day'])+' · <span class="status">'+esc(x['status'])+'</span><h2>'+esc(x['title'])+'</h2><pre>'+esc(x['body'])+'</pre><small>Provider ID: '+esc(x['remote_id'] or '—')+' · '+esc(x['error'])+'</small>'
  if x['status'] in ('draft','review','rejected'):
   cards+='<details><summary>Edit draft</summary><form style="display:block" action="/edit/'+str(x['id'])+'" method="post"><p>Title <input style="max-width:100%;width:90%" name="title" value="'+esc(x['title'])+'"></p><textarea name="body" rows="7" style="width:97%">'+esc(x['body'])+'</textarea><p><button>Save edits</button></p></form></details>'
  cards+='</div>'
 return page(cards,'Publishing History')

@app.post('/edit/{pid}')
async def edit_draft(pid:int,req:Request,_:bool=Depends(guard)):
 csrf(req);f=await req.form();title=str(f.get('title','')).strip()[:200];body=str(f.get('body','')).strip()
 if not title or not body or len(body)>4000:raise HTTPException(400,'Invalid text length')
 c=db();p=c.execute('SELECT * FROM posts WHERE id=?',(pid,)).fetchone()
 if not p or p['status'] not in ('draft','review','rejected'):c.close();raise HTTPException(409,'This post cannot be edited')
 if p['platform']=='x' and len(body)>280:c.close();raise HTTPException(400,'X text exceeds 280 characters')
 c.execute('UPDATE posts SET title=?,body=?,status=? WHERE id=?',(title,body,'review' if p['platform']=='reddit' else 'draft',pid));c.commit();c.close()
 return RedirectResponse('/history',303)

@app.get('/insights',response_class=HTMLResponse)
def insights(_:bool=Depends(guard)):
 c=db();posts=[dict(x) for x in c.execute('SELECT * FROM posts ORDER BY id DESC LIMIT 150')];notes=[dict(x) for x in c.execute('SELECT * FROM comment_notes ORDER BY id DESC LIMIT 50')];manual=[dict(x) for x in c.execute('SELECT * FROM engagement ORDER BY id DESC LIMIT 150')];c.close()
 buf_error='';buf=[]
 if os.getenv('BUFFER_API_KEY','').strip():
  try:
   from providers import buffer_sent_posts
   buf=buffer_sent_posts(40)
  except Exception as e:buf_error=str(e)[:220]
 sent_count=sum(p['status'] in ('queued','notification_queued') for p in posts)
 cards='<div class="hero"><h1>Performance & replies</h1><small>Actual provider history where available, plus transparent manual notes.</small></div><div class="chips"><span class="chip">Local drafts: '+str(len(posts))+'</span><span class="chip">Accepted into queues: '+str(sent_count)+'</span><span class="chip">Buffer sent posts returned: '+str(len(buf))+'</span><span class="chip">Manual metric logs: '+str(len(manual))+'</span></div>'
 cards+='<div class="note"><b>Meaning of statuses:</b> queued ≠ published. Reddit notification_queued requires final manual submission. Buffer post metrics are experimental, may be delayed, and comment text/replies are unavailable from Buffer API. Postpone engagement is not verified in this app.</div>'
 if buf_error:cards+='<div class="note">Buffer sync: '+esc(buf_error)+'</div>'
 if buf:
  cards+='<div class="card"><h2>Sent posts from Buffer (live API)</h2>'
  for x in buf[:40]:
   metrics=x.get('metrics') or []
   cards+='<p><b>'+esc(x.get('dueAt'))+'</b> · '+esc(x.get('text','')[:230])+'<br><small>'+esc(', '.join(str(m.get('type'))+': '+str(m.get('value')) for m in metrics if isinstance(m,dict)) or 'Metrics unavailable')+'</small></p><hr>'
  cards+='</div>'
 cards+='<div class="card"><h2>Record actual results</h2><p class="muted">Enter verified figures from Reddit, Postpone, LinkedIn or X. Blank values stay unknown; do not estimate.</p><form style="display:block" method="post" action="/insights/log"><p>Platform <select name="platform"><option>reddit</option><option>linkedin</option><option>x</option></select> Remote post ID / URL <input name="remote_id" required placeholder="post link or ID" style="max-width:360px;width:60%"></p><p>Likes <input name="reactions" type="number" min="0"> Comments <input name="comments" type="number" min="0"> Impressions <input name="impressions" type="number" min="0"></p><p>Clicks <input name="clicks" type="number" min="0"> Shares <input name="shares" type="number" min="0"></p><p>Notes <input name="notes" style="max-width:600px;width:95%" placeholder="Where you checked it / what changed"></p><button>Save verified results</button></form></div>'
 cards+='<div class="card"><h2>Track replies and follow-up ideas</h2><p>Paste actual comments you received. This app does not fetch reply text automatically.</p><form style="display:block" method="post" action="/insights/comment"><p>Local post ID <input type="number" name="post_id" min="1" required> Username <input name="author" placeholder="Author"></p><textarea name="text" rows="3" style="width:95%" required placeholder="What they replied"></textarea><p>Your follow-up or notes <input name="response" style="max-width:650px;width:95%"></p><button>Save reply</button></form></div>'
 if manual:
  cards+='<div class="card"><h2>Recorded results</h2>'
  for x in manual[:40]:cards+='<p><b>'+esc(x['platform'])+'</b> '+esc(x['remote_id'])+' · '+esc(x['recorded'])+'<br><small>Likes '+esc(x['reactions'])+' | Comments '+esc(x['comments'])+' | Impressions '+esc(x['impressions'])+' | Clicks '+esc(x['clicks'])+' | Shares '+esc(x['shares'])+'</small><br>'+esc(x['notes'])+'</p>'
  cards+='</div>'
 if notes:
  cards+='<div class="card"><h2>Saved responses</h2>'
  for x in notes:cards+='<p><b>Post #'+str(x['post_id'])+' · '+esc(x['author'])+'</b>: '+esc(x['text'])+'<br><small>Your response: '+esc(x['response'])+'</small></p>'
  cards+='</div>'
 return page(cards,'Social Insights')

@app.post('/insights/log')
async def save_metrics(req:Request,_:bool=Depends(guard)):
 csrf(req);f=await req.form();p=str(f.get('platform',''));remote=str(f.get('remote_id','')).strip()
 if p not in ('reddit','linkedin','x') or not remote or len(remote)>300:raise HTTPException(400,'Bad platform or post ID')
 nums=[]
 for field in ('reactions','comments','impressions','clicks','shares'):
  raw=str(f.get(field,'')).strip()
  if not raw:nums.append(None)
  elif raw.isdigit() and int(raw)<1_000_000_000:nums.append(int(raw))
  else:raise HTTPException(400,'Metrics must be nonnegative integers')
 c=db();c.execute('INSERT INTO engagement(platform,remote_id,reactions,comments,impressions,clicks,shares,notes,recorded) VALUES(?,?,?,?,?,?,?,?,?)',(p,remote,*nums,str(f.get('notes',''))[:600],dt.datetime.now(TZ).isoformat()));c.commit();c.close()
 return RedirectResponse('/insights',303)

@app.post('/insights/comment')
async def save_comment(req:Request,_:bool=Depends(guard)):
 csrf(req);f=await req.form()
 try:pid=int(f.get('post_id',''))
 except ValueError:raise HTTPException(400,'Bad post ID')
 comment=str(f.get('text','')).strip()
 if not comment or len(comment)>3000:raise HTTPException(400,'Invalid comment')
 c=db();found=c.execute('SELECT id FROM posts WHERE id=?',(pid,)).fetchone()
 if not found:c.close();raise HTTPException(404,'Post not found')
 c.execute('INSERT INTO comment_notes(post_id,author,text,response,created) VALUES(?,?,?,?,?)',(pid,str(f.get('author',''))[:120],comment,str(f.get('response',''))[:1000],dt.datetime.now(TZ).isoformat()));c.commit();c.close()
 return RedirectResponse('/insights',303)

@app.get('/topics',response_class=HTMLResponse)
def topic_radar(_:bool=Depends(guard)):
 items=trends()
 out='<div class="hero"><h1>Topic Radar</h1><small>Recent headline signals and proven evergreen email deliverability topics. Verify every external claim before publishing.</small></div>'
 out+='<div class="card"><h2>Recent headlines to research</h2>'
 if items:
  for i in items:out+='<p><a href="'+esc(i['url'])+'" target="_blank" rel="noopener noreferrer">'+esc(i['title'])+'</a></p>'
 else:out+='<p class="muted">No live feed available. You can still build evergreen posts below.</p>'
 out+='</div><div class="card"><h2>Useful evergreen angles</h2>'
 for t in TOPICS:out+='<p>'+esc(t)+'</p>'
 out+='</div><div class="card"><h2>Hashtag guidance</h2><p>For X and LinkedIn, use zero to two directly relevant hashtags where useful: #EmailDeliverability, #EmailMarketing, #DMARC, #ColdEmail. These are topic tags, not verified live trending hashtags. Reddit: no hashtags.</p><a href="/studio">Create a post from an angle</a></div>'
 return page(out,'Topic Radar')
