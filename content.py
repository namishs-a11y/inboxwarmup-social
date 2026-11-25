import os, json, re, datetime, hashlib, xml.etree.ElementTree as ET
from urllib.parse import quote
import requests

TOPICS=[
'Why Gmail accepts your message but files it under spam',
'SPF passes but mail still goes to junk: what to check next',
'DMARC alignment for cold outreach without hurting your primary domain',
'Cold email reply rate vs inbox placement: which metric matters first?',
'Outlook sender reputation troubleshooting without guessing',
'Why email warmup is not a replacement for permission and good content',
'DKIM selector misconfiguration: practical diagnosis',
'First-week sending volumes: avoiding sudden spikes',
'Authentication checklist before a sales outreach campaign',
'How to audit bounces, complaints, and unsubscribe handling',
'Why a delivered SMTP event is not proof of inbox delivery',
'Understanding Google Postmaster Tools limitations',
'What happens when your outreach list has stale addresses',
'How to review a sending domain after a spam placement drop',
'Email warmup misconceptions new founders should know',
]

def trends():
    """Headlines are idea signals, not verified facts; never make factual claims from them alone."""
    try:
        url='https://news.google.com/rss/search?q='+quote('email deliverability OR Gmail sender requirements OR DMARC when:7d')+'&hl=en-US&gl=US&ceid=US:en'
        r=requests.get(url,timeout=8,headers={'User-Agent':'InboxWarmupContentResearch/1.0'})
        r.raise_for_status()
        root=ET.fromstring(r.content)
        items=[]
        for item in root.findall('.//item')[:12]:
            title=(item.findtext('title') or '').strip()[:160]
            link=(item.findtext('link') or '').strip()
            if title and link: items.append({'title':title,'url':link})
        return items[:8]
    except Exception:return []

def generate(platform, topic, news=None):
    key=os.getenv('GROQ_API_KEY','').strip()
    if not key: raise RuntimeError('GROQ_API_KEY is missing in .env')
    seed={'reddit':'Write a substantial, conversational, genuinely helpful discussion starter: a question plus a diagnostic checklist. No hashtags, no promotional links, no brand mention, no pretending to be an independent user. Do not spam or use engagement bait.',
          'linkedin':'Write a strong 1-line hook, short paragraphs, three useful technical takeaways, one genuine question. Use at most 2 carefully relevant hashtags. Sound like an informed B2B practitioner, not an ad.',
          'x':'Write a clear standout hook and one precise tip or surprising insight under 260 characters including at most one relevant hashtag. Avoid generic marketing slogans.'}[platform]
    news=news or []
    signals='; '.join(x['title'] for x in news[:3])
    prompt=f'''Today: {datetime.date.today().isoformat()}. Audience: marketers, SaaS founders, small business operators who care about sending reputation and cold outreach. Core topic: {topic}. News headline signals (unverified, only for choosing angles): {signals or 'none'}. Platform: {platform}. {seed} Give a fresh, engaging, technically useful angle. Content must be original; do not claim any news item is true unless verified; do not invent numbers, results, stats, experience, product capabilities, or customer stories. InboxWarmup.io is an inbox warmup SaaS, manual warmup, and warmup does not guarantee delivery. For LinkedIn and X, only mention the company if organically relevant and disclose affiliation when applicable. Provide practical steps readers can take. Return only JSON with "title" and "body" strings.'''
    try:
        r=requests.post('https://api.groq.com/openai/v1/chat/completions',headers={'Authorization':f'Bearer {key}'},json={'model':os.getenv('GROQ_MODEL','openai/gpt-oss-20b'),'messages':[{'role':'system','content':'You write engaging, original, truthful organic social posts. Output valid JSON only.'},{'role':'user','content':prompt}],'temperature':0.75,'response_format':{'type':'json_object'},'max_completion_tokens':1200},timeout=55)
        r.raise_for_status(); d=json.loads(r.json()['choices'][0]['message']['content']); title=str(d['title']).strip()[:200]; body=str(d['body']).strip()
    except requests.HTTPError as e:raise RuntimeError('Groq returned HTTP '+str(e.response.status_code)) from None
    except Exception as e:raise RuntimeError('Groq generation failed: '+type(e).__name__) from None
    if not title or not body:raise RuntimeError('Empty AI draft')
    if platform=='x' and len(body)>280:raise RuntimeError('X draft exceeds 280 characters. Regenerate.')
    if platform=='reddit':body=re.sub(r'(?<!\w)#[A-Za-z0-9_]+','',body).strip()
    return title,body

