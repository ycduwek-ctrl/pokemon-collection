/* Public reference images have their own DB, separate from personal collections. */
const HitimSets=(()=>{
  let dbPromise,language='English',sets=[],pack=null,features=[],job=null,viewToken=0,shown=60;
  let urls=[],activeKey='',message='',sortMode='hits',showUnavailable=false,renderToken=0;
  const CACHE_VERSION=2,CACHE_AGE=24*60*60*1000;
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
  async function metadata(id,path,extra={},force=false){
    const cached=await read('packs',id);
    if(!force&&cached?.cacheVersion===CACHE_VERSION&&Date.now()-cached.fetchedAt<CACHE_AGE)return cached;
    try{const result=await api(path);const value={...result,...extra,id,cacheVersion:result.version===CACHE_VERSION?CACHE_VERSION:0,fetchedAt:Date.now()};await write({packs:[value]});return value;}
    catch(e){if(cached){message='מוצגת הרשימה השמורה; הרענון לא הושלם.';return cached;}throw e;}
  }
  function sources(card){return [...new Set([...(card.imageSources||[]),card.catalogImage].filter(Boolean))];}
  function orderedCards(){return [...(pack?.cards||[])].sort((a,b)=>(sortMode==='hits'?(b.hitRank||0)-(a.hitRank||0):0)||String(a.number).localeCompare(String(b.number),'en',{numeric:true}));}
  function imageFailed(img,index){
    const card=orderedCards()[index];if(!card)return;
    const candidates=[...new Set([card.thumbnail,...sources(card)].filter(Boolean))];
    const next=Number(img.dataset.attempt||0)+1;img.dataset.attempt=next;
    if(next<candidates.length){img.src=candidates[next];return;}
    img.hidden=true;const label=img.parentElement.querySelector('.set-image-missing');if(label)label.hidden=false;
  }
  function logoFailed(img,index){const s=sets[index];if(!img.dataset.fallback&&s?.preview){img.dataset.fallback='1';img.src=s.preview;img.classList.add('card-cover');}else{img.hidden=true;}}
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
      const cached=await metadata('list|'+lang,'/catalog/sets?language='+encodeURIComponent(lang));
      if(token!==viewToken)return;sets=cached.sets;render();
    }catch(e){if(token===viewToken)el('setsStatus').textContent=e.message||'לא ניתן לפתוח את מאגר הסדרות במכשיר.';}
  }
  function count(packKey){return features.filter(f=>f.packKey===packKey).length;}
  function render(){
    if(!el('setsOverlay').classList.contains('open'))return;
    const rendering=++renderToken;
    releaseUrls();
    el('setsBack').hidden=!pack;el('setsLanguage').hidden=!!pack;el('setsSearch').hidden=!!pack;
    el('setsTitle').textContent=pack?pack.name:'סדרות להורדה';
    el('setsNote').textContent=pack?'צבע = הורד למכשיר. היטים תחילה: לפי נדירות וסוג קלף, לא לפי מחיר שוק.':'בחר סדרה כדי לצפות בקלפים ולהוריד תמונות להשוואה בזמן סריקה.';
    el('setsActions').innerHTML=activeKey?'<button class="system-action" onclick="HitimSets.deactivate()">השוואת סדרה פעילה · חזור לזיהוי רגיל</button>':'';
    if(!pack){
      el('setsActions').innerHTML+=`<button class="system-action" onclick="HitimSets.refresh()">רענן רשימת סדרות</button><button class="system-action" onclick="HitimSets.filterMcDonalds()">מקדונלד׳ס</button><button class="system-action" onclick="HitimSets.toggleUnavailable()">${showUnavailable?'הסתר סדרות ללא תמונות':'הצג גם סדרות ללא תמונות'}</button>`;
      const query=el('setsSearch').value.trim().toLowerCase().replace(/מקדונלד[׳'’]?ס?/g,'mcdonald');
      el('setsStatus').textContent=message||(sets.length?'ניתן להוריד חלק מהסדרה ולהמשיך אחר כך.':'אין כרגע סדרות זמינות בשפה זו במקור הנתונים.');
      el('setsContent').innerHTML='<div class="set-grid">'+sets.filter(s=>(showUnavailable||s.imageCount>0)&&(s.name.toLowerCase().includes(query)||s.id.toLowerCase().includes(query))).map(s=>{
        const n=count(key(language,s.id)),index=sets.indexOf(s);
        return `<button class="set-tile ${n?'downloaded':''}" onclick="HitimSets.enter(${index})"><div class="set-logo"><span class="set-cover-label" dir="auto">${esc(s.name)}</span>${s.logo||s.preview?`<img class="${s.logo?'':'card-cover'}" src="${esc(s.logo||s.preview)}" alt="${esc(s.name)}" loading="lazy" onerror="HitimSets.logoFailed(this,${index})">`:''}</div><b dir="auto">${esc(s.name)}</b><span>${s.imageCount?`${n} / ${s.imageCount} תמונות הורדו`:'אין תמונות להורדה במקור'}</span><small>${s.available} קלפים בקטלוג${s.imageCount<s.available?' · תמונות חסרות':''}</small>${n?'<em>✓ יש הורדה במכשיר</em>':''}</button>`;
      }).join('')+'</div>';return;
    }
    const downloaded=new Set(features.filter(f=>f.packKey===pack.id).map(f=>f.id));
    const downloadable=pack.cards.filter(c=>c.catalogImage),n=downloadable.filter(c=>downloaded.has(key(pack.id,c.catalogCardId))).length;
    const missing=pack.cards.length-downloadable.length;
    el('setsStatus').textContent=(message||`${n} / ${downloadable.length} תמונות הורדו · ${pack.cards.length} קלפים ברשימה`)+(missing?` · ל־${missing} קלפים אין תמונה במקור`: '');
    el('setsActions').innerHTML+=`<button class="system-action primary" onclick="HitimSets.download()" ${job||!downloadable.length||n===downloadable.length?'disabled':''}>${!downloadable.length?'אין תמונות להורדה':n===downloadable.length?'✓ התמונות הזמינות הורדו':n?'המשך הורדה':'הורד סדרה'}</button>${job?'<button class="system-action" onclick="HitimSets.cancel()">עצור הורדה</button>':''}<button class="system-action" onclick="HitimSets.activate()" ${n?'':'disabled'}>${activeKey===pack.id?'✓ נבחרה להשוואה':'השווה צילומים לסדרה זו'}</button><button class="system-action" onclick="HitimSets.refresh()" ${job?'disabled':''}>רענן קלפים ותמונות</button><button class="system-action" onclick="HitimSets.removeDownload()" ${job||!n?'disabled':''}>מחק תמונות שהורדו</button><label class="set-sort">מיון <select aria-label="מיון קלפים" onchange="HitimSets.sort(this.value)"><option value="hits" ${sortMode==='hits'?'selected':''}>היטים תחילה</option><option value="number" ${sortMode==='number'?'selected':''}>מספר קלף</option></select></label>`;
    if(/mcdonald/i.test(pack.name))el('setsActions').innerHTML+='<p class="set-promo-note">האיור עשוי להופיע גם בסדרה אחרת. לצילום חשוב לכלול את המספר ואת קוד M שבשוליים. אפשר לבחור סדרה זו להשוואה או לחפש לפי הקוד והמספר.</p>';
    const current=pack;
    const visible=orderedCards().slice(0,shown);
    el('setsContent').innerHTML='<div class="set-card-grid">'+visible.map((c,i)=>{
      const id=key(pack.id,c.catalogCardId),ready=downloaded.has(id);
      return `<button class="set-card ${ready?'downloaded':''}" onclick="HitimSets.preview(${i})">${c.catalogImage?`<img id="setCardImg${i}" src="${esc(c.thumbnail||c.catalogImage)}" loading="lazy" alt="${esc(c.name)}" onerror="HitimSets.imageFailed(this,${i})">`:''}<span class="set-image-missing" ${c.catalogImage?'hidden':''}>תמונה אינה זמינה</span><b dir="auto">${esc(c.name)}</b><small dir="ltr">${esc(c.number)}</small>${c.rarity?`<small class="set-rarity" dir="ltr">${esc(c.rarity)}</small>`:''}<span>${ready?'✓ הורד':c.catalogImage?'טרם הורד':'תמונה חסרה במקור'}</span></button>`;
    }).join('')+'</div>'+(shown<pack.cards.length?'<button class="system-action" onclick="HitimSets.more()">עוד קלפים</button>':'');
    visible.forEach(async(c,i)=>{if(!downloaded.has(key(current.id,c.catalogCardId)))return;try{const record=await read('images',key(current.id,c.catalogCardId));if(pack!==current||rendering!==renderToken||!el('setsOverlay').classList.contains('open'))return;const img=el('setCardImg'+i);if(img&&record?.blob){const url=URL.createObjectURL(record.blob);urls.push(url);img.hidden=false;img.onerror=null;img.src=url;const label=img.parentElement?.querySelector('.set-image-missing');if(label)label.hidden=true;}}catch(e){}});
  }
  async function refresh(){
    if(job)return;
    const lang=language,current=pack,token=++viewToken;message='';el('setsStatus').textContent='מרענן רשימה...';
    try{
      const result=current?await metadata(current.id,'/catalog/set-cards?language='+encodeURIComponent(lang)+'&set_id='+encodeURIComponent(current.setId),{name:current.name,setId:current.setId},true):await metadata('list|'+lang,'/catalog/sets?language='+encodeURIComponent(lang),{},true);
      if(token!==viewToken)return;if(current)pack=result;else sets=result.sets;message=message||'הרשימה עודכנה.';render();
    }
    catch(e){if(token===viewToken)el('setsStatus').textContent='הרענון לא הושלם. הרשימה השמורה נשארה זמינה.';}
  }
  async function enter(index){
    const set=sets[index];if(!set)return;const token=++viewToken;el('setsStatus').textContent='טוען קלפים...';
    try{
      const id=key(language,set.id);message='';
      const item=await metadata(id,'/catalog/set-cards?language='+encodeURIComponent(language)+'&set_id='+encodeURIComponent(set.id),{name:set.name,setId:set.id});
      if(token!==viewToken)return;pack=item;shown=60;render();
    }catch(e){if(token===viewToken)el('setsStatus').textContent=e.message;}
  }
  async function download(){
    if(job||!pack)return;
    const target=pack,existing=new Set(features.map(f=>f.id));
    const pending=target.cards.filter(c=>c.catalogImage&&!existing.has(key(target.id,c.catalogCardId)));
    if(!pending.length)return;
    const task={cancelled:false,controllers:new Set(),done:0,failed:0};job=task;message='מוריד תמונות...';render();
    let position=0;
    async function worker(){while(!task.cancelled&&position<pending.length){
      const card=pending[position++];
      try{
        const {blob,vector}=await fetchReference(card,task);if(task.cancelled)return;
        const id=key(target.id,card.catalogCardId),feature={id,packKey:target.id,card,vector,version:1};
        await write({features:[feature],images:[{id,packKey:target.id,blob}]});features=features.filter(f=>f.id!==id);features.push(feature);task.done++;
      }catch(e){if(!task.cancelled){task.failed++;if(e?.name==='QuotaExceededError'){task.cancelled=true;message='אין מספיק מקום במכשיר. התמונות שכבר ירדו נשמרו.';}}}
      if(pack===target&&el('setsOverlay').classList.contains('open'))el('setsStatus').textContent=`${count(target.id)} / ${target.cards.filter(c=>c.catalogImage).length} תמונות הורדו${task.failed?' · '+task.failed+' לא ירדו':''}`;
    }}
    try{await Promise.all([worker(),worker(),worker()]);}
    finally{
      job=null;
      if(pack===target){message=task.cancelled?(message.includes('מקום')?message:'ההורדה נעצרה. אפשר להמשיך מאותה נקודה.'):task.failed?`${task.failed} תמונות לא ירדו גם לאחר ניסיון חוזר. לחץ המשך הורדה; מה שכבר ירד נשמר.`:'התמונות הזמינות הורדו ונשמרו במכשיר.';render();}
    }
  }
  async function fetchReference(card,task){
    const candidates=sources(card);let lastError;
    // Each URL is tried once, then the primary gets one retry. Both network and
    // decode failures are recoverable; corrupt/HTML blobs never count as saved.
    const direct=candidates.filter(url=>!/^https:\/\/(pkmncards\.com|tcgplayer-cdn\.tcgplayer\.com)\//.test(url));
    for(const url of direct.length?[...direct,direct[0]]:[]){
      if(task.cancelled)throw Error('cancelled');
      const controller=new AbortController();task.controllers.add(controller);
      const timer=setTimeout(()=>controller.abort(),15000);
      try{
        const response=await fetch(url,{signal:controller.signal,mode:'cors'});if(!response.ok)throw Error('image');
        const blob=await response.blob();const vector=await HitimSetVision.fromBlob(blob);
        return {blob,vector};
      }catch(e){lastError=e;}
      finally{clearTimeout(timer);task.controllers.delete(controller);}
    }
    // Some public image hosts do not send CORS headers. The authenticated
    // server resolves the card ID against the same trusted catalogue.
    for(let source=0;source<candidates.length;source++){
      if(task.cancelled)throw Error('cancelled');
      const controller=new AbortController();task.controllers.add(controller);
      try{
        const response=await HitimAuth.fetch('/catalog/reference-image?language='+encodeURIComponent(card.language||'English')+'&card_id='+encodeURIComponent(card.catalogCardId)+'&source='+source,{signal:controller.signal,timeoutMs:30000});
        if(!response.ok)throw Error('image');
        const blob=await response.blob(),vector=await HitimSetVision.fromBlob(blob);return {blob,vector};
      }catch(e){lastError=e;}
      finally{task.controllers.delete(controller);}
    }
    throw lastError||Error('image');
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
    const c=orderedCards()[index];if(!c)return;const record=await read('images',key(pack.id,c.catalogCardId));
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
    const candidates=[],latest=await read('packs',activeKey);
    for(const {record} of ranked){
      const image=await read('images',record.id);if(!image?.blob)continue;
      const localImage=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(image.blob);});
      const metadata=latest?.cards?.find(c=>c.catalogCardId===record.card.catalogCardId)||record.card;
      candidates.push({...metadata,catalogImage:localImage,catalogMatch:'visual-reference',needsConfirmation:true});
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
  return {open,close,refresh,deactivate,removeDownload,loadLanguage,render,enter,download,cancel,activate,preview,localIdentify,match,imageFailed,logoFailed,
    sort(value){sortMode=value==='number'?'number':'hits';shown=60;render();},
    toggleUnavailable(){showUnavailable=!showUnavailable;render();},
    filterMcDonalds(){showUnavailable=true;el('setsSearch').value='mcdonald';render();},
    back(){viewToken++;pack=null;message='';render();},more(){shown+=60;render();}};
})();
