import os, random, secrets, string, json, time
from collections import deque
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT=Path(__file__).parent
ROLES=['Police','Colonel','Thief','Terrorist','Tax Agent','Business Woman','Politician']
ACTIONS=['income','aid','coup']+ROLES
BLOCKS={'aid':'Tax Agent','Police':'Police','Thief':'Thief','Terrorist':'Colonel'}
rooms={}
app=FastAPI(title='Elmakina Live')
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
@app.get('/')
def index(): return FileResponse(ROOT/'static/index.html')
@app.get('/health')
def health(): return {'ok':True,'rooms':len(rooms)}

def shuffle(deck): random.SystemRandom().shuffle(deck)
def alive(p): return len(p['cards'])>0
def log(r,s):r['log'].append(s);r['log']=r['log'][-65:]
def player(r,pid):return next((p for p in r['players'] if p['id']==pid),None)
def name(r,pid):
 p=player(r,pid);return p['name'] if p else '?'
def draw(r):
 if not r['deck']: raise ValueError('The draw deck is empty.')
 return r['deck'].pop()
def discard(r,role):r['deck'].append(role);shuffle(r['deck'])
def banktake(r,p,n):
 n=min(n,r['bank'],12-p['coins']);p['coins']+=n;r['bank']-=n;return n
def pay(r,p,n):p['coins']-=n;r['bank']+=n
def transfer(a,b,n):
 n=min(n,a['coins'],12-b['coins']);a['coins']-=n;b['coins']+=n;return n

def endturn(r):
 r['pending']=None;r['phase']='turn';r['losses']=[];r['resume']=None
 survivors=[p for p in r['players'] if alive(p)]
 if len(survivors)<=1:
  r['phase']='finished';log(r,f"🏆 {survivors[0]['name']} wins!" if survivors else 'No survivor.');return
 previous=r['turn'];i=next(j for j,p in enumerate(r['players']) if p['id']==previous)
 for k in range(1,len(r['players'])+1):
  j=(i+k)%len(r['players']);p=r['players'][j]
  if alive(p):
   r['turn']=p['id'];r['round']+=(j<=i);break

def loss(r,pid,why,resume):
 p=player(r,pid)
 if not p or not alive(p):return resume(r)
 r['phase']='loss';r['lossPlayer']=pid;r['lossReason']=why;r['resume']=resume
 log(r,f"{p['name']} must discard one card ({why}).")

def resolve_action(r):
 q=r['pending'];a=player(r,q['actor']);action=q['action'];t=player(r,q.get('target'))
 if action=='income':log(r,f"{a['name']} collected {banktake(r,a,1)} coin.");return endturn(r)
 if action=='aid':log(r,f"{a['name']} collected {banktake(r,a,2)} aid coins.");return endturn(r)
 if action=='coup':
  pay(r,a,7);log(r,f"{a['name']} paid 7 coins to coup {t['name']}.")
  if t['coins']>=9:
   r['phase']='coup';return
  return loss(r,t['id'],'coup',endturn)
 if action=='Terrorist':
  pay(r,a,3);log(r,f"{a['name']} launched a Terrorist attack against {t['name']} for 3 coins.")
  return loss(r,t['id'],'Terrorist attack',endturn)
 if action=='Thief':
  n=transfer(t,a,2);log(r,f"{a['name']} stole {n} coins from {t['name']}.");return endturn(r)
 if action=='Tax Agent':
  amount=0
  for x in r['players']:
   if x['id']!=a['id'] and alive(x) and x['coins']>=7:amount+=transfer(x,a,1)
  log(r,f"{a['name']} collected {amount} tax coins.");return endturn(r)
 if action=='Business Woman':
  log(r,f"{a['name']} collected {banktake(r,a,4)} coins as Business Woman.")
  q['taxDone']=[];return tax_window(r)
 if action=='Colonel':
  pay(r,a,4)
  role=q['accused'];match=role in t['cards']
  if match:
   t['cards'].remove(role);discard(r,role);log(r,f"{t['name']} revealed {role} — Colonel accusation SUCCESS. Card removed.")
   if not alive(t):log(r,f"{t['name']} was eliminated.")
   return endturn(r)
  n=transfer(a,t,0) # payment already returned to bank; move exact cost out of bank to defender
  give=min(4,r['bank'],12-t['coins']);r['bank']-=give;t['coins']+=give
  log(r,f"Colonel accusation FALSE. {t['name']} receives {give} coins and must show and exchange their full hand.")
  r['phase']='colonelProof';return
 if action=='Police':
  q['inspected']=random.randrange(len(t['cards']));r['phase']='police';return
 if action=='Politician':
  n=len(a['cards']);q['choices']=a['cards']+[draw(r) for _ in range(n)];r['phase']='politician';return