STYLES = {
 'operator': 'A practical operator sharing specific troubleshooting steps, plain language, no corporate fluff.',
 'contrarian': 'Start with an unexpected but defensible point. Explain why conventional advice can be wrong, then provide a helpful correction. No fabricated claims.',
 'story': 'Use an illustrative hypothetical scenario, explicitly say it is hypothetical, show a problem and a remedy. Never pretend it happened to you or to customers.',
 'checklist': 'A crisp, save-worthy diagnostic checklist with concrete checks, not vague tips.',
 'question': 'A thought-provoking question inviting practitioners to share experiences, with your own useful context first.',
 'myth': 'Explain a common deliverability myth and how to test it in practice, with technical nuance.',
}
GOALS = {
 'reach': 'Maximize relevant reach using a compelling original opening, but no rage bait.',
 'trust': 'Earn technical credibility with accurate and actionable detail.',
 'discussion': 'Encourage sincere replies with one thoughtful, specific question.',
 'traffic': 'Help the reader first; optional natural CTA to InboxWarmup.io only for LinkedIn/X, never force a link.',
}

def generate_custom(platform, topic, style='operator', goal='reach', context='', angle=1, history=None):
    """Groq-generated single candidate. No unattended publish side-effects."""
    key=os.getenv('GROQ_API_KEY','').strip()
    if not key: raise RuntimeError('GROQ_API_KEY missing')
    if platform not in ('reddit','linkedin','x'): raise ValueError('Unsupported platform')
    style=STYLES.get(style, STYLES['operator']); goal=GOALS.get(goal,GOALS['reach'])
    recent=' | '.join((history or [])[:12])
    platform_rules={
      'reddit':'Genuinely helpful non-promotional discussion. 140-240 words. No hashtags, product links, brand mentions or marketing CTAs. No imitation of a customer. One authentic question. Posting requires human approval and community rules review.',
      'linkedin':'Strong specific first sentence, conversational paragraphs, about 100-170 words, max 2 relevant hashtags, one genuinely useful insight and one discussion prompt. No emoji overload.',
      'x':'Single punchy post <= 270 characters total. One useful takeaway. At most one topical hashtag, only if it reads naturally. No thread unless requested.',
    }[platform]
    prompt=f'''Write one ORIGINAL social post for InboxWarmup.io's organic marketing campaign.
Platform: {platform}. Topic: {topic[:280]}. Content style: {style}. Goal: {goal}. Creative variation: {angle} of 3; change the hook and structure substantially for each variation.
User notes: {context[:1000] or 'none'}.
Recent headlines already covered (DO NOT repeat): {recent[:1200] or 'none'}.
Rules: {platform_rules}
The brand sells inbox/domain warmup. Warmup is manual, not automated; it does not guarantee inbox placement. No made-up customer story, first-person experience, statistics, studies, news claims, results, metrics, or features. No phony urgency, clickbait, fake trends, hashtags in Reddit, blanket claims about spam filters, fake endorsements, or hard selling. Write like an actual experienced practitioner talking to another person. Favor examples that can be checked: SPF alignment vs authentication pass, DKIM, DMARC, bounces, sending history, unsubscribes, Postmaster limitations. Never claim to have used a tool or tested data unless user provided it. Content must be useful on its own. Plain English, varied rhythms, distinct hooks. Respond with a JSON object with just title and body strings. No markdown code fence.'''
    try:
        r=requests.post('https://api.groq.com/openai/v1/chat/completions',headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'},json={'model':os.getenv('GROQ_MODEL','openai/gpt-oss-20b'),'messages':[{'role':'system','content':'You are a meticulous B2B email-deliverability editor, with lively natural writing. Output exactly valid JSON.'},{'role':'user','content':prompt}],'temperature':0.85,'response_format':{'type':'json_object'},'max_completion_tokens':1300},timeout=60)
        r.raise_for_status();d=json.loads(r.json()['choices'][0]['message']['content']);title=str(d['title']).strip()[:160];body=str(d['body']).strip()
    except requests.HTTPError as e:raise RuntimeError('Groq returned HTTP '+str(e.response.status_code)) from None
    except Exception as e:raise RuntimeError('Groq generation failed: '+type(e).__name__) from None
    if not title or not body:raise RuntimeError('Groq returned empty content')
    if platform=='x' and len(body)>280:raise RuntimeError('Generated X post exceeds 280 characters; choose Regenerate')
    if platform=='reddit':body=re.sub(r'(?<!\w)#[A-Za-z0-9_]+','',body).strip()
    return title,body
