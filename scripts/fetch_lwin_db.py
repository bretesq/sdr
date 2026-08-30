#!/usr/bin/env python3
"""Build a complete local LWIN talkgroup + site database from RadioReference."""
import re, json, time, urllib.request, os, sys

UA={'User-Agent':'Mozilla/5.0'}
def get(u,tries=3):
    for i in range(tries):
        try: return urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=30).read().decode('utf-8','ignore')
        except Exception as e:
            if i==tries-1: raise
            time.sleep(2)

strip=lambda s: re.sub(r'\s+',' ',re.sub(r'<[^>]+>','',s)).strip()
sysh=get('https://www.radioreference.com/db/sid/4347')

# --- categories: id -> name ---
cats={}
for m in re.finditer(r'tgCat/(\d+)', sysh):
    cid=m.group(1); win=sysh[max(0,m.start()-1500):m.start()]
    names=re.findall(r'>\s*([A-Z][^<>{}]{6,70}?)\s*<', win)
    nm=next((n for n in reversed(names) if 'Talkgroup' not in n and 'View' not in n and len(n)>6),'?')
    cats.setdefault(cid, nm.strip())
print(f"categories: {len(cats)}", flush=True)

TR=re.compile(r'<tr[^>]*data-tgdec="(\d+)"[^>]*>(.*?)</tr>', re.S)
TD=re.compile(r'<td[^>]*>(.*?)</td>', re.S)
db={}
for k,(cid,name) in enumerate(sorted(cats.items()),1):
    try: h=get(f'https://www.radioreference.com/db/tgCat/{cid}')
    except Exception as e:
        print(f"  [{k}/{len(cats)}] {name}: FAIL {e}",flush=True); continue
    n=0
    for m in TR.finditer(h):
        dec=int(m.group(1)); tds=TD.findall(m.group(2))
        if len(tds)<6: continue
        mode=tds[2]
        enc='full' if 'rrdb-tgbadge-enc-part' not in mode and 'rrdb-tgbadge-enc' in mode else ('partial' if 'rrdb-tgbadge-enc-part' in mode else 'clear')
        db[dec]={'hex':strip(tds[1]),'mode':strip(tds[2]).replace('Enc','').strip(),'enc':enc,
                 'alpha':strip(tds[3]),'desc':strip(tds[4]),'tag':strip(tds[5]),'cat':name,'tgcat':cid}
        n+=1
    if k%25==0 or n>60: print(f"  [{k}/{len(cats)}] {name}: {n}",flush=True)
    time.sleep(0.5)

os.makedirs('reference',exist_ok=True)
json.dump(db,open('reference/lwin_talkgroups.json','w'),indent=1,sort_keys=True)
print(f"\nSAVED reference/lwin_talkgroups.json : {len(db)} talkgroups",flush=True)
enc=sum(1 for v in db.values() if v['enc']=='full')
print(f"  fully encrypted: {enc}  partial: {sum(1 for v in db.values() if v['enc']=='partial')}  clear: {sum(1 for v in db.values() if v['enc']=='clear')}")

# --- sites ---
sites={}
for m in re.finditer(r'(\d+) \((\d+)\) (\d+) \(([0-9a-fA-F]+)\) (\S+) ([^\n<]{3,60}?)\s{2,}', strip(sysh)):
    pass
json.dump(cats,open('reference/lwin_categories.json','w'),indent=1)
print("SAVED reference/lwin_categories.json")