def resolve_colonel_challenged(r):
 q=r['pending'];a=player(r,q['actor']);t=player(r,q['target']);challenger=q['colonelChallenger']
 pay(r,a,4)
 if q['accused'] in t['cards']:
  t['cards'].remove(q['accused']);discard(r,q['accused'])
  log(r,f"{t['name']} revealed {q['accused']} — Colonel accusation SUCCESS. Card removed.")
  if not alive(t):log(r,f"{t['name']} was eliminated.")
  return loss(r,challenger,'failed challenge to truthful Colonel',endturn)
 give=min(4,r['bank'],12-t['coins']);r['bank']-=give;t['coins']+=give
 log(r,f"Colonel accusation FALSE. {t['name']} receives {give} coins and must exchange their full hand.")
 # Still apply the failed bluff challenge penalty before the mistaken accusation exchange.
 return loss(r,challenger,'failed challenge to truthful Colonel',lambda room: set_colonel_proof(room))

def set_colonel_proof(r):r['phase']='colonelProof'

def after_verified(r):
 q=r['pending'];r['phase']='reaction';r['passes']=[];q['verified']=True
 # Allow a block even after a failed challenge, avoiding double challenge of the original actor.

def resolve_block(r):
 q=r['pending'];log(r,f"{name(r,q['blocker'])}'s {q['blockRole']} block stands. Action canceled.");endturn(r)

def public(r,pid):
 p=player(r,pid);q=r['pending'];phase=r['phase'];me=pid
 allowed={}
 if phase=='turn' and me==r['turn']:
  allowed={'actions':ACTIONS}
 if phase=='reaction' and q and alive(p) and me!=q['actor']:
  waiting=me not in r.get('passes',[]) and me not in q.get('taxClaims',[])
  allowed={'canChallenge':waiting and q['action'] not in ('aid','income','coup') and not q.get('verified',False),'canBlock':waiting and q['action'] in BLOCKS,'canPass':waiting,'canTax':waiting and q['action']=='Business Woman'}
 if phase=='blockReaction' and q and alive(p) and me!=q['blocker']:
  allowed={'canChallenge':True,'canPass':me not in r.get('passes',[])}
 if phase=='loss' and me==r.get('lossPlayer'):allowed={'lose':True}
 if phase=='coup' and me==q['target']:allowed={'survive':True}
 if phase=='police' and me==q['actor']:allowed={'inspect':True}
 if phase=='politician' and me==q['actor']:allowed={'choose':True}
 if phase=='colonelProof' and me==q['target']:allowed={'exchange':True}
 if phase=='tax' and alive(p) and me!=q['actor'] and me not in q.get('taxDone',[]):allowed={'tax':True,'canPass':me not in r.get('passes',[])}
 if phase=='taxChallenge' and alive(p) and me==q['actor']:allowed={'canChallenge':True,'canPass':True}
 data={'code':r['code'],'you':pid,'host':r['host'],'started':r['started'],'phase':phase,'round':r['round'],'turn':r['turn'],'bank':r['bank'],'players':[{'id':x['id'],'name':x['name'],'coins':x['coins'],'remaining':len(x['cards']),'connected':bool(x.get('sockets'))} for x in r['players']], 'hand':p['cards'] if r['started'] else [],'log':r['log'][-25:],'pending':None if not q else {k:v for k,v in q.items() if k in ['actor','action','target','accused','blocker','blockRole','taxClaimant','taxClaims','verified']},'allowed':allowed,'chat':r.get('chat',[])[-60:],'diceRounds':r.get('diceRounds',[]) if phase=='dice' else [],'firstPlayer':r.get('firstPlayer'),'diceReady':r.get('diceReady',[]) if phase=='dice' else [],'lossReason':r.get('lossReason') if phase=='loss' and me==r.get('lossPlayer') else None}
 if phase=='police' and me==q['actor']:data['inspectedCard']=player(r,q['target'])['cards'][q['inspected']]
 if phase=='politician' and me==q['actor']:data['choices']=q['choices']
 if phase=='colonelProof' and me==q['target']:data['proofHand']=p['cards']
 if phase=='finished':data['winner']=next((x['name'] for x in r['players'] if alive(x)),None)
 return data

