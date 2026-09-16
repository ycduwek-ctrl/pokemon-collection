// Run with fake-indexeddb 6 on NODE_PATH (test-only dependency, not shipped).
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const {indexedDB}=require('fake-indexeddb');
const elements=new Map();
function element(id){if(!elements.has(id)){const classes=new Set();elements.set(id,{value:'',innerHTML:'',textContent:'',hidden:false,style:{},classList:{add:v=>classes.add(v),remove:v=>classes.delete(v),contains:v=>classes.has(v)},replaceChildren(){this.innerHTML='';},close(){},showModal(){}});}return elements.get(id);}
const storage=new Map();let requests=0,failSecond=true,offline=false;
const cards=[1,2].map(n=>({catalogCardId:'me02.5-'+n,setCode:'me02.5',name:'Card '+n,number:n+'/217',language:'English',catalogImage:'https://test/'+n,needsEnglishName:false}));
const ctx=vm.createContext({indexedDB,console,URL,Blob,setTimeout,clearTimeout,AbortController,FileReader:class{readAsDataURL(){this.result='data:image/webp;base64,AAAA';this.onload();}},document:{getElementById:element},localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},escapeHtml:String,closeNavigationViews(){},closePanel(){},HitimDB:{putCard(){throw Error('Must never change user collection');}},fetch:async url=>{requests++;return {ok:!(url.endsWith('/2')&&failSecond),blob:async()=>new Blob(['image'])};},HitimAuth:{fetch:async url=>{if(offline)throw Error('offline');return {ok:true,json:async()=>url.startsWith('/catalog/sets?')?{sets:[{id:'me02.5',name:'Ascended Heroes',available:2,imageCount:2}]}:{id:'me02.5',cards}};}}});
vm.runInContext(fs.readFileSync('hitim-set-vision.js','utf8'),ctx);
vm.runInContext('HitimSetVision.fromBlob=async()=>[0,1,0];',ctx);
vm.runInContext(fs.readFileSync('hitim-sets.js','utf8')+'\nthis.sets=HitimSets;',ctx);
(async()=>{
await ctx.sets.open();await ctx.sets.enter(0);await ctx.sets.download();assert.equal(requests,2);assert(element('setsStatus').textContent.includes('חלק'));
failSecond=false;await ctx.sets.download();assert.equal(requests,3,'resume must skip saved card');assert(element('setsContent').innerHTML.includes('downloaded'));
ctx.sets.activate();assert.equal(storage.get('hitim-reference-set'),'English|me02.5');
const match=await ctx.sets.match(new Blob(['photo']));assert.equal(match.needsConfirmation,true);assert.equal(match.matchCandidates.length,2);assert(match.matchCandidates[0].catalogImage.startsWith('data:'));
offline=true;await ctx.sets.open();await ctx.sets.enter(0);assert(element('setsContent').innerHTML.includes('Card 1'));
await ctx.sets.removeDownload();assert.equal(await ctx.sets.match(new Blob(['photo'])),null);assert.equal(storage.get('hitim-reference-set'),undefined);
console.log('Downloaded sets: partial failure, resume, offline metadata/images, visual confirmation and cache-only deletion passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
