"""Forward paper indices only. No order client, live endpoint or personal account balance."""
import datetime as dt
import json, math, os, sys, urllib.parse, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo
NY=ZoneInfo("America/New_York")
ASSETS=("SPY","QQQ","GLD","BIL")
NAMES=("Trend3","Conditional QQQ","SPY","QQQ")
CASES=("gross","ordinary30","bil_exempt")
FEE=.001
def weights(name,on):
    s,q,g=(int(bool(on[x])) for x in ASSETS[:3])
    if name=="Trend3": w=[.8/3*s,.2+.8/3*q,.8/3*g]
    elif name=="Conditional QQQ": w=[.20*s,.15+.45*q,.20*g]
    elif name in ASSETS[:2]: w=[float(name=="SPY"),float(name=="QQQ"),0.]
    else: raise ValueError("unknown model")
    return w+[max(0.,1-sum(w))]
def new_book():
    return dict(v=[0.]*4,cash=100.,peak=100.,dd=0.,worst=0.,tax=0.,nav=100.)
def mark(book,market,target,case):
    prev=book["v"];op=[p*a[0] for p,a in zip(prev,market)]
    pre=sum(op)+book["cash"]
    if target is not None:
        volume=sum(abs(w*pre-x) for w,x in zip(target,op));post=pre-FEE*volume
        op=[w*post for w in target];cash=max(0.,post-sum(op))
    else:cash=book["cash"]
    # Prior close owns ex-date income, including when sold at this open.
    # Reinvest in the paying fund at close if still held; otherwise credit cash.
    v=[x*a[1] for x,a in zip(op,market)]
    for j,(p,a) in enumerate(zip(prev,market)):
        gross=p*a[2];rate=0. if case=="gross" or case=="bil_exempt" and j==3 else .30
        tax=gross*rate;book["tax"]+=tax
        if op[j]>0:v[j]+=gross-tax
        else:cash+=gross-tax
    nav=cash+sum(v)
    if not math.isfinite(nav) or nav<=0:raise ValueError("invalid paper mark")
    peak=max(nav,book["peak"]);dd=nav/peak-1.
    book.update(v=v,cash=cash,nav=nav,peak=peak,dd=dd,worst=min(dd,book["worst"]))
def bar_ratios(today,prev):
    # Split-adjusted prices remove split jumps. Separately isolate the vendor's
    # cash-dividend adjustment. Never deduct withholding from capital appreciation.
    o,c,a=today;_,pc,pa=prev
    discount=1-(pa/pc)/(a/c)
    if abs(discount)<.000015:discount=0.
    if not (-.000001<=discount<=.05):raise ValueError("material non-cash adjustment; review required")
    return (o/pc,c/o,max(0.,discount))
