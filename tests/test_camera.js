const fs=require('fs'),vm=require('vm'),assert=require('assert');
const nodes=new Map();
const document={addEventListener(){},getElementById(id){if(!nodes.has(id))nodes.set(id,{value:'',disabled:false,textContent:'',classList:{add(){},remove(){}},pause(){},async play(){},click(){this.clicked=true;}});return nodes.get(id);}};
let resolveStream,stopped=0;
const ctx=vm.createContext({document,window:{addEventListener(){}},navigator:{mediaDevices:{getUserMedia:()=>new Promise(r=>resolveStream=r)}},quickSessionToken:3});
vm.runInContext(fs.readFileSync('hitim-camera.js','utf8')+'\nthis.camera=HitimCamera;',ctx);
(async()=>{
const r=ctx.camera.sourceRect(1920,1080,{left:0,top:0,width:400,height:600},{left:80,top:100,width:240,height:400});
assert.equal(r.width,432);assert.equal(r.height,720);assert.equal(r.y,180);assert.equal(r.x,744);
const portrait=ctx.camera.sourceRect(1080,1920,{left:20,top:50,width:300,height:600},{left:50,top:150,width:240,height:400});
assert(portrait.x>=0&&portrait.x+portrait.width<=1080);assert(portrait.y>=0&&portrait.y+portrait.height<=1920);
const track={stop(){stopped++;},getCapabilities(){return {};}};const stream={getTracks:()=>[track],getVideoTracks:()=>[track]};
const opened=ctx.camera.open();ctx.camera.close();resolveStream(stream);await opened;assert.equal(stopped,1);
const reopened=ctx.camera.open();resolveStream(stream);await reopened;assert.equal(document.getElementById('scanCaptureBtn').disabled,false);
ctx.camera.native();assert.equal(stopped,2);assert.equal(document.getElementById('quickFrontCamera').clicked,true);assert.equal(ctx.quickSessionToken,3);
console.log('Camera: portrait/landscape crop, late permissions cleanup, stream release and native fallback passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
