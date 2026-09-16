/* Experimental aligned-card comparison. Always requires human confirmation. */
const HitimSetVision=(()=>{
  const WIDTH=12,HEIGHT=16;
  function descriptor(rgba){
    const out=[];let mean=0;
    for(let i=0;i<rgba.length;i+=4){const y=.299*rgba[i]+.587*rgba[i+1]+.114*rgba[i+2];out.push(y,rgba[i]-y,rgba[i+2]-y);mean+=y;}
    mean/=out.length/3;
    let variance=0;for(let i=0;i<out.length;i+=3)variance+=(out[i]-mean)**2;
    const scale=Math.max(24,Math.sqrt(variance/(out.length/3)));
    for(let i=0;i<out.length;i+=3){out[i]=(out[i]-mean)/scale;out[i+1]/=128;out[i+2]/=128;}
    return out;
  }
  async function fromBlob(blob,aligned=false){
    const bitmap=await createImageBitmap(blob,{imageOrientation:'from-image'});
    try{
      if(aligned&&(bitmap.width/bitmap.height<.58||bitmap.width/bitmap.height>.84))throw new Error('Card must fill the frame for visual comparison');
      const canvas=document.createElement('canvas');canvas.width=WIDTH;canvas.height=HEIGHT;
      const ctx=canvas.getContext('2d',{willReadFrequently:true});
      // Ignore narrow frame edges; guided camera already removes the background.
      ctx.drawImage(bitmap,bitmap.width*.07,bitmap.height*.07,bitmap.width*.86,bitmap.height*.86,0,0,WIDTH,HEIGHT);
      return descriptor(ctx.getImageData(0,0,WIDTH,HEIGHT).data);
    }finally{bitmap.close();}
  }
  function distance(a,b){
    if(!a||!b||a.length!==b.length||!a.length)return Infinity;
    let sum=0;for(let i=0;i<a.length;i++)sum+=(a[i]-b[i])**2;
    return Math.sqrt(sum/a.length);
  }
  function rank(query,records){
    return records.map(r=>({record:r,distance:distance(query,r.vector)}))
      .filter(r=>Number.isFinite(r.distance)&&r.distance<.65)
      .sort((a,b)=>a.distance-b.distance).slice(0,6);
  }
  return {descriptor,fromBlob,distance,rank};
})();