def blank():return dict(version="shadow-qqq-v1",plans=[],history=[],books={},asof=None)
def stamp(day,hm):return dt.datetime.fromisoformat(day+'T'+hm).replace(tzinfo=NY)
def advance(ledger,calendar,bars,now):
    if ledger.get("version")!="shadow-qqq-v1": raise ValueError("ledger version mismatch")
    dates=sorted(calendar)
    done=[d for d in dates if stamp(d,calendar[d][1])+dt.timedelta(minutes=90)<=now]
    future=[d for d in dates if stamp(d,calendar[d][0])>now]
    if not done or not future:raise ValueError("calendar unavailable")
    last=done[-1];nxt=future[0]
    if not ledger['books']:
        ledger.update(start=nxt,asof=last,books={n:{k:new_book() for k in CASES} for n in NAMES})
    if ledger['asof'] not in dates or ledger['asof']>last:raise ValueError("missing ledger anchor or clock drift")
    plans=ledger['plans']
    for d in (d for d in done if d>ledger['asof']):
        p=dates[dates.index(d)-1];plan=next((v for v in plans if v['open']==d),None)
        if plan and dt.datetime.fromisoformat(plan['locked'])>=stamp(d,calendar[d][0]):raise ValueError("late order plan")
        obs=[bar_ratios(bars[s][d],bars[s][p]) for s in ASSETS]
        for n in NAMES:
            for k in CASES:mark(ledger['books'][n][k],obs,weights(n,plan['on']) if plan else None,k)
        if d>=ledger['start']:
            ledger['history'].append({'date':d,'net':{n:round(ledger['books'][n]['ordinary30']['nav'],8) for n in NAMES}})
        ledger['asof']=d
    # Latest fully closed month before next executable opening. If a scheduling
    # outage missed the normal first open, log a later prospective fill, never backdate.
    ends=[d for i,d in enumerate(dates[:-1]) if d<=last and dates[i+1][:7]!=d[:7]]
    sig=ends[-1]
    if not plans or plans[-1]['signal']!=sig:
        i=dates.index(sig);win=dates[i-199:i+1]
        if len(win)!=200:raise ValueError("short signal history")
        on={s:bars[s][sig][2]>=sum(bars[s][d][2] for d in win)/200 for s in ASSETS[:3]}
        plans.append(dict(open=nxt,signal=sig,on=on,locked=now.isoformat(),kind='launch' if not plans else 'monthly' if dates[dates.index(sig)+1]==nxt else 'late-forward'))
    due=next(d for d in dates if d>last)
    ledger['next_close_due']=(stamp(due,calendar[due][1])+dt.timedelta(hours=8)).isoformat()
    return ledger
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):raise ValueError("unexpected redirect")
READ=urllib.request.build_opener(NoRedirect())
def get(url,params):
    if url not in ("https://data.alpaca.markets/v2/stocks/bars","https://paper-api.alpaca.markets/v2/calendar"):raise ValueError("unapproved market-data URL")
    req=urllib.request.Request(url+"?"+urllib.parse.urlencode(params),headers={"APCA-API-KEY-ID":os.environ['DATA_KEY'],"APCA-API-SECRET-KEY":os.environ['DATA_SECRET']},method="GET")
    with READ.open(req,timeout=45) as r:return json.load(r)
def inputs(now):
    day=now.astimezone(NY).date();start=(day-dt.timedelta(days=430)).isoformat()
    cal=get("https://paper-api.alpaca.markets/v2/calendar",{"start":start,"end":(day+dt.timedelta(days=12)).isoformat()})
    calendar={r['date']:(r['open'],r['close']) for r in cal};parts=[]
    for adj in ['split','split,dividend']:
        q=dict(symbols=','.join(ASSETS),start=start,end=(now.astimezone(dt.timezone.utc)-dt.timedelta(minutes=25)).isoformat(),adjustment=adj,timeframe="1Day",feed="sip",limit=10000)
        out={s:{} for s in ASSETS}
        while True:
            reply=get("https://data.alpaca.markets/v2/stocks/bars",q)
            for s,rows in reply['bars'].items():
                for row in rows:
                    d=dt.datetime.fromisoformat(row['t'].replace('Z','+00:00')).astimezone(NY).date().isoformat()
                    vals=tuple(float(row[k]) for k in ['o','c'])
                    if not all(math.isfinite(v) and v>0 for v in vals) or d in out[s]:raise ValueError("bad bar")
                    out[s][d]=vals
            page=reply.get('next_page_token')
            if not page:break
            q['page_token']=page
        parts.append(out)
    bars={s:{d:(o,c,parts[1][s][d][1]) for d,(o,c) in parts[0][s].items() if d in parts[1][s]} for s in ASSETS}
    return calendar,bars
def main():
    dest=Path(sys.argv[1]);state=json.loads(dest.read_text());now=dt.datetime.now(dt.timezone.utc)
    cal,bars=inputs(now);advance(state,cal,bars,now)
    dest.write_text(json.dumps(state,sort_keys=True,separators=(',',':'))+'\n')
    print("Shadow indices only:",state["start"],"last completed close:",state["asof"],"marks:",len(state['history']))
if __name__=='__main__':main()