async def broadcast(r):
 for p in r['players']:
  for ws in list(p.get('sockets',[])):
   try:await ws.send_json({'type':'state','state':public(r,p['id'])})
   except:p['sockets'].discard(ws)

def execute(r,pid,msg):
 typ=msg.get('type');p=player(r,pid);phase=r['phase'];q=r['pending']
 if typ=='start':
  if r['started'] or pid!=r['host'] or not 2<=len(r['players'])<=10:raise ValueError('Only host can start a game with 2–10 players.')
  r['started']=True;r['deck']=[role for role in ROLES for _ in range(3)];shuffle(r['deck'])
  r['bank']=200-2*len(r['players'])
  hand_size=3 if len(r['players'])==2 else 2
  for x in r['players']:x['cards']=[draw(r) for _ in range(hand_size)];x['coins']=2
  # All players roll a fair server-side six-sided die. Tied top rolls reroll until unique.
  contenders=list(r['players']);rounds=[]
  while True:
   rolls={x['id']:secrets.randbelow(6)+1 for x in contenders}
   rounds.append([{'id':x['id'],'name':x['name'],'roll':rolls[x['id']]} for x in contenders])
   highest=max(rolls.values());contenders=[x for x in contenders if rolls[x['id']]==highest]
   if len(contenders)==1:break
  winner=contenders[0];r['diceRounds']=rounds;r['firstPlayer']=winner['id']
  r['turn']=winner['id'];r['phase']='dice';r['diceReady']=[]
  log(r,'Dice rolled to select the first player: '+winner['name']+'.')
  log(r,f'Game started. {hand_size} private cards each.')
  return
 if typ=='dice_ready' and phase=='dice':
  if pid not in r['diceReady']:r['diceReady'].append(pid)
  if all(x['id'] in r['diceReady'] for x in r['players']):r['phase']='turn'
  return
 if typ=='chat':
  message=str(msg.get('message','')).strip()[:400]
  if not message:raise ValueError('Enter a message.')
  if len(r['chat'])>=100:r['chat']=r['chat'][-99:]
  r['chat'].append({'player':p['name'],'id':pid,'text':message,'at':int(time.time())})
  return
 if not r['started']:raise ValueError('Waiting for the host to start.')
 if typ=='action' and phase=='turn' and r['turn']==pid:
  action=msg.get('action');target=msg.get('target');role=msg.get('accused')
  if action not in ACTIONS:raise ValueError('Invalid action.')
  cost={'coup':7,'Terrorist':3,'Colonel':4}.get(action,0)
  if p['coins']<cost:raise ValueError('Not enough coins.')
  if action in ['coup','Terrorist','Colonel','Police','Thief']:
   t=player(r,target)
   if not t or not alive(t) or target==pid:raise ValueError('Choose another living player.')
  else:target=None
  if action=='Colonel' and role not in ROLES:raise ValueError('Choose a role to accuse.')
  if action!='Colonel':role=None
  r['pending']={'actor':pid,'action':action,'target':target,'accused':role,'verified':False,'taxClaims':[],'taxDone':[]};r['passes']=[]
  log(r,f"{p['name']} declared {action}"+(f" against {name(r,target)}" if target else '')+(f" (accusing {role})" if action=='Colonel' else '')+'.')
  if action in ['income','coup']:return resolve_action(r)
  r['phase']='reaction';return
 if typ=='tax' and phase=='reaction' and q['action']=='Business Woman' and pid!=q['actor'] and alive(p) and pid not in q['taxClaims'] and pid not in r['passes']:
  q['taxClaims'].append(pid)
  log(r,f"{p['name']} requested one coin as Tax Agent from {name(r,q['actor'])}.")
  if reaction_complete(r):resolve_action(r)
  return
 if typ=='pass' and phase in ['reaction','blockReaction','tax','taxChallenge']:
  if pid==q['actor'] and phase in ['reaction','tax']:raise ValueError('Acting player cannot pass for opponents.')
  if phase=='blockReaction' and pid==q['blocker']:raise ValueError('Blocker cannot pass.')
  if phase=='taxChallenge' and pid!=q['actor']:raise ValueError('Only Business Woman chooses to challenge a tax request.')
  if not alive(p):raise ValueError('Eliminated players cannot react.')
  if phase=='tax' and pid in q['taxDone']:raise ValueError('Already claimed tax.')
  if phase=='reaction' and (pid in r['passes'] or pid in q.get('taxClaims',[])):raise ValueError('You have already responded.')
  r['passes']=list(set(r.get('passes',[])+[pid]))
  if phase=='reaction':required=[x['id'] for x in r['players'] if alive(x) and x['id']!=q['actor'] and x['id'] not in q.get('taxClaims',[])]
  elif phase=='blockReaction':required=[x['id'] for x in r['players'] if alive(x) and x['id']!=q['blocker']]
  elif phase=='tax':required=[x['id'] for x in r['players'] if alive(x) and x['id']!=q['actor'] and x['id'] not in q['taxDone']]
  else:required=[q['actor']]
  if all(x in r['passes'] for x in required):
   if phase=='reaction':resolve_action(r)
   elif phase=='blockReaction':resolve_block(r)
   elif phase=='tax':endturn(r)
   else:pay_tax(r)
  return
 if typ=='challenge' and phase in ('reaction','blockReaction','taxChallenge'):
  if not alive(p):raise ValueError('Eliminated players cannot challenge.')
  if phase=='reaction':
   if pid in r['passes'] or pid in q.get('taxClaims',[]):raise ValueError('You have already responded.')
   if q['action'] in ('aid','income','coup') or q.get('verified') or pid==q['actor']:raise ValueError('This claim cannot be challenged.')
   claimant=q['actor'];role=q['action']
  elif phase=='blockReaction':claimant=q['blocker'];role=q['blockRole']
  else:
   if pid!=q['actor']:raise ValueError('Only Business Woman can challenge these Tax Agent claims.')
   claimant=q['taxClaimant'];role='Tax Agent'
  if pid==claimant:raise ValueError('Cannot challenge yourself.')
  claimant_p=player(r,claimant);truth=role in claimant_p['cards']
  log(r,f"{p['name']} challenged {claimant_p['name']}'s {role}: {'TRUTH' if truth else 'BLUFF'}.")
  if truth:
   claimant_p['cards'].remove(role);discard(r,role);claimant_p['cards'].append(draw(r))
   log(r,f"{claimant_p['name']} proved {role} and exchanged that card.")
   def continuation(room):
    if phase=='reaction':
     return after_verified(room)
    if phase=='blockReaction':return resolve_block(room)
    return pay_tax(room)
   if phase=='reaction' and role=='Colonel':
    # Freeze target's matching card BEFORE the challenge penalty can discard it.
    r['pending']['colonelChallenger']=pid
    return resolve_colonel_challenged(r)
   return loss(r,pid,'failed bluff challenge',continuation)
  def continuation(room):
   if phase=='reaction':return endturn(room)
   if phase=='blockReaction':return resolve_action(room)
   return tax_window(room)
  return loss(r,claimant,'false role claim',continuation)
 if typ=='block' and phase=='reaction' and q and q['action'] in BLOCKS and pid!=q['actor'] and alive(p):
  q['blocker']=pid;q['blockRole']=BLOCKS[q['action']];r['phase']='blockReaction';r['passes']=[];log(r,f"{p['name']} claimed {q['blockRole']} to block.");return
 if typ=='lose' and phase=='loss' and pid==r.get('lossPlayer'):
  idx=msg.get('index');
  if type(idx)!=int or not 0<=idx<len(p['cards']):raise ValueError('Choose one of your cards.')
  card=p['cards'].pop(idx);discard(r,card);log(r,f"{p['name']} lost one hidden card.")
  if not alive(p):log(r,f"{p['name']} was eliminated.")
  fn=r['resume'];r['resume']=None;return fn(r)
 if typ=='coup' and phase=='coup' and pid==q['target']:
  if msg.get('survive'):
   if p['coins']<9:raise ValueError('Need 9 coins to survive.')
   pay(r,p,9);log(r,f"{p['name']} paid 9 coins to survive.");return endturn(r)
  return loss(r,pid,'coup',endturn)
 if typ=='police' and phase=='police' and pid==q['actor']:
  if msg.get('replace'):
   t=player(r,q['target']);old=t['cards'][q['inspected']];discard(r,old);t['cards'][q['inspected']]=draw(r);log(r,f"{name(r,pid)} ordered {t['name']} to exchange an inspected card.")
  else:log(r,f"{name(r,pid)} kept the inspected card unchanged.")
  return endturn(r)
 if typ=='politician' and phase=='politician' and pid==q['actor']:
  indices=msg.get('indices');n=len(p['cards'])
  if not isinstance(indices,list) or len(indices)!=n or len(set(indices))!=n or any(type(i)!=int or i<0 or i>=len(q['choices']) for i in indices):raise ValueError(f'Select exactly {n} unique cards.')
  selected=[q['choices'][i] for i in indices];extra=[card for j,card in enumerate(q['choices']) if j not in indices]
  p['cards']=selected
  for card in extra:discard(r,card)
  log(r,f"{p['name']} exchanged cards as Politician.");return endturn(r)
 if typ=='exchange' and phase=='colonelProof' and pid==q['target']:
  n=len(p['cards']);log(r,f"{p['name']} showed their cards: {', '.join(p['cards'])}.")
  for c in p['cards']:discard(r,c)
  p['cards']=[draw(r) for _ in range(n)]
  return endturn(r)
 if typ=='tax' and phase=='tax' and pid!=q['actor'] and pid not in q['taxDone'] and alive(p):
  q['taxClaimant']=pid;q['taxDone'].append(pid);r['phase']='taxChallenge';r['passes']=[]
  log(r,f"{p['name']} claimed Tax Agent to collect one coin from {name(r,q['actor'])}.");return
 raise ValueError('This move is not valid right now.')

