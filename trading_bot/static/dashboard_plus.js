(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  if (!$('tb-movers-winners')) return;
  const fmt = v => Number(v).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});
  const money = v => Number.isFinite(Number(v)) && v != null ? `₹${fmt(v)}` : '—';
  const api = async (url, opts={}) => {
    const r=await fetch(url,{cache:'no-store',...opts});
    const data=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:`HTTP ${r.status}`);
    return data;
  };
  const jsonPost=(url,data,headers={})=>api(url,{method:'POST',headers:{'Content-Type':'application/json',...headers},body:JSON.stringify(data)});
  const note=(id,msg,err=false)=>{const el=$(id);if(el){el.textContent=msg;el.classList.toggle('tb-error',err);}};
  let gettingMovers=false;
  const drawMovers=(target,items,type)=>{
    target.replaceChildren();
    if(!items.length){const li=document.createElement('li');li.className='tb-movers-empty';li.textContent=type==='up'?'No positive movers in the latest snapshot':'No negative movers in the latest snapshot';target.appendChild(li);return;}
    const maximum=Math.max(...items.map(i=>Math.abs(i.change_pct)),0.05);
    for(const item of items){
      const li=document.createElement('li');li.className='tb-mover';
      const row=document.createElement('div');row.className='tb-mover-row';
      const symbol=document.createElement('strong');symbol.textContent=item.symbol;
      const value=document.createElement('span');value.textContent=`${item.change_pct>0?'+':''}${fmt(item.change_pct)}%`;
      value.className=type==='up'?'tb-positive':'tb-negative';
      row.append(symbol,value);
      const bar=document.createElement('div');bar.className='tb-mover-track';
      const fill=document.createElement('div');fill.className='tb-mover-fill '+type;
      fill.style.width=Math.max(2,100*Math.abs(item.change_pct)/maximum).toFixed(1)+'%';
      bar.appendChild(fill);
      const detail=document.createElement('div');detail.className='tb-mover-detail';
      detail.textContent=`₹${fmt(item.price)} · ${item.change>=0?'+':''}₹${fmt(item.change)}`;
      li.append(row,bar,detail);target.appendChild(li);
    }
  };
  async function refreshMovers(){
    if(gettingMovers||document.hidden)return;
    gettingMovers=true;
    try {
      const data=await api('/api/movers');
      drawMovers($('tb-movers-winners'),data.winners,'up');
      drawMovers($('tb-movers-losers'),data.losers,'down');
      $('tb-gainers-count').textContent=String(data.winners.length);
      $('tb-losers-count').textContent=String(data.losers.length);
      const stamp=new Date(data.snapshot_utc).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',second:'2-digit',timeZone:'Asia/Kolkata'});
      note('tb-movers-note',`${data.valid_quotes}/${data.constituents} NIFTY 50 stocks priced · Dhan snapshot ${stamp} IST · ${Math.round(data.age_seconds)}s old${data.stale?' · STALE: '+(data.warning||'quote refresh failed'):''}`,!!data.stale);
    }catch(e){note('tb-movers-note','Market movers unavailable: '+e.message,true);}
    finally{gettingMovers=false;}
  }

  let riskPositions=[];
  let selectedRiskKey=null;
  let closing=false;
  let monitoring=new Map();
  const key=p=>p.expiry+'|'+p.security_id;
  const riskList=$('tb-paper-risk-list');
  const picked=()=>riskPositions.find(p=>key(p)===selectedRiskKey);
  const sltpValue=(input)=>{
    if(input.value.trim()==='')return null;
    const n=Number(input.value);
    if(!Number.isFinite(n)||n<=0)throw new Error('Enter a valid positive premium or leave blank');
    return n;
  };
  async function saveRisk(position,slInput,tpInput,btn){
    let sl,tp;
    try{sl=sltpValue(slInput);tp=sltpValue(tpInput);}catch(e){note('tb-risk-note',e.message,true);return;}
    btn.disabled=true;
    try {
      await jsonPost('/api/paper/risk',{expiry:position.expiry,security_id:position.security_id,sl,tp});
      note('tb-risk-note','Visual premium levels saved. These do NOT trigger automatic exits.');
      await refreshRisk(true);
    }catch(e){note('tb-risk-note',e.message,true);}
    finally{btn.disabled=false;}
  }
  function renderRisk(positions){
    riskList.replaceChildren();
    if(!positions.length){riskList.textContent='No paper option positions. Open a simulated option trade first.';drawPremium();return;}
    for(const p of positions){
      const card=document.createElement('article');card.className='tb-risk-position';
      if(key(p)===selectedRiskKey)card.classList.add('selected');
      const heading=document.createElement('button');heading.type='button';heading.className='tb-risk-select';
      heading.textContent=`${p.symbol} · ${p.qty} qty`;
      heading.addEventListener('click',()=>{selectedRiskKey=key(p);renderRisk(riskPositions);drawPremium();});
      const sub=document.createElement('div');sub.className='tb-risk-small';
      sub.textContent=`Avg ${money(p.avg_price)} · Bid ${money(p.mark_bid)} · Open P&L ${money(p.unrealized_pnl)}`;
      const fields=document.createElement('div');fields.className='tb-risk-fields';
      const sl=document.createElement('input');sl.type='number';sl.step='0.05';sl.min='0.01';sl.placeholder='SL ₹';sl.value=p.sl??'';sl.setAttribute('aria-label','Option premium stop-loss');
      const tp=document.createElement('input');tp.type='number';tp.step='0.05';tp.min='0.01';tp.placeholder='TP ₹';tp.value=p.tp??'';tp.setAttribute('aria-label','Option premium take-profit');
      const save=document.createElement('button');save.type='button';save.className='tb-compact-btn';save.textContent='Save';
      save.addEventListener('click',()=>saveRisk(p,sl,tp,save));
      fields.append(sl,tp,save);card.append(heading,sub,fields);riskList.appendChild(card);
    }
  }
  async function refreshRisk(force=false){
    if(document.hidden)return;
    if(!force && riskList.contains(document.activeElement))return;
    try {
      const data=await api('/api/paper/risk');
      riskPositions=data.positions;
      if(!riskPositions.some(p=>key(p)===selectedRiskKey))selectedRiskKey=riskPositions.length?key(riskPositions[0]):null;
      renderRisk(riskPositions);
      drawPremium();
      $('tb-close-all').disabled=closing||riskPositions.length===0;
      $('tb-close-count').textContent=String(riskPositions.length);
    }catch(e){note('tb-risk-note',e.message,true);}
  }
  async function closeAllPaper(){
    if(closing||!riskPositions.length)return;
    if(!window.confirm(`Close ALL ${riskPositions.length} PAPER positions at latest available bids?\n\nThis affects only your virtual SQLite ledger.\nNo live Dhan orders will be placed.\nIf any quote is stale, nothing will close.`))return;
    closing=true;$('tb-close-all').disabled=true;
    try {
      const result=await jsonPost('/api/paper/close-all',{confirm:'CLOSE_ALL_PAPER'},{'X-Paper-Action':'paper-close-all'});
      note('tb-risk-note',`Closed ${result.closed} paper positions · Realized P&L ${money(result.realized_pnl)}. No real trades.`);
      await refreshRisk(true);
      window.dispatchEvent(new Event('tb-paper-refresh'));
    }catch(e){note('tb-risk-note','Close all rejected; no positions changed: '+e.message,true);}
    finally{closing=false;$('tb-close-all').disabled=!riskPositions.length;}
  }

  // This is a separate option PREMIUM graph. Futures prices must never be
  // plotted against premium SL/TP values on the same axis.
  function drawPremium(){
    const canvas=$('tb-premium-canvas');if(!canvas)return;
    const pos=picked();
    const width=Math.max(280,Math.floor(canvas.getBoundingClientRect().width||360));
    const height=170,ratio=Math.min(2,window.devicePixelRatio||1);
    canvas.width=width*ratio;canvas.height=height*ratio;
    const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);
    ctx.clearRect(0,0,width,height);
    ctx.fillStyle='#0d1726';ctx.fillRect(0,0,width,height);
    const left=46,right=12,top=16,bottom=22,graphW=width-left-right,graphH=height-top-bottom;
    if(!pos){ctx.fillStyle='#94a8c0';ctx.font='12px sans-serif';ctx.fillText('Select an open paper option position',14,80);return;}
    const prices=(monitoring.get(key(pos))||[]).map(p=>p.value);
    const levels=[pos.sl,pos.tp,pos.avg_price,pos.mark_bid].filter(v=>v!=null&&Number.isFinite(Number(v))).map(Number);
    const all=[...prices,...levels];
    if(!all.length){ctx.fillStyle='#94a8c0';ctx.font='12px sans-serif';ctx.fillText('Awaiting a fresh option bid…',14,80);return;}
    let lo=Math.min(...all),hi=Math.max(...all),pad=Math.max(1,(hi-lo)*.15);lo-=pad;hi+=pad;
    const y=v=>top+(hi-v)/(hi-lo)*graphH;
    ctx.strokeStyle='#233750';ctx.lineWidth=1;
    for(let i=0;i<=4;i++){const yy=top+i*graphH/4;ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(width-right,yy);ctx.stroke();ctx.fillStyle='#8095ad';ctx.font='10px sans-serif';ctx.fillText(fmt(hi-(hi-lo)*i/4),3,yy+3);}
    const line=(v,color,label)=>{if(v==null)return;ctx.strokeStyle=color;ctx.setLineDash([5,5]);ctx.lineWidth=1.5;ctx.beginPath();ctx.moveTo(left,y(v));ctx.lineTo(width-right,y(v));ctx.stroke();ctx.setLineDash([]);ctx.fillStyle=color;ctx.font='bold 11px sans-serif';ctx.fillText(label+' '+fmt(v),left+4,Math.max(11,y(v)-4));};
    line(pos.sl,'#fa6a75','SL');line(pos.tp,'#27cf9e','TP');line(pos.avg_price,'#92a4c0','AVG');
    if(prices.length){
      ctx.strokeStyle='#67b7ed';ctx.lineWidth=2;ctx.beginPath();
      prices.forEach((p,i)=>{const xx=left+i*graphW/Math.max(1,prices.length-1);if(i===0)ctx.moveTo(xx,y(p));else ctx.lineTo(xx,y(p));});ctx.stroke();
      const xx=left+graphW,yy=y(prices.at(-1));ctx.fillStyle='#67b7ed';ctx.beginPath();ctx.arc(xx,yy,3.2,0,2*Math.PI);ctx.fill();
    }
    ctx.fillStyle='#94a8c0';ctx.font='10px sans-serif';ctx.fillText(prices.length+' cached bid sample'+(prices.length===1?'':'s')+' · not historical candles',left,height-5);
  }
  document.addEventListener('tb-chain-refreshed',ev=>{
    const data=ev.detail;if(!data||data.stale)return;
    for(const row of data.rows||[]){for(const leg of [row.ce,row.pe]){
      if(!leg||!Number.isFinite(Number(leg.bid))||Number(leg.bid)<=0)continue;
      const k=leg.expiry+'|'+leg.security_id;
      const points=monitoring.get(k)||[];
      if(!points.length||points.at(-1).when!==data.fetched_at_utc){points.push({when:data.fetched_at_utc,value:Number(leg.bid)});if(points.length>80)points.shift();monitoring.set(k,points);}
    }}
    drawPremium();refreshRisk();
  });

  // Futures chart SL/TP price lines are manual VISUAL guides only.
  const futures=window.TradingBotChart;
  let chartLines=[];
  let chartSymbol='';
  const chartInput=(id)=>{
    const raw=$(id).value.trim();if(!raw)return null;
    const num=Number(raw);if(!Number.isFinite(num)||num<=0)throw new Error('Chart prices must be positive');return num;
  };
  function drawChartLevels(){
    if(!futures?.series?.createPriceLine)return;
    for(const item of chartLines){try{futures.series.removePriceLine(item);}catch{}}
    chartLines=[];
    const levels=[{value:chartInput('tb-fut-sl'),title:'VISUAL SL',color:'#fb7783'},
                  {value:chartInput('tb-fut-tp'),title:'VISUAL TP',color:'#26c7a2'}];
    for(const item of levels){if(item.value!=null){chartLines.push(futures.series.createPriceLine({price:item.value,color:item.color,lineWidth:2,lineStyle:2,axisLabelVisible:true,title:item.title}));}}
  }
  $('tb-chart-apply').addEventListener('click',()=>{
    try {
      const sl=chartInput('tb-fut-sl'),tp=chartInput('tb-fut-tp');
      if(sl!=null&&tp!=null&&sl>=tp)throw new Error('For a long reference, SL must be below TP');
      drawChartLevels();
      try{localStorage.setItem('tb-reference-'+chartSymbol,JSON.stringify({sl,tp}));}catch{}
      note('tb-chart-note','Visual futures lines updated. They do not place or close orders.');
    }catch(e){note('tb-chart-note',e.message,true);}
  });
  $('tb-chart-clear').addEventListener('click',()=>{$('tb-fut-sl').value='';$('tb-fut-tp').value='';drawChartLevels();try{localStorage.removeItem('tb-reference-'+chartSymbol);}catch{}note('tb-chart-note','Futures reference lines cleared.');});
  api('/api/status').then(data=>{
    chartSymbol=data.symbol||'futures';
    try{const state=JSON.parse(localStorage.getItem('tb-reference-'+chartSymbol)||'{}');$('tb-fut-sl').value=state.sl??'';$('tb-fut-tp').value=state.tp??'';drawChartLevels();}catch{}
  }).catch(()=>{});

  $('tb-close-all').addEventListener('click',closeAllPaper);
  $('tb-movers-refresh').addEventListener('click',refreshMovers);
  window.addEventListener('resize',drawPremium);
  window.addEventListener('tb-paper-refresh',()=>refreshRisk(true));
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){refreshRisk(true);refreshMovers();}});
  refreshMovers();refreshRisk(true);
  setInterval(refreshMovers,25000);
  setInterval(()=>refreshRisk(),12000);
})();
