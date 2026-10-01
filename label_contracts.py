import json, collections, time, requests, os
from rpc import call
T={v:k for k,v in json.load(open('data/topics.json')).items()}
TR=T['Transfer(address,address,uint256)']
SNAP=25745731
addrs=set()
for name in ['snusd','nusd']:
  d=json.load(open(f'data/logs_{name}.json')); b=collections.defaultdict(int)
  for l in d['logs']:
    if l['topics'][0]!=TR: continue
    if int(l['blockNumber'],16)>SNAP: continue
    f='0x'+l['topics'][1][-40:]; t='0x'+l['topics'][2][-40:]; v=int(l['data'],16)
    b[f]-=v; b[t]+=v
  addrs |= {a for a,v in b.items() if v>0}
# also redeemers
d=json.load(open('data/logs_redemption.json'))
for l in d['logs']:
  if l['topics'][0]==T['Redeemed(address,uint256,uint256)']: addrs.add('0x'+l['topics'][1][-40:])
addrs.discard('0x'+'0'*40)
fn='data/address_meta.json'
meta=json.load(open(fn)) if os.path.exists(fn) else {}
for a in sorted(addrs):
  if a in meta: continue
  code=call('eth_getCode',[a,'latest'])
  m={'code_len':(len(code)-2)//2}
  if code.startswith('0xef0100'): m['kind']='eip7702'
  elif code=='0x': m['kind']='eoa'
  else:
    m['kind']='contract'
    try:
      j=requests.get(f'https://eth.blockscout.com/api/v2/addresses/{a}',timeout=30).json()
      m['name']=j.get('name'); m['tags']=[t.get('display_name') for t in (j.get('public_tags') or [])]
      m['proxy']=j.get('proxy_type'); m['impl']=[(i.get('name'),i.get('address_hash') or i.get('address')) for i in (j.get('implementations') or [])]
      m['token']=j.get('token') and {k:j['token'].get(k) for k in ['name','symbol','type']}
    except Exception as e: m['err']=str(e)
    time.sleep(0.25)
  meta[a]=m
  if len(meta)%50==0: json.dump(meta,open(fn,'w'),indent=1); print(len(meta),flush=True)
json.dump(meta,open(fn,'w'),indent=1)
print(len(meta), collections.Counter(m['kind'] for m in meta.values()))
