import os, requests

class ProviderError(RuntimeError): pass

def _graphql(url, key, query, variables=None):
    if not key: raise ProviderError('Missing API key')
    try:
        r=requests.post(url, headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'}, json={'query':query,'variables':variables or {}}, timeout=25)
        r.raise_for_status()
        result=r.json()
    except requests.HTTPError as e:
        raise ProviderError(f'Provider returned HTTP {e.response.status_code}') from None
    except (requests.RequestException,ValueError):
        raise ProviderError('Provider connection failed or returned invalid JSON') from None
    if result.get('errors'):
        raise ProviderError('; '.join(str(e.get('message','Unknown GraphQL error')) for e in result['errors'])[:350])
    return result.get('data') or {}

def postpone_accounts():
    """Try documented/common GraphQL social account shape; fall back to introspection.

    Postpone may restrict schema introspection and API features by subscription.
    Never assume a returned account without explicit platform identity.
    """
    key=os.getenv('POSTPONE_API_KEY','').strip()
    if not key: raise ProviderError('Postpone API key missing. Open CONFIGURE.bat to add it.')
    endpoint='https://api.postpone.app/gql'
    options=[
      'query { socialAccounts { id username name platform isConnected isEnabled } }',
      'query { socialAccounts { id username platform } }',
      'query { socialAccounts { username platform } }',
      'query { socialAccounts { username socialPlatform } }',
    ]
    errors=[]
    for q in options:
      try:
        data=_graphql(endpoint,key,q)
        acc=data.get('socialAccounts')
        if isinstance(acc,list):
          results=[]
          for a in acc:
            platform=str(a.get('platform') or a.get('socialPlatform') or '').lower()
            if 'reddit' in platform: platform='reddit'
            elif 'linkedin' in platform: platform='linkedin'
            results.append({**a,'platform':platform,'isConnected':a.get('isConnected',True),'isEnabled':a.get('isEnabled',True)})
          return results
      except ProviderError as e:errors.append(str(e))
    raise ProviderError('Postpone account discovery unavailable ('+('; '.join(errors[:1]))[:120]+'). This may be an API-plan or schema restriction. Open Postpone dashboard to verify API access.')

def buffer_channels():
    key=os.getenv('BUFFER_API_KEY','').strip()
    if not key: raise ProviderError('Buffer API key missing. Open CONFIGURE.bat to add it.')
    data=_graphql('https://api.buffer.com',key,'query { account { organizations { id name } } }')
    result=[]
    for org in (data.get('account') or {}).get('organizations') or []:
        orgid=org['id']
        # Literal GraphQL argument avoids ambiguity about custom scalar variable types.
        import json
        q='query { channels(input: { organizationId: '+json.dumps(orgid)+' }) { id name service } }'
        dat=_graphql('https://api.buffer.com',key,q)
        result.extend([{**item,'organization':org.get('name','')} for item in dat.get('channels') or []])
    return result

def postpone_schedule(platform, title, body, username, subreddit=None):
    if platform=='reddit':
        if not subreddit: raise ProviderError('Choose an approved subreddit first')
        query='''mutation ($input: ScheduleRedditPostInput!) { scheduleRedditPost(input:$input) { success errors { field message } post { id } } }'''
        data={'username':username,'title':title[:300],'content':body,'schedulingMode':'QUEUE_NEXT','publishingStatus':'READY_TO_PUBLISH','submissions':[{'validationId':'iw1','subreddit':subreddit.lstrip('r/'),'publishingMethod':'MANUAL'}]}
        field='scheduleRedditPost'
    elif platform=='linkedin':
        query='''mutation ($input: ScheduleLinkedInPostInput!) { scheduleLinkedInPost(input:$input) { success errors { field message } post { id } } }'''
        data={'username':username,'text':body[:3000],'visibility':'PUBLIC','schedulingMode':'QUEUE_NEXT','publishingStatus':'READY_TO_PUBLISH','submissions':[{}]}
        field='scheduleLinkedInPost'
    else: raise ProviderError('Unsupported platform')
    result=_graphql('https://api.postpone.app/gql',os.getenv('POSTPONE_API_KEY',''),query,{'input':data}).get(field) or {}
    if not result.get('success'):
        raise ProviderError('; '.join(str(i.get('message','Scheduling rejected')) for i in result.get('errors',[]))[:350] or 'Postpone rejected this post')
    return str((result.get('post') or {}).get('id') or 'accepted')

def buffer_x_queue(body, channel_id):
    query='''mutation ($input: CreatePostInput!) { createPost(input:$input) { ... on PostActionSuccess { post { id } } ... on MutationError { message } } }'''
    inp={'text':body[:280], 'channelId':channel_id, 'schedulingType':'automatic','mode':'addToQueue'}
    response=_graphql('https://api.buffer.com',os.getenv('BUFFER_API_KEY',''),query,{'input':inp})
    result=response.get('createPost') or {}
    if not result.get('post'):
        raise ProviderError(str(result.get('message','Buffer did not return a post ID'))[:350])
    return str(result['post']['id'])

def buffer_sent_posts(limit=40):
    """Read sent Buffer posts and optional experimental metrics. Never fake missing fields."""
    key=os.getenv('BUFFER_API_KEY','').strip()
    if not key: raise ProviderError('Buffer API key missing')
    data=_graphql('https://api.buffer.com',key,'query { account { organizations { id name } } }')
    out=[]
    for org in (data.get('account') or {}).get('organizations') or []:
        import json
        orgid=json.dumps(org['id'])
        # Try richer metrics first, then fall back to stable history fields.
        queries=[
          f'''query {{ posts(first:{min(limit,50)},input:{{organizationId:{orgid},filter:{{status:[sent]}}}}) {{ edges {{ node {{ id text channelId dueAt metrics {{ type value }} metricsUpdatedAt }} }} }} }}''',
          f'''query {{ posts(first:{min(limit,50)},input:{{organizationId:{orgid},filter:{{status:[sent]}}}}) {{ edges {{ node {{ id text channelId dueAt }} }} }} }}''',
        ]
        last=None
        for query in queries:
            try:
                res=_graphql('https://api.buffer.com',key,query)
                posts=(res.get('posts') or {}).get('edges') or []
                out.extend([edge['node'] for edge in posts if isinstance(edge,dict) and edge.get('node')]);break
            except ProviderError as e:last=e
        else:raise last or ProviderError('Buffer post history unavailable')
    return out
