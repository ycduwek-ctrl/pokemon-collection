/* Public reference images have their own DB, separate from personal collections. */
const HitimSets=(()=>{
  let dbPromise,language='English',sets=[],pack=null,features=[],job=null,viewToken=0,shown=60;
  let urls=[],activeKey='',message='';
  const el=id=>document.getElementById(id),esc=s=>escapeHtml(String(s??''));
  const key=(lang,id)=>lang+'|'+id;
  function db(){
    if(!dbPromise)dbPromise=new Promise((resolve,reject)=>{
      const req=indexedDB.open('hitim-public-set-references',1);
      req.onupgradeneeded=()=>{for(const name of ['packs','images','features']){const store=req.result.createObjectStore(name,{keyPath:'id'});if(name!=='packs')store.createIndex('packKey','packKey');}};
      req.onsuccess=()=>resolve(req.result);req.onerror=()=>{dbPromise=null;reject(req.error);};
    });return dbPromise;
  }
  async function read(store,id){const d=await db();return new Promise((resolve,reject)=>{const r=d.transaction(store).objectStore(store).get(id);r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});}
  async function all(store){const d=await db();return new Promise((resolve,reject)=>{const r=d.transaction(store).objectStore(store).getAll();r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});}
  async function write(records){const d=await db();return new Promise((resolve,reject)=>{const tx=d.transaction(Object.keys(records),'readwrite');for(const [store,items] of Object.entries(records))for(const item of items)tx.objectStore(store).put(item);tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);});}
  async function api(path){const r=await HitimAuth.fetch(path,{timeoutMs:20000});if(!r.ok)throw Error('הקטלוג לא זמין כרגע. נסה שוב.');return r.json();}
  function releaseUrls(){urls.forEach(URL.revokeObjectURL);urls=[];}
  function close(){el('setPreview')?.close();viewToken++;releaseUrls();el('setsOverlay').classList.remove('open');}
  async function open(){
    closeNavigationViews();closePanel();el('setsOverlay').classList.add('open');
    try{activeKey=localStorage.getItem('hitim-reference-set')||'';}catch(e){}
    await loadLanguage(language);
  }
  async function loadLanguage(lang){
    language=lang;pack=null;message='';const token=++viewToken;el('setsStatus').textContent='טוען סדרות...';el('setsContent').replaceChildren();el('setsLanguage').value=lang;
    try{
      features=await all('features');
      let cached=await read('packs','list|'+lang);
      if(!cached){const result=await api('/catalog/sets?language='+encodeURIComponent(lang));cached={id:'list|'+lang,sets:result.sets};await write({packs:[cached]});}
      if(token!==viewToken)return;sets=cached.sets;render();
    }catch(e){if(token===viewToken)el('setsStatus').textContent=e.message||'לא ניתן לפתוח את מאגר הסדרות במכשיר.';}
  }
  function count(packKey){return features.filter(f=>f.packKey===packKey).length;}
  function render(){
    if(!el('setsOverlay').classList.contains('open'))return;
    releaseUrls();
    el('setsBack').hidden=!pack;el('setsLanguage').hidden=!!pack;el('setsSearch').hidden=!!pack;
    el('setsTitle').textContent=pack?pack.name:'סדרות להורדה';
    el('setsNote').textContent=pack?'צבע = תמונה שהורדה למכשיר. ההורדה אינה מוסיפה קלפים לאוסף שלך.':'בחר סדרה כדי לצפות בקלפים ולהוריד תמונות להשוואה בזמן סריקה.';
    el('setsActions').innerHTML=activeKey?'<button class="system-action" onclick="HitimSets.deactivate()">השוואת סדרה פעילה · חזור לזיהוי רגיל</button>':'';
    if(!pack){
      el('setsActions').innerHTML+='<button class="system-action" onclick="HitimSets.refresh()">רענן רשימת סדרות</button>';
      const query=el('setsSearch').value.trim().toLowerCase();
      el('setsStatus').textContent=message||(sets.length?'ניתן להוריד חלק מהסדרה ולהמשיך אחר כך.':'אין כרגע סדרות זמינות בשפה זו במקור הנתונים.');
      el('setsContent').innerHTML='<div class="set-grid">'+sets.filter(s=>s.name.toLowerCase().includes(query)||s.id.toLowerCase().includes(query)).map(s=>{
        const n=count(key(language,s.id)),index=sets.indexOf(s);
        return `<button class="set-tile ${n?'downloaded':''}" onclick="HitimSets.enter(${index})"><div class="set-logo">${s.logo?`<img src="${esc(s.logo)}" alt="${esc(s.name)}" loading="lazy" onerror="this.hidden=true">`:''}</div><b dir="auto">${esc(s.name)}</b><span>${n} / ${s.imageCount} תמונות הורדו</span><small>${s.available} קלפים בקטלוג</small>${n?'<em>✓ יש הורדה במכשיר</em>':''}</button>`;
      }).join('')+'</div>';return;
    }
    const downloaded=new Set(features.filter(f=>f.packKey===pack.id).map(f=>f.id));
    const downloadable=pack.cards.filter(c=>c.catalogImage),n=downloadable.filter(c=>downloaded.has(key(pack.id,c.catalogCardId))).length;
    el('setsStatus').textContent=message||`${n} / ${downloadable.length} תמונות הורדו · ${pack.cards.length} קלפים ברשימה`;
    el('setsActions').innerHTML+=`<button class="system-action primary" onclick="HitimSets.download()" ${job||n===downloadable.length?'disabled':''}>${n===downloadable.length?'✓ ההורדה הושלמה':n?'המשך הורדה':'הורד סדרה'}</button>${job?'<button class="system-action" onclick="HitimSets.cancel()">עצור הורדה</button>':''}<button class="system-action" onclick="HitimSets.activate()" ${n?'':'disabled'}>${activeKey===pack.id?'✓ נבחרה להשוואה':'השווה צילומים לסדרה זו'}</button><button class="system-action" onclick="HitimSets.removeDownload()" ${job||!n?'disabled':''}>מחק תמונות שהורדו</button>`;
    const current=pack;
    el('setsContent').innerHTML='<div class="set-card-grid">'+pack.cards.slice(0,shown).map((c,i)=>{
      const id=key(pack.id,c.catalogCardId),ready=downloaded.has(id);
      return `<button class="set-card ${ready?'downloaded':''}" onclick="HitimSets.preview(${i})"><img id="setCardImg${i}" src="${esc(c.catalogImage)}" loading="lazy" alt="${esc(c.name)}" onerror="this.style.visibility='hidden'"><b dir="auto">${esc(c.name)}</b><small dir="ltr">${esc(c.number)}</small><span>${ready?'✓ הורד':c.catalogImage?'טרם הורד':'תמונה חסרה במקור'}</span></button>`;
    }).join('')+'</div>'+(shown<pack.cards.length?'<button class="system-action" onclick="HitimSets.more()">עוד קלפים</button>':'');
    pack.cards.slice(0,shown).forEach(async(c,i)=>{if(!downloaded.has(key(current.id,c.catalogCardId)))return;try{const record=await read('images',key(current.id,c.catalogCardId));if(pack!==current||!el('setsOverlay').classList.contains('open'))return;const img=el('setCardImg'+i);if(img&&record?.blob){const url=URL.createObjectURL(record.blob);urls.push(url);img.style.visibility='';img.src=url;}}catch(e){}});
  }
  async function refresh(){
    const lang=language,token=++viewToken;el('setsStatus').textContent='מרענן רשימה...';
    try{const result=await api('/catalog/sets?language='+encodeURIComponent(lang));await write({packs:[{id:'list|'+lang,sets:result.sets}]});if(token!==viewToken)return;sets=result.sets;message='הרשימה עודכנה.';render();}
    catch(e){if(token===viewToken)el('setsStatus').textContent='הרענון לא הושלם. הרשימה השמורה נשארה זמינה.';}
  }
  async function enter(index){
    const set=sets[index];if(!set)return;const token=++viewToken;el('setsStatus').textContent='טוען קלפים...';
    try{
      const id=key(language,set.id);let item=await read('packs',id);
      if(!item){const result=await api('/catalog/set-cards?language='+encodeURIComponent(language)+'&set_id='+encodeURIComponent(set.id));item={...result,id,name:set.name,setId:set.id};await write({packs:[item]});}
      if(token!==viewToken)return;pack=item;shown=60;message='';render();
    }catch(e){if(token===viewToken)el('setsStatus').textContent=e.message;}
  }
  async function download(){
    if(job||!pack)return;
    const target=pack,existing=new Set(features.map(f=>f.id));
    const pending=target.cards.filter(c=>c.catalogImage&&!existing.has(key(target.id,c.catalogCardId)));
    const task={cancelled:false,controllers:new Set(),done:0,failed:0};job=task;message='מוריד תמונות...';render();
    let position=0;
    async function worker(){while(!task.cancelled&&position<pending.length){
      const card=pending[position++],controller=new AbortController();task.controllers.add(controller);const timer=setTimeout(()=>controller.abort(),20000);
      try{
        const response=await fetch(card.catalogImage,{signal:controller.signal,mode:'cors'});if(!response.ok)throw Error('image');
        const blob=await response.blob();if(task.cancelled)return;
        const vector=await HitimSetVision.fromBlob(blob);if(task.cancelled)return;
        const id=key(target.id,card.catalogCardId),feature={id,packKey:target.id,card,vector,version:1};
        await write({features:[feature],images:[{id,packKey:target.id,blob}]});features=features.filter(f=>f.id!==id);features.push(feature);task.done++;
      }catch(e){if(!task.cancelled){task.failed++;if(e?.name==='QuotaExceededError'){task.cancelled=true;message='אין מספיק מקום במכשיר. התמונות שכבר ירדו נשמרו.';}}}
      finally{clearTimeout(timer);task.controllers.delete(controller);}
      if(pack===target&&el('setsOverlay').classList.contains('open'))el('setsStatus').textContent=`${count(target.id)} / ${target.cards.filter(c=>c.catalogImage).length} תמונות הורדו${task.failed?' · '+task.failed+' לא ירדו':''}`;
    }}
    try{await Promise.all([worker(),worker(),worker()]);}
    finally{
      job=null;
      if(pack===target){message=task.cancelled?(message.includes('מקום')?message:'ההורדה נעצרה. אפשר להמשיך מאותה נקודה.'):task.failed?'חלק מהתמונות לא ירדו. לחץ המשך הורדה לניסיון נוסף.':'ההורדה הושלמה. התמונות זמינות במכשיר.';render();}
    }
  }
  async function removeDownload(){
    if(job||!pack)return;const target=pack;
    try{
      const ids=features.filter(f=>f.packKey===target.id).map(f=>f.id),database=await db();
      await new Promise((resolve,reject)=>{const tx=database.transaction(['features','images'],'readwrite');for(const id of ids){tx.objectStore('features').delete(id);tx.objectStore('images').delete(id);}tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);});
      features=features.filter(f=>f.packKey!==target.id);
      if(activeKey===target.id){activeKey='';try{localStorage.removeItem('hitim-reference-set');}catch(e){}}
      updateBadge();message='תמונות הייחוס הוסרו מהמכשיר. האוסף שלך לא השתנה.';render();
    }catch(e){el('setsStatus').textContent='לא ניתן להסיר את ההורדה כרגע.';}
  }
  function cancel(){if(job){job.cancelled=true;job.controllers.forEach(c=>c.abort());}}
  function updateBadge(){
    try{activeKey=localStorage.getItem('hitim-reference-set')||'';}catch(e){}
    const badge=el('downloadedSetScanMode');if(!badge)return;badge.hidden=!activeKey;
    badge.textContent='השוואת תמונות: '+(pack?.id===activeKey?pack.name:activeKey.split('|').pop())+' · שנה';
  }
  function activate(){activeKey=pack.id;try{localStorage.setItem('hitim-reference-set',activeKey);}catch(e){}updateBadge();message='הסדרה נבחרה להשוואה חזותית ניסיונית. תידרש בחירת התמונה התואמת; הגרסה וההולוגרמה אינן מאומתות כך.';render();}
  async function preview(index){
    const c=pack?.cards[index];if(!c)return;const record=await read('images',key(pack.id,c.catalogCardId));
    const src=record?.blob?URL.createObjectURL(record.blob):c.catalogImage;if(record?.blob)urls.push(src);
    const dialog=el('setPreview');el('setPreviewImage').src=src||'';el('setPreviewName').textContent=c.name+' · '+c.number;dialog.showModal();
  }
  async function match(file){
    try{activeKey=localStorage.getItem('hitim-reference-set')||'';}catch(e){}
    if(!activeKey)return null;
    const records=(await all('features')).filter(f=>f.packKey===activeKey&&f.version===1);
    if(!records.length)return null;
    const query=await HitimSetVision.fromBlob(file,true),ranked=HitimSetVision.rank(query,records);
    if(!ranked.length)return null;
    const candidates=[];
    for(const {record} of ranked){
      const image=await read('images',record.id);if(!image?.blob)continue;
      const localImage=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(image.blob);});
      candidates.push({...record.card,catalogImage:localImage,catalogMatch:'visual-reference',needsConfirmation:true});
    }
    return candidates.length?{needsConfirmation:true,matchCandidates:candidates,identificationMode:'visual-reference',value:'',priceStatus:'pending'}:null;
  }
  async function localIdentify(file,ocr){
    const found=await match(file).catch(()=>null);
    // Explicit selected-set mode returns visual choices, never an automatic ID.
    return found||ocr();
  }
  function deactivate(){activeKey='';try{localStorage.removeItem('hitim-reference-set');}catch(e){}updateBadge();message='חזרת לזיהוי הרגיל בכל הקטלוג.';render();}

  updateBadge();
  return {open,close,refresh,deactivate,removeDownload,loadLanguage,render,enter,download,cancel,activate,preview,localIdentify,match,back(){viewToken++;pack=null;message='';render();},more(){shown+=60;render();}};
})();
