/* Trading Bot drawing tools for Lightweight Charts 5.2.x.
 * Educational annotations only: never places or executes trades.
 * Coordinates are anchored to revealed candle timestamps + index prices.
 * Per-symbol/timeframe drawings are stored locally; future-created items stay hidden on rewind.
 */
(() => {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const COLORS = {ray:'#f7cf78',trend:'#7da7ff',fib:'#ba91ff',long:'#42d9ad',short:'#ff788a'};
  const FIB = [0,0.236,0.382,0.5,0.618,0.786,1];
  const storagePrefix = 'trading_bot_replay_drawings_v1:';
  const $ = id => document.getElementById(id);
  const priceStr = n => Number(n).toLocaleString('en-IN',{maximumFractionDigits:2,minimumFractionDigits:2});
  function svgNode(tag, attrs={}, content='') {
    const el=document.createElementNS(NS,tag);
    for(const [key,v] of Object.entries(attrs)) if(v!==undefined && v!==null) el.setAttribute(key,String(v));
    if(content) el.textContent=content;
    return el;
  }
  function distPointToSeg(px,py,x1,y1,x2,y2) {
    const d=(x2-x1)**2+(y2-y1)**2;
    const t=d?Math.max(0,Math.min(1,((px-x1)*(x2-x1)+(py-y1)*(y2-y1))/d)):0;
    return Math.hypot(px-(x1+t*(x2-x1)),py-(y1+t*(y2-y1)));
  }
  function safeDrawing(o) {
    return o&&typeof o.id==='string'&&['ray','trend','fib','long','short'].includes(o.type)&&
      Number.isFinite(o.createdAt)&&Array.isArray(o.points)&&o.points.length>=1&&o.points.length<=3&&
      o.points.every(p=>Number.isFinite(p.time)&&Number.isFinite(p.price)&&p.price>0);
  }
  class DrawingManager {
    constructor({host,chart,series,bars,symbol,timeframe,onPosition}) {
      this.host=host; this.chart=chart; this.series=series; this.bars=Array.from(bars||[]);
      this.symbol=symbol; this.tf=timeframe; this.onPosition=onPosition;
      this.key=storagePrefix+symbol+':'+timeframe;
      this.drawings=[];this.mode='pan';this.points=[];this.preview=null;this.selected=null;this.drag=null;
      this.raf=null;this.endTip=null;
      try { const parsed=JSON.parse(localStorage.getItem(this.key)||'[]');
        if(Array.isArray(parsed))this.drawings=parsed.filter(safeDrawing).slice(0,500);
      } catch(_) { this.message('Saved drawings could not be loaded. New drawings will work.'); }
      this.svg=svgNode('svg',{'class':'drawing-overlay','aria-label':'Drawing overlay'});
      this.host.appendChild(this.svg);
      this.listeners=[];
      this.listen(this.svg,'pointerdown',e=>this.onDown(e));
      this.listen(this.svg,'pointermove',e=>this.onMove(e));
      this.listen(this.svg,'pointerup',e=>this.onUp(e));
      this.listen(this.svg,'pointercancel',()=>this.cancelDrag());
      this.listen(this.svg,'contextmenu',e=>{if(this.mode!=='pan')e.preventDefault();});
      this.listen(window,'keydown',e=>this.onKey(e));
      this.listen(this.host,'wheel',()=>this.requestRender());
      this.listen(this.host,'pointerup',()=>this.requestRender());
      if(typeof ResizeObserver!=='undefined') {
        this.resize=new ResizeObserver(()=>this.requestRender());this.resize.observe(this.host);
      }
      this.rangeHandler=()=>this.requestRender();
      try {this.chart.timeScale().subscribeVisibleLogicalRangeChange(this.rangeHandler);}catch(_){}
      this.setMode('pan'); this.requestRender();
    }
    listen(target,event,fn) {target.addEventListener(event,fn);this.listeners.push([target,event,fn]);}
    dispose() {
      if(this.resize)this.resize.disconnect();
      try {this.chart.timeScale().unsubscribeVisibleLogicalRangeChange(this.rangeHandler);}catch(_){}
      this.listeners.forEach(([el,event,fn])=>el.removeEventListener(event,fn));
      if(this.raf!==null)cancelAnimationFrame(this.raf);
      this.svg.remove();
    }
    message(text) {if($('drawing-status'))$('drawing-status').textContent=text;}
    requestRender() {if(this.raf!==null)return;this.raf=requestAnimationFrame(()=>{this.raf=null;this.render();});}
    setMode(mode) {
      if(!['pan','select','ray','trend','fib','long','short'].includes(mode))return;
      this.mode=mode;this.points=[];this.preview=null;this.drag=null;
      this.svg.style.pointerEvents=mode==='pan'?'none':'auto';
      this.svg.style.cursor=mode==='pan'?'auto':mode==='select'?'default':'crosshair';
      document.querySelectorAll('[data-draw-tool]').forEach(btn=>{
        const active=btn.dataset.drawTool===mode;
        btn.classList.toggle('active',active);btn.setAttribute('aria-pressed',String(active));
      });
      const steps={pan:'Pan / zoom the chart normally. Choose a drawing tool to begin.',select:'Select a drawing; drag its endpoints to edit. Delete removes selection.',ray:'Horizontal ray: click a visible candle at your price.',trend:'Trend line: click two chart points.',fib:'Fibonacci: click the swing start and swing end.',long:'Long position: click ENTRY, then STOP below entry, then TARGET above entry.',short:'Short position: click ENTRY, then STOP above entry, then TARGET below entry.'};
      this.message(steps[mode]);this.requestRender();
    }
    appendBar(bar) {this.bars.push(bar);this.requestRender();}
    snapshot() {return this.drawings.map(d=>({...d,points:d.points.map(p=>({...p}))}));}
    save() {try{localStorage.setItem(this.key,JSON.stringify(this.drawings.slice(-500)));}catch(_){this.message('Unable to save drawings: browser storage unavailable.');}}
    deleteSelected() {if(!this.selected)return;this.drawings=this.drawings.filter(d=>d.id!==this.selected);this.selected=null;this.save();this.requestRender();this.message('Selected drawing deleted.');}
    undo() {if(this.points.length){this.points.pop();this.preview=null;this.requestRender();return;}
      if(this.drawings.length){this.drawings.pop();this.selected=null;this.save();this.requestRender();this.message('Last drawing removed.');}}
    clear() {if(this.drawings.length&&window.confirm('Clear all drawings for this symbol and timeframe?')){this.drawings=[];this.selected=null;this.save();this.requestRender();this.message('Drawings cleared for this timeframe.');}}
    onKey(e) {if(['INPUT','TEXTAREA','SELECT'].includes(e.target?.tagName))return;
      if(e.key==='Escape'&&this.mode!=='pan'){this.setMode('pan');}
      else if((e.key==='Delete'||e.key==='Backspace')&&this.mode==='select'){e.preventDefault();this.deleteSelected();}}
    pixel(e){const r=this.host.getBoundingClientRect();return {x:e.clientX-r.left,y:e.clientY-r.top};}
    plotWidth(){try{return this.chart.timeScale().width()||this.host.clientWidth-65;}catch(_){return this.host.clientWidth-65;}}
    plotHeight(){return Math.max(0,this.host.clientHeight-26);}
    toWorld(pixel) {
      if(!this.bars.length||pixel.x<0||pixel.x>this.plotWidth()||pixel.y<0||pixel.y>this.plotHeight())return null;
      let logical,price;
      try {logical=this.chart.timeScale().coordinateToLogical(pixel.x);price=this.series.coordinateToPrice(pixel.y);}catch(_){return null;}
      if(!Number.isFinite(logical)||!Number.isFinite(price)||price<=0)return null;
      const index=Math.min(this.bars.length-1,Math.max(0,Math.round(logical)));
      return {time:this.bars[index].time,price:Math.round(price*100)/100};
    }
    toPixel(point) {
      let x,y;
      try{x=this.chart.timeScale().timeToCoordinate(point.time);y=this.series.priceToCoordinate(point.price);}catch(_){return null;}
      if(!Number.isFinite(x)||!Number.isFinite(y))return null;
      return {x,y};
    }
    visible(d) {return this.bars.length&&d.createdAt<=this.bars[this.bars.length-1].time;}
    onDown(e) {
      if(e.button!==0||this.mode==='pan')return;
      e.preventDefault();const pixel=this.pixel(e);
      if(this.mode==='select'){
        const hit=this.hitTest(pixel);
        this.selected=hit?.drawing.id||null;
        if(hit){this.drag={id:hit.drawing.id,anchor:hit.anchor,at:pixel,original:hit.drawing.points.map(p=>({...p}))};
          this.svg.setPointerCapture(e.pointerId);}
        this.requestRender();return;
      }
      const p=this.toWorld(pixel);
      if(!p){this.message('Click inside the revealed candle area, not the price or time axes.');return;}
      this.points.push(p);this.preview=null;
      const wanted=this.mode==='ray'?1:this.mode==='long'||this.mode==='short'?3:2;
      if(this.points.length===wanted){
        const drawing={id:`d_${Date.now()}_${Math.random().toString(36).slice(2,8)}`,type:this.mode,
          createdAt:this.bars[this.bars.length-1].time,points:this.points.map(p=>({...p}))};
        if(!this.valid(drawing)) {this.points.pop();this.message(this.mode==='long'?'For LONG: click SL below entry and TP above entry.':'For SHORT: click SL above entry and TP below entry.');this.requestRender();return;}
        this.drawings.push(drawing);this.selected=drawing.id;this.save();
        this.points=[];
        if((drawing.type==='long'||drawing.type==='short')&&typeof this.onPosition==='function')this.onPosition(drawing);
        this.message(`${this.name(drawing.type)} added. Choose Select to edit its anchors, or keep drawing.`);
      } else if(this.mode==='long'||this.mode==='short')this.message(this.points.length===1?'Now click STOP LOSS.':'Now click TAKE PROFIT.');
      else this.message('Now click the second point.');
      this.requestRender();
    }
    valid(d) {if(d.type!=='long'&&d.type!=='short')return true;
      const [entry,stop,target]=d.points.map(p=>p.price);
      return d.type==='long'?stop<entry&&target>entry:stop>entry&&target<entry;}
    name(type){return {ray:'Horizontal ray',trend:'Trend line',fib:'Fibonacci',long:'Long position',short:'Short position'}[type]||type;}
    onMove(e){
      if(this.mode==='pan')return;
      const pixel=this.pixel(e);
      if(this.drag){
        const d=this.drawings.find(x=>x.id===this.drag.id),p=this.toWorld(pixel);
        if(!d||!p)return;
        const next=this.drag.original.map(a=>({...a}));
        if(this.drag.anchor>=0)next[this.drag.anchor]=p;
        else {
          const initial=this.toWorld(this.drag.at);if(!initial)return;
          const originalIndex=this.bars.findIndex(b=>b.time===initial.time);
          const nextIndex=this.bars.findIndex(b=>b.time===p.time);
          if(originalIndex<0||nextIndex<0)return;
          const offset=nextIndex-originalIndex;
          const deltaPrice=p.price-initial.price;
          next.forEach((a,i)=>{const idx=this.bars.findIndex(b=>b.time===a.time);
            a.time=this.bars[Math.max(0,Math.min(this.bars.length-1,idx+offset))].time;
            a.price=Math.max(0.01,Math.round((a.price+deltaPrice)*100)/100);
          });
        }
        if(this.valid({...d,points:next})) {d.points=next;this.requestRender();}
      } else if(this.mode!=='select'&&this.points.length){this.preview=this.toWorld(pixel);this.requestRender();}
    }
    onUp(e){if(this.drag){this.drag=null;try{this.svg.releasePointerCapture(e.pointerId);}catch(_){}this.save();this.requestRender();}}
    cancelDrag(){this.drag=null;this.requestRender();}
    hitTest(pixel){
      for(const d of [...this.drawings].reverse()){
        if(!this.visible(d))continue;
        const points=d.points.map(p=>this.toPixel(p));if(points.some(p=>!p))continue;
        for(let i=0;i<points.length;i++)if(Math.hypot(pixel.x-points[i].x,pixel.y-points[i].y)<12)return {drawing:d,anchor:i};
        if(d.type==='ray'){if(pixel.x>=points[0].x&&Math.abs(pixel.y-points[0].y)<9)return {drawing:d,anchor:-1};}
        else if(d.type==='trend'){if(distPointToSeg(pixel.x,pixel.y,points[0].x,points[0].y,points[1].x,points[1].y)<9)return {drawing:d,anchor:-1};}
        else if(d.type==='fib'){
          const x1=Math.min(points[0].x,points[1].x),x2=Math.max(points[0].x,points[1].x);
          const lo=Math.min(points[0].y,points[1].y),hi=Math.max(points[0].y,points[1].y);
          if(pixel.x>=x1&&pixel.x<=Math.max(x2,x1+90)&&pixel.y>=lo-10&&pixel.y<=hi+10)return {drawing:d,anchor:-1};
        } else {
          const x=points[0].x,width=Math.max(100,Math.abs(points[2].x-x));
          const lo=Math.min(...points.map(p=>p.y)),hi=Math.max(...points.map(p=>p.y));
          if(pixel.x>=x-5&&pixel.x<=x+width+5&&pixel.y>=lo-5&&pixel.y<=hi+5)return {drawing:d,anchor:-1};
        }
      }
      return null;
    }
    line(g,x1,y1,x2,y2,color,extra={}){
      g.appendChild(svgNode('line',{x1,y1,x2,y2,stroke:color,'stroke-width':2,'stroke-linecap':'round',...extra}));
    }
    rect(g,x,y,w,h,fill,opacity=0.2){g.appendChild(svgNode('rect',{x,y,width:Math.max(0,w),height:Math.max(0,h),fill,'fill-opacity':opacity}));}
    label(g,x,y,text,color){
      const width=Math.max(40,text.length*6.4+12);
      this.rect(g,Math.max(0,x),y-11,width,17,'#0b1422',0.94);
      g.appendChild(svgNode('text',{x:Math.max(0,x)+5,y:y+1,fill:color,'font-size':10,'font-weight':700,'font-family':'system-ui'},text));
    }
    drawOne(g,d,ghost=false) {
      const pts=d.points.map(p=>this.toPixel(p));if(pts.some(p=>!p))return;
      const [a,b,c]=pts,selected=!ghost&&d.id===this.selected;
      const color=COLORS[d.type];
      if(d.type==='ray'){
        this.line(g,a.x,a.y,this.plotWidth(),a.y,color,{'stroke-dasharray':'7 4'});
        this.label(g,Math.min(this.plotWidth()-83,a.x+9),a.y-7,`RAY ${priceStr(d.points[0].price)}`,color);
      } else if(d.type==='trend'&&b){this.line(g,a.x,a.y,b.x,b.y,color);
      } else if(d.type==='fib'&&b){
        const left=Math.min(a.x,b.x),right=Math.max(a.x+80,b.x),p0=d.points[0].price,p1=d.points[1].price;
        this.rect(g,left,Math.min(a.y,b.y),right-left,Math.abs(a.y-b.y),color,0.065);
        FIB.forEach((level,i)=>{
          const v=p1+(p0-p1)*level,y=this.series.priceToCoordinate(v);
          if(!Number.isFinite(y))return;
          this.line(g,left,y,right,y,color,{'stroke-width':i===0||i===6?1.5:1,'stroke-opacity':i===0||i===6?0.94:0.6});
          this.label(g,Math.min(right-55,left+4),y-3,`${level.toFixed(3)} · ${priceStr(v)}`,color);
        });
      } else if((d.type==='long'||d.type==='short')&&b&&c){
        const x=a.x,width=Math.max(100,Math.abs(c.x-x),Math.abs(b.x-x));
        const riskTop=Math.min(a.y,b.y),riskBottom=Math.max(a.y,b.y);
        const rewardTop=Math.min(a.y,c.y),rewardBottom=Math.max(a.y,c.y);
        this.rect(g,x,riskTop,width,riskBottom-riskTop,'#fa596d',0.19);
        this.rect(g,x,rewardTop,width,rewardBottom-rewardTop,'#28cd98',0.19);
        this.line(g,x,a.y,x+width,a.y,'#dce5fd',{'stroke-dasharray':'4 3'});
        this.line(g,x,b.y,x+width,b.y,'#fa7184');this.line(g,x,c.y,x+width,c.y,'#3bdfa8');
        const risk=Math.abs(d.points[0].price-d.points[1].price),rr=Math.abs(d.points[2].price-d.points[0].price)/risk;
        this.label(g,x+5,a.y-7,`${d.type.toUpperCase()} ENTRY ${priceStr(d.points[0].price)}`,'#dce5fd');
        this.label(g,x+5,b.y-7,`SL ${priceStr(d.points[1].price)}`,'#ff95a3');
        this.label(g,x+5,c.y-7,`TP ${priceStr(d.points[2].price)} · 1:${rr.toFixed(2)}`,'#7af1ca');
      }
      if(selected||ghost){
        pts.forEach(p=>g.appendChild(svgNode('circle',{cx:p.x,cy:p.y,r:ghost?3.8:5.2,fill:'#0c1420',stroke:color,'stroke-width':2})));
      }
    }
    render() {
      const width=this.host.clientWidth,height=this.host.clientHeight;
      if(!width||!height)return;
      this.svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
      this.svg.setAttribute('width',String(width));this.svg.setAttribute('height',String(height));
      this.svg.replaceChildren();
      const defs=svgNode('defs');const clip=svgNode('clipPath',{id:'tb-drawing-clip'});
      clip.appendChild(svgNode('rect',{x:0,y:0,width:this.plotWidth(),height:this.plotHeight()}));defs.appendChild(clip);this.svg.appendChild(defs);
      // An invisible painted rectangle ensures pointer events reach the overlay
      // across the entire plot when drawing/select mode is active.
      this.svg.appendChild(svgNode('rect',{x:0,y:0,width:this.plotWidth(),height:this.plotHeight(),fill:'transparent','pointer-events':'all'}));
      const layer=svgNode('g',{'clip-path':'url(#tb-drawing-clip)','pointer-events':'none'});
      this.svg.appendChild(layer);
      for(const d of this.drawings)if(this.visible(d))this.drawOne(layer,d);
      if(this.points.length){const points=[...this.points];if(this.preview)points.push(this.preview);
        const d={id:'preview',type:this.mode,points};
        // Position drawing needs all 3 anchor prices; preview intermediate steps as lines.
        if(['long','short'].includes(this.mode)&&points.length<3){
          const a=this.toPixel(points[0]),b=points[1]&&this.toPixel(points[1]);
          if(a&&b)this.line(layer,a.x,a.y,b.x,b.y,COLORS[this.mode],{'stroke-dasharray':'4 4'});
        }else this.drawOne(layer,d,true);
      }
    }
  }
  window.TradingBotDrawings={create:opts=>new DrawingManager(opts)};
})();
