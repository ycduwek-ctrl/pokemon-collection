const fs=require('fs'),vm=require('vm'),assert=require('assert');
const ctx=vm.createContext({setTimeout,clearTimeout});
vm.runInContext(fs.readFileSync('hitim-scan-strategy.js','utf8')+'\nthis.strategy=HitimScanStrategy;',ctx);
const base={needsName:c=>!!c.needsEnglishName,translate:(a,b)=>({...a,name:b.name}),sanitize:a=>a};
(async()=>{
let calls=0;const exact={catalogCardId:'AS5a-035',language:'Chinese',name:'Blastoise'};
assert.equal(await ctx.strategy.identify({...base,local:async()=>exact,vision:async()=>{calls++;return exact;}}),exact);assert.equal(calls,0);
const choices={needsConfirmation:true,matchCandidates:[exact]};
assert.equal(await ctx.strategy.identify({...base,local:async()=>choices,vision:async()=>{calls++;return exact;}}),exact);assert.equal(calls,1);
assert.equal(await ctx.strategy.identify({...base,local:async()=>choices,vision:async()=>{throw Error('offline');}}),choices);
assert.equal(await ctx.strategy.identify({...base,local:async()=>choices,vision:async()=>({name:'unverified guess'})}),choices);
assert.equal(await ctx.strategy.identify({...base,local:()=>new Promise(()=>{}),vision:async()=>exact,localBudgetMs:5}),exact);
let resolveLate;const late=new Promise(resolve=>resolveLate=resolve);
assert.equal(await ctx.strategy.identify({...base,local:()=>late,vision:async()=>{resolveLate(exact);await Promise.resolve();throw Error('offline');},localBudgetMs:5}),exact);
assert.equal(ctx.strategy.samePrinting(exact,{...exact,language:'English'}),false);
assert.equal(ctx.strategy.samePrinting(exact,{...exact,catalogCardId:'wrong'}),false);
console.log('Scan strategy: exact OCR, ambiguous OCR, timeout, late fallback, failed AI and printing isolation passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
