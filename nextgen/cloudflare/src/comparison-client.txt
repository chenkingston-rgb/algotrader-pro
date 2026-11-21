/* Shared rendering for the normalized, hypothetical comparison indices. */
globalThis.startPaperComparison = function(root,read,privateView=false) {
  const models=['Trend3','Conditional QQQ','SPY','QQQ'];
  const make=(tag,txt,parent)=>{const e=document.createElement(tag);if(txt!=null)e.textContent=txt;parent.appendChild(e);return e};
  root.textContent='';
  const slug=make('p','FORWARD PAPER TEST  ·  USD',root);slug.style.cssText='color:#78cdbc;font-size:.8rem;letter-spacing:.12em;margin:0';
  make('h2','Strategy and benchmark comparison',root);
  const stamp=make('p','Waiting for the paper ledger…',root);stamp.setAttribute('aria-live','polite');
  const label=make('label','Dividend treatment  ',root);
  const select=make('select',null,label);select.style.cssText='padding:.5em;max-width:100%;font:inherit';
  for(const [v,t] of [['ordinary30','30% withholding · conservative'],['bil_exempt','30% ordinary · BIL exempt scenario'],['gross','Gross · before withholding']]){const o=make('option',t,select);o.value=v}
  const sc=make('div',null,root);sc.style.cssText='overflow-x:auto;margin:.8em 0';
  const table=make('table',null,sc);table.style.cssText='width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums';
  const tr=make('tr',null,make('thead',null,table));
  for(const s of ['Portfolio','Total return','Drawdown now','Worst drawdown','Tax per $100 start'])make('th',s,tr);
  const tbody=make('tbody',null,table);
  const plan=make('p','',root); const info=make('details',null,root);make('summary','How to read this comparison',info);
  make('p','Each index starts with $100 of simulated capital on the same US session. Returns include estimated distributions and 0.10% friction on traded notional. Drawdowns are measured from each index’s own daily closing high, including its starting value. They are not intraday or broker P&L.',info);
  make('p','Conditional QQQ always holds 15% QQQ. At month-end it adds 45% QQQ, 20% SPY and 20% gold when each fund closes at or above its own 200-session average. The rest is BIL. Trend3 has a permanent 20% QQQ stake and three equal trend sleeves. The SPY and QQQ benchmarks stay invested.',info);
  make('p',(privateView?'Investor context: Singaporean individual trading US-listed funds, assuming non-US tax status. ':'Singapore individual investor scenario (assuming non-US tax status). ')+'The 30% scenario is an estimate of US withholding on ordinary distributions, not a Singapore tax on portfolio gains. Some BIL distributions can qualify as interest-related dividends. The BIL-exempt case shows the other endpoint. Use broker tax records for the actual amount. No capital-gains tax is modeled.',info);
  make('p','Targets are recorded before the simulated opening fill. End-of-day marks use delayed consolidated daily bars; ex-date dividend income is estimated from price adjustments and reinvested at close if the fund is still held. No real or broker-paper orders are sent.',info);
  let state=null,seq=0;
  const pc=v=>(v>0?'+':'')+(v*100).toFixed(2)+'%';
  function render(){
    tbody.textContent='';plan.textContent='';
    if(!state||state.version!=='shadow-qqq-v1'||!state.start||!Array.isArray(state.history)){stamp.textContent='No verified paper baseline yet.';return}
    const marked=state.history.length>0,expired=Date.now()>Date.parse(state.next_close_due);
    stamp.textContent=(expired?'STALE · expected close missing. ':'')+(marked?'Marks through '+state.asof+' · '+state.history.length+' US sessions.':'Armed · awaiting the first completed close.')+' Common start: '+state.start+'.';
    for(const n of models){
      const b=state.books?.[n]?.[select.value];const row=make('tr',null,tbody);make('th',n,row).scope='row';
      if(!b||!['nav','dd','worst','tax'].every(k=>Number.isFinite(b[k])))throw Error('Invalid index data');
      for(const s of marked?[pc(b.nav/100-1),pc(b.dd),pc(b.worst),'$'+b.tax.toFixed(3)]:['—','—','—','—'])make('td',s,row);
    }
    const p=state.plans?.at(-1);
    if(p)plan.textContent='Latest locked plan: '+p.signal+' month-end → '+p.open+' simulated open'+(p.kind==='late-forward'?' (late; not backdated)':'')+'. '+['SPY','QQQ','GLD'].map(t=>t+' '+(p.on[t]?'on':'off')).join(' · ')+'.';
  }
  const clear=()=>{seq++;state=null;tbody.textContent='';plan.textContent='';stamp.textContent='Comparison not loaded.'};
  async function refresh(){const i=++seq;try{const v=await read();if(i!==seq)return;state=v;render()}catch(_){if(i===seq){clear();stamp.textContent='Comparison unavailable. Do not treat missing data as a flat return.'}}}
  select.onchange=()=>{try{render()}catch(_){clear()}};
  return {refresh,clear};
};
