import json
import sqlite3
import threading
from contextlib import nullcontext
from datetime import datetime,timezone
from pathlib import Path
from .url_rules import identity_key

STATUSES={'new','contacted','replied','qualified','archived'}
LIST_FIELDS=('emails','phones','whatsapp','social_links','keywords','reasons')


def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Store:
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        with self.connect() as c:
            c.executescript('''PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS leads(id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS identities(key TEXT PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id));
                CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
            ''')

    def connect(self):
        c=sqlite3.connect(self.path,timeout=15); c.execute('PRAGMA foreign_keys=ON'); return c

    def has_identity(self,url):
        with self.connect() as c:
            return bool(c.execute('SELECT 1 FROM identities WHERE key=?',(identity_key(url),)).fetchone())

    def add_alias(self,source,alias,connection):
        row=connection.execute('SELECT lead_id FROM identities WHERE key=?',(identity_key(source),)).fetchone()
        if row: connection.execute('INSERT OR IGNORE INTO identities VALUES(?,?)',(identity_key(alias),row[0]))

    def save_lead(self,data,connection=None,aliases=()):
        keys={identity_key(data['source_url'])}
        keys.update(identity_key(url) for url in aliases)
        if data.get('website'): keys.add(identity_key(data['website']))
        # Mentioned social links may belong to partners or a web designer. Only
        # the page itself and its explicit official website establish identity.
        stamp=now()
        with self.lock,(nullcontext(connection) if connection is not None else self.connect()) as c:
            rows=c.execute('SELECT key,lead_id FROM identities WHERE key IN ('+','.join('?' for _ in keys)+')',tuple(keys)).fetchall()
            identity_ids=dict(rows); ids=sorted(set(identity_ids.values()))
            old_records={lid:json.loads(c.execute('SELECT payload FROM leads WHERE id=?',(lid,)).fetchone()[0]) for lid in ids}
            base={**data,'status':'new','notes':'','sources':[],'created_at':stamp}
            edited=[lid for lid,old in old_records.items() if old.get('manual_updated_at') or old.get('status')!='new' or old.get('notes')]
            if len(edited)>1:
                # Keep independently edited records intact rather than choose
                # which human notes/status to overwrite during an automatic merge.
                keep=identity_ids.get(identity_key(data['source_url']),edited[0])
                ids=[keep]; keys={key for key in keys if key not in identity_ids or identity_ids[key]==keep}
                base['merge_notice']='发现关联线索，但双方均有人工跟进记录，已分别保留，避免覆盖备注。'
            # Deterministic consolidation only when explicit identities overlap.
            for lid in ids:
                old=old_records[lid]
                for field in LIST_FIELDS: base[field]=sorted(set(base.get(field,[])+old.get(field,[])))
                base['sources']+=old.get('sources',[])
                for field in ['name','name_source','website','country_evidence','target_country']:
                    if old.get(field): base[field]=old[field]
                if old.get('status')!='new': base['status']=old['status']
                if old.get('notes'): base['notes']=old['notes']
                for field in ['manual_updated_at','merge_notice']:
                    if old.get(field): base[field]=old[field]
                for field in ['screening','screening_reason','buyer_type']:
                    if old.get(field) and not base.get(field): base[field]=old[field]
                base['created_at']=min(base['created_at'],old['created_at'])
                base['score']=max(base.get('score',0),old.get('score',0))
            # Prefer the official company name over a profile handle or Contact title.
            if data.get('channel')=='website' and data.get('name','').lower() not in {'contact','contact us','about','about us'}:
                base['name']=data['name']; base['name_source']=data.get('name_source',''); base['channel']='website'
            source_map={s['url']:s for s in base['sources']}
            source_map[data['source_url']]={'url':data['source_url'],'captured_at':stamp}
            base['sources']=list(source_map.values()); base['updated_at']=stamp
            base.pop('contact_pages',None)
            lid=ids[0] if ids else c.execute('INSERT INTO leads(payload) VALUES(?)',('{}',)).lastrowid
            base['id']=lid
            c.execute('UPDATE leads SET payload=? WHERE id=?',(json.dumps(base,ensure_ascii=False),lid))
            for other in ids[1:]:
                c.execute('UPDATE identities SET lead_id=? WHERE lead_id=?',(lid,other))
                c.execute('DELETE FROM leads WHERE id=?',(other,))
            for key in keys: c.execute('INSERT OR REPLACE INTO identities VALUES(?,?)',(key,lid))
            return base

    def get_lead(self,lid):
        with self.connect() as c: row=c.execute('SELECT payload FROM leads WHERE id=?',(lid,)).fetchone()
        if not row: raise ValueError('线索不存在')
        return json.loads(row[0])

    def update_lead(self,lid,status,notes):
        if status not in STATUSES: raise ValueError('跟进状态无效')
        if not isinstance(notes,str) or len(notes)>5000: raise ValueError('备注最多5000字')
        with self.lock,self.connect() as c:
            data=self.get_lead(lid); data.update(status=status,notes=notes,updated_at=now(),manual_updated_at=now())
            c.execute('UPDATE leads SET payload=? WHERE id=?',(json.dumps(data,ensure_ascii=False),lid))
        return data

    def list_leads(self,q='',status='',page=1,all_rows=False):
        if status and status not in STATUSES: raise ValueError('跟进状态无效')
        with self.connect() as c: rows=[json.loads(r[0]) for r in c.execute('SELECT payload FROM leads ORDER BY id DESC')]
        q=q.strip().casefold()
        rows=[r for r in rows if (not status or r['status']==status) and (not q or q in json.dumps(r,ensure_ascii=False).casefold())]
        page=max(1,page)
        return {'items':rows if all_rows else rows[(page-1)*25:page*25],'total':len(rows),'page':page,'page_size':25}

    def save_job(self,job,connection=None):
        with self.lock,(nullcontext(connection) if connection is not None else self.connect()) as c:
            job=dict(job)
            if not job.get('id'): job['id']=c.execute('INSERT INTO jobs(payload) VALUES(?)',('{}',)).lastrowid
            c.execute('UPDATE jobs SET payload=? WHERE id=?',(json.dumps(job,ensure_ascii=False),job['id']))
        return job

    def get_job(self,jid):
        with self.connect() as c: row=c.execute('SELECT payload FROM jobs WHERE id=?',(jid,)).fetchone()
        if not row: raise ValueError('任务不存在')
        return json.loads(row[0])

    def jobs(self):
        with self.connect() as c: return [json.loads(r[0]) for r in c.execute('SELECT payload FROM jobs ORDER BY id DESC')]

    def recover_jobs(self):
        for job in self.jobs():
            changed=False
            if job['status'] in {'running','queued'}:
                job['status']='paused'; job['message']='程序重新启动，待你确认后继续'
                changed=True
            for item in job['items']:
                if item['status']=='running':
                    item['status']='skipped' if job['status']=='stopped' and job['mode']!='facebook' else 'pending'; changed=True
            if changed: self.save_job(job)