def reaction_complete(r):
 q=r['pending']
 return all(p['id'] in r['passes'] or p['id'] in q.get('taxClaims',[]) for p in r['players'] if alive(p) and p['id']!=q['actor'])

def tax_window(r):
 q=r['pending'];r['passes']=[]
 queued=[pid for pid in q.get('taxClaims',[]) if pid not in q['taxDone'] and alive(player(r,pid))]
 if not queued:return endturn(r)
 q['taxClaimant']=queued[0];q['taxDone'].append(queued[0]);r['phase']='taxChallenge'
def pay_tax(r):
 q=r['pending'];a=player(r,q['actor']);t=player(r,q['taxClaimant']);n=transfer(a,t,1)
 log(r,f"{t['name']} collected {n} coin from {a['name']} as tax.");tax_window(r)

def create_room(pid,n):
 code=''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(5))
 while code in rooms:code=''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(5))
 r={'code':code,'host':pid,'players':[{'id':pid,'name':n,'coins':0,'cards':[],'sockets':set()}],'started':False,'deck':[],'bank':200,'turn':pid,'round':1,'phase':'lobby','pending':None,'log':[],'passes':[],'losses':[],'resume':None,'updated':time.time(),'chat':[],'diceRounds':[],'diceReady':[],'firstPlayer':None}
 rooms[code]=r;return r

