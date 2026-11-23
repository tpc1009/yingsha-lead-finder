"""Persistent candidate pools; batch allocation and jobs share a SQLite transaction."""
import hashlib
import json

from .facebook import profile_url, search_source
from .store import now
from .url_rules import identity_key

TERMINAL={'done','review','skipped','failed'}


class FacebookSessions:
    def __init__(self,store):
        self.store=store
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS facebook_sessions(id INTEGER PRIMARY KEY, session_key TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)')

    def _read(self,c,sid):
        row=c.execute('SELECT payload FROM facebook_sessions WHERE id=?',(sid,)).fetchone()
        if not row: raise ValueError('搜索记录不存在')
        return json.loads(row[0])

    def _state(self,c,data):
        items={i['key']:{**i} for i in data['candidates']}
        jobs=[json.loads(r[0]) for r in c.execute('SELECT payload FROM jobs ORDER BY id')]
        jobs=[j for j in jobs if j.get('session_id')==data['id'] and j.get('mode')=='facebook']
        active=None
        for job in jobs:
            if job['status'] not in {'completed','stopped'}: active=job
            for item in job['items']:
                key=item['key']
                if item['status'] in TERMINAL or job['status']!='stopped':
                    items[key]={**items[key],**item}
        # Resolving a failure against a manually saved lead supersedes old jobs.
        for candidate in data['candidates']:
            if candidate.get('resolved_status'):
                items[candidate['key']].update(status=candidate['resolved_status'],message=candidate['message'])
        summary={k:v for k,v in data.items() if k!='candidates'}
        counts={s:sum(i['status']==s for i in items.values()) for s in TERMINAL}
        processed=sum(counts.values())
        summary.update(total=len(items),processed=processed,remaining=len(items)-processed,
                       matched=counts['done'],review=counts['review'],skipped=counts['skipped'],failed=counts['failed'],
                       active_job_id=active['id'] if active else None,active_status=active['status'] if active else '',
                       latest_job_id=jobs[-1]['id'] if jobs else None)
        return summary,items

    def get(self,sid):
        with self.store.lock,self.store.connect() as c:
            return self._state(c,self._read(c,sid))[0]

    def list(self):
        with self.store.lock,self.store.connect() as c:
            return [self._state(c,json.loads(r[0]))[0] for r in c.execute('SELECT payload FROM facebook_sessions ORDER BY id DESC').fetchall()]

    def seen_final(self,sid,url):
        key=identity_key(url)
        return any(j.get('session_id')==sid and j.get('mode')=='facebook'
                   and any(i['status'] in {'done','review','skipped'} and i.get('final_url')
                           and identity_key(i['final_url'])==key for i in j['items'])
                   for j in self.store.jobs())

    def ingest(self,source,candidates,product,buyer_type,country):
        source=search_source(source)
        context=[s.strip() for s in (product,buyer_type,country)]
        if not all(context): raise ValueError('请填写产品、客户类型和目标市场')
        key=hashlib.sha256(json.dumps([source,*[s.casefold() for s in context]],ensure_ascii=False).encode()).hexdigest()
        with self.store.lock,self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT id FROM facebook_sessions WHERE session_key=?',(key,)).fetchone()
            data=self._read(c,row[0]) if row else dict(source_url=source,product=context[0],buyer_type=context[1],country=context[2],created_at=now(),candidates=[])
            seen={i['key'] for i in data['candidates']}
            for candidate in candidates:
                try: url=profile_url(candidate['url'])
                except ValueError: continue
                identity=identity_key(url)
                if identity in seen: continue
                exists=c.execute('SELECT 1 FROM identities WHERE key=?',(identity,)).fetchone()
                data['candidates'].append(dict(url=url,key=identity,title=candidate.get('title',''),status='skipped' if exists else 'pending',message='已有客户线索，跳过重复访问' if exists else ''))
                seen.add(identity)
            if not data['candidates']: raise ValueError('当前搜索结果没有可采集的 Facebook 主页，请加载公共主页搜索结果后重试')
            if not row: data['id']=c.execute('INSERT INTO facebook_sessions(session_key,payload) VALUES(?,?)',(key,'{}')).lastrowid
            data['updated_at']=now()
            c.execute('UPDATE facebook_sessions SET payload=? WHERE id=?',(json.dumps(data,ensure_ascii=False),data['id']))
            return self._state(c,data)[0]

    def reserve(self,sid,target_id,retry=False):
        with self.store.lock,self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            data=self._read(c,sid); summary,items=self._state(c,data)
            if summary['active_job_id']: raise ValueError('请先完成、继续或停止本批，再处理下一批')
            selected=[]
            for candidate in data['candidates']:
                item=items[candidate['key']]
                if retry:
                    if item['status']!='failed': continue
                elif item['status'] in TERMINAL: continue
                if c.execute('SELECT 1 FROM identities WHERE key=?',(item['key'],)).fetchone():
                    candidate.update(status='skipped',resolved_status='skipped',message='已有客户线索，跳过重复访问')
                    continue
                if len(selected)<10:
                    selected.append({**candidate,'status':'pending','message':''})
            c.execute('UPDATE facebook_sessions SET payload=? WHERE id=?',(json.dumps(data,ensure_ascii=False),sid))
            if not selected: return None
            job=dict(mode='facebook',session_id=sid,target_id=target_id,retry=retry,product=data['product'],buyer_type=data['buyer_type'],country=data['country'],status='queued',total=len(selected),done=0,review=0,skipped=0,failed=0,message='等待采集本批候选',created_at=now(),items=selected)
            job['id']=c.execute('INSERT INTO jobs(payload) VALUES(?)',('{}',)).lastrowid
            c.execute('UPDATE jobs SET payload=? WHERE id=?',(json.dumps(job,ensure_ascii=False),job['id']))
            return job
