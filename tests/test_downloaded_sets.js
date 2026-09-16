// Run with fake-indexeddb 6 on NODE_PATH (test-only dependency, not shipped).
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const {indexedDB}=require('fake-indexeddb');
const elements=new Map();
function element(id){if(!elements.has(id)){const classes=new Set();elements.set(id,{value:'',innerHTML:'',textContent:'',hidden:false,style:{},classList:{add:v=>classes.add(v),remove:v=>classes.delete(v),contains:v=>classes.has(v)},replaceChildren(){this.innerHTML='';},close(){},showModal(){}});}return elements.get(id);}
const storage=new Map();let requests=0,failSecond=true,offline=false,apiRequests=0;
const cards=[1,2,3,4].map(n=>({catalogCardId:'me02.5-'+n,setCode:'me02.5',name:'Card '+n,number:n+'/217',language:'English',catalogImage:'https://test/'+n,needsEnglishName:false,hitRank:n===2?95:n===3?80:0}));
cards[2].imageSources=['https://test/3','https://test/3-alt'];cards[3].catalogImage='';
const ctx=vm.createContext({indexedDB,console,URL,Blob,setTimeout,clearTimeout,AbortController,FileReader:class{readAsDataURL(){this.result='data:image/webp;base64,AAAA';this.onload();}},document:{getElementById:element},localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},escapeHtml:String,closeNavigationViews(){},closePanel(){},HitimDB:{putCard(){throw Error('Must never change user collection');}},fetch:async url=>{requests++;return {ok:!(url.endsWith('/2')&&failSecond)&&!url.endsWith('/3'),blob:async()=>new Blob(['image'])};},HitimAuth:{fetch:async url=>{if(url.startsWith('/catalog/reference-image'))return {ok:false};apiRequests++;if(offline)throw Error('offline');return {ok:true,json:async()=>url.startsWith('/catalog/sets?')?{sets:[{id:'me02.5',name:'Ascended Heroes',available:4,imageCount:3}]}:{id:'me02.5',cards}};}}});
vm.runInContext(fs.readFileSync('hitim-set-vision.js','utf8'),ctx);
vm.runInContext('HitimSetVision.fromBlob=async()=>[0,1,0];',ctx);
vm.runInContext(fs.readFileSync('hitim-sets.js','utf8')+'\nthis.sets=HitimSets;',ctx);
(async()=>{
// Seed the previous release's permanent stale manifests. They must refresh.
const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('hitim-public-set-references',1);r.onupgradeneeded=()=>{for(const name of ['packs','images','features']){const store=r.result.createObjectStore(name,{keyPath:'id'});if(name!=='packs')store.createIndex('packKey','packKey');}};r.onsuccess=()=>resolve(r.result);r.onerror=reject;});
await new Promise(resolve=>{const t=db.transaction('packs','readwrite');t.objectStore('packs').put({id:'list|English',sets:[]});t.objectStore('packs').put({id:'English|me02.5',cards:[],name:'Ascended Heroes',setId:'me02.5'});t.oncomplete=resolve;});
await ctx.sets.open();await ctx.sets.enter(0);assert.equal(apiRequests,2,'old cached manifests must refresh automatically');
let html=element('setsContent').innerHTML;assert(html.indexOf('Card 2')<html.indexOf('Card 1'),'hits appear first');
ctx.sets.sort('number');html=element('setsContent').innerHTML;assert(html.indexOf('Card 1')<html.indexOf('Card 2'));
ctx.sets.sort('hits');await ctx.sets.preview(0);assert(element('setPreviewName').textContent.startsWith('Card 2'),'preview follows sorted order');
await ctx.sets.download();assert.equal(requests,5);assert(element('setsStatus').textContent.includes('ניסיון חוזר'));assert(element('setsStatus').textContent.includes('אין תמונה'));

failSecond=false;await ctx.sets.download();assert.equal(requests,6,'resume must skip saved card');assert(element('setsContent').innerHTML.includes('downloaded'));
ctx.sets.activate();assert.equal(storage.get('hitim-reference-set'),'English|me02.5');
const match=await ctx.sets.match(new Blob(['photo']));assert.equal(match.needsConfirmation,true);assert.equal(match.matchCandidates.length,3);assert(match.matchCandidates[0].catalogImage.startsWith('data:'));
offline=true;await ctx.sets.open();await ctx.sets.enter(0);assert(element('setsContent').innerHTML.includes('Card 1'));
await ctx.sets.removeDownload();assert.equal(await ctx.sets.match(new Blob(['photo'])),null);assert.equal(storage.get('hitim-reference-set'),undefined);
// An empty image pack must never be labelled as completed.
offline=false;cards.forEach(c=>{c.catalogImage='';c.imageSources=[];});await ctx.sets.refresh();
assert(element('setsActions').innerHTML.includes('אין תמונות להורדה'));assert(!element('setsActions').innerHTML.includes('✓ התמונות הזמינות הורדו'));
console.log('Downloaded sets: stale-cache migration, hits/number sorting, preview, fallback, retry/resume, missing images, offline and safe deletion passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