@app.websocket('/ws')
async def socket(ws:WebSocket):
 await ws.accept();r=None;pid=None
 try:
  while True:
   msg=await ws.receive_json();typ=msg.get('type')
   if r is None:
    if typ not in ('create','join'):raise ValueError('Create or join a room first.')
    n=str(msg.get('name','')).strip()[:24]
    if not n:raise ValueError('Enter your name.')
    token=str(msg.get('token') or secrets.token_urlsafe(20))[:120]
    if typ=='create':
     r=create_room(token,n)
    else:
     code=str(msg.get('code','')).strip().upper();r=rooms.get(code)
     if not r:raise ValueError('Room not found. Check the code.')
     existing=player(r,token)
     if existing:
      pid=token
     elif r['started'] or len(r['players'])>=10:raise ValueError('Room is started or full. Only existing players may reconnect.')
     else:
      if any(x['name'].lower()==n.lower() for x in r['players']):raise ValueError('Choose a different name.')
      r['players'].append({'id':token,'name':n,'coins':0,'cards':[],'sockets':set()})
    pid=token;player(r,pid)['sockets'].add(ws);await ws.send_json({'type':'joined','code':r['code'],'token':pid});await broadcast(r);continue
   if typ=='ping':await ws.send_json({'type':'pong'});continue
   try:
    execute(r,pid,msg);r['updated']=time.time();await broadcast(r)
   except (ValueError,KeyError,TypeError,IndexError) as e:
    await ws.send_json({'type':'error','message':str(e)})
 except WebSocketDisconnect:pass
 except Exception as e:
  try:await ws.send_json({'type':'error','message':str(e)})
  except:pass
 finally:
  if r and pid:
   player(r,pid)['sockets'].discard(ws)
   if r['phase']=='lobby' and not r['started'] and not player(r,pid)['sockets']:
    # preserve disconnected seat briefly for refresh/reconnect
    pass
   await broadcast(r)
