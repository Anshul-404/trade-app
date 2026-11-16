"""Browser integration smoke tests with mocked Lightweight Charts and replay API.
Run: python tests/test_drawings_browser.py (requires chromium + playwright)."""
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
html=(ROOT/'replay_static'/'index.html').read_text()
html=html.replace('<link rel="stylesheet" href="/replay.css">','')
html=html.replace('<script src="/chart-library.js"></script><script defer src="/drawing_tools.js"></script><script defer src="/replay.js"></script>','')
html=html.replace('<head>', '<head><base href="http://127.0.0.1:19183/">')
stub="""
window.LightweightCharts={CandlestickSeries:{}, CrosshairMode:{Normal:0},
  createSeriesMarkers:()=>({setMarkers(){}}),
  createChart:(host)=>{
    const d=document.createElement('div');d.className='stub-plot';d.style.height='100%';host.append(d);
    let data=[], callbacks=[];
    const scale={
      fitContent(){}, scrollToRealTime(){}, width(){return host.clientWidth-65;},
      coordinateToLogical(x){return x/10;}, logicalToCoordinate(i){return i*10;},
      timeToCoordinate(t){const i=data.findIndex(b=>b.time===t);return i>=0?i*10:null;},
      subscribeVisibleLogicalRangeChange(cb){callbacks.push(cb)},
      unsubscribeVisibleLogicalRangeChange(cb){callbacks=callbacks.filter(f=>f!==cb)},
    };
    const series={setData(b){data=[...b]},update(b){data.push(b)},createPriceLine(p){return p},removePriceLine(){},
      priceToCoordinate(price){return 300-(price-21000)*2},coordinateToPrice(y){return 21000+(300-y)/2}};
    window._lastChart={host,series,scale,forceRange:()=>callbacks.forEach(cb=>cb({from:0,to:20})),getData:()=>data};
    return {addSeries(){return series},timeScale(){return scale},resize(){},remove(){host.replaceChildren()}};
  }
};
window.testBars=Array.from({length:240},(_,i)=>({time:1704167100+i*60,open:21000+i,high:21003+i,low:20996+i,close:21001+i,volume:0}));
window.fetch=async(u)=>{const v=new URL(u),p=v.searchParams,b=window.testBars;let result;
if(v.pathname==='/api/catalog')result={symbol:'NIFTY50_13',timeframes:['1m','2m','5m','15m','60m','1d','1w'].map(tf=>({timeframe:tf,count:b.length,first:b[0].time,last:b.at(-1).time}))};
else if(v.pathname==='/api/start') {let index=p.has('index')?Number(p.get('index')):Math.max(0,b.findIndex(x=>x.time>=Math.floor(new Date(p.get('date')+'+05:30').getTime()/1000)));result={cursor:index,total:b.length,history:b.slice(Math.max(0,index-180),index),first:b[0].time,last:b.at(-1).time,quality:{}};}
else if(v.pathname==='/api/step'){const i=Number(p.get('cursor')),n=Number(p.get('count'));result={bars:b.slice(i,i+n),cursor:Math.min(i+n,b.length),total:b.length,done:i+n>=b.length};}
else return {ok:false,status:404,json:async()=>({error:'not found'})};
return {ok:true,status:200,json:async()=>result};};
"""
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1000},accept_downloads=True)
    page.set_content(html)
    page.add_style_tag(content=(ROOT/'replay_static'/'replay.css').read_text())
    errs=[];page.on('pageerror',lambda e:errs.append(str(e)))
    page.add_script_tag(content="window.__drawingStore={};Object.defineProperty(window, 'localStorage', {configurable:true,value:{getItem(k){return window.__drawingStore[k]||null;},setItem(k,v){window.__drawingStore[k]=String(v);}}});")
    page.add_script_tag(content=stub)
    page.add_script_tag(content=(ROOT/'replay_static'/'drawing_tools.js').read_text())
    page.add_script_tag(content=(ROOT/'replay_static'/'replay.js').read_text().replace('window.location.href','"http://127.0.0.1:19183/"'))
    page.wait_for_function("document.querySelector('#progress').textContent.includes('/ 240 bars')",timeout=6000)
    page.locator('#next-ten').click()
    page.wait_for_function("document.querySelector('#progress').textContent.includes('10 / 240')")
    chart=page.locator('#chart')
    bbox=chart.bounding_box(); assert bbox, 'chart not rendered'
    def pt(x,y): return (bbox['x']+x,bbox['y']+y)
    def click(x,y): page.mouse.click(*pt(x,y))
    page.locator('[data-draw-tool="ray"]').click()
    click(50,200)
    assert 'Horizontal ray added' in page.locator('#drawing-status').inner_text()
    page.wait_for_function("document.querySelectorAll('.drawing-overlay line').length>=1")
    page.locator('[data-draw-tool="trend"]').click()
    click(30,220);click(70,180)
    assert 'Trend line added' in page.locator('#drawing-status').inner_text()
    page.locator('[data-draw-tool="fib"]').click()
    click(30,250);click(70,180)
    assert 'Fibonacci added' in page.locator('#drawing-status').inner_text()
    page.wait_for_function("document.querySelectorAll('.drawing-overlay line').length>=9")
    page.locator('[data-draw-tool="long"]').click()
    click(20,190);click(20,230);click(80,145)
    assert 'Long position added' in page.locator('#drawing-status').inner_text()
    assert page.locator('#stop-loss').input_value()=='21035.00'
    assert page.locator('#take-profit').input_value()=='21077.50'
    page.locator('[data-draw-tool="short"]').click()
    click(20,190);click(20,140);click(80,245)
    assert 'Short position added' in page.locator('#drawing-status').inner_text()
    assert page.locator('#stop-loss').input_value()=='21080.00'
    assert page.locator('#take-profit').input_value()=='21027.50'
    assert page.evaluate("JSON.parse(localStorage.getItem('trading_bot_replay_drawings_v1:NIFTY50_13:1m')).length")==5
    assert not page.locator('#position-card').is_visible(), 'drawing a position must not execute a trade'
    # Select and drag the horizontal ray vertically without changing timestamps.
    page.locator('[data-draw-tool="select"]').click()
    page.mouse.move(*pt(170,200));page.mouse.down()
    page.mouse.move(*pt(170,210),steps=4);page.mouse.up()
    ray_price=page.evaluate("JSON.parse(localStorage.getItem('trading_bot_replay_drawings_v1:NIFTY50_13:1m'))[0].points[0].price")
    assert ray_price==21045, f'editing ray failed: {ray_price}'
    # Remove selected (ray), then undo last (short).
    page.locator('#drawing-delete').click()
    assert page.evaluate("JSON.parse(localStorage.getItem('trading_bot_replay_drawings_v1:NIFTY50_13:1m')).length")==4
    page.locator('#drawing-undo').click()
    assert page.evaluate("JSON.parse(localStorage.getItem('trading_bot_replay_drawings_v1:NIFTY50_13:1m')).length")==3
    # Rewind before drawing creation: drawings persist in storage, do not reveal future annotations.
    page.locator('#previous').click()
    page.wait_for_function("document.querySelector('#progress').textContent.includes('9 / 240')")
    assert page.locator('.drawing-overlay line').count()==0, 'future annotations must be hidden'
    page.locator('#next').click()
    page.wait_for_function("document.querySelector('#progress').textContent.includes('10 / 240')")
    page.wait_for_function("document.querySelector('.drawing-overlay').querySelectorAll('line').length>=9")
    # When a different timeframe is selected, drawings must not bleed between TFs.
    page.locator('#timeframe').select_option('5m')
    page.wait_for_function("document.querySelector('#chart-title').textContent.includes('5m')")
    assert page.locator('.drawing-overlay line').count()==0
    assert not errs,errs
    print('PASS: all five tools, ticket prefill, draw persistence, delete/undo, no-lookahead rewind, timeframe isolation; JS errors:',errs)
    browser.close()
