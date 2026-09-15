/* Guided capture: crop exactly the visible guide, never apply a synthetic zoom. */
const HitimCamera=(()=>{
  let stream=null,generation=0,capturing=false;
  const el=id=>document.getElementById(id);
  const stop=s=>s?.getTracks().forEach(track=>track.stop());
  function close(){
    generation++;stop(stream);stream=null;capturing=false;
    const video=el('scanVideo');if(video){video.pause();video.srcObject=null;}
    el('scanCaptureOverlay')?.classList.remove('open');
  }
  function native(){close();const input=el('quickFrontCamera');input.value='';input.click();}
  function gallery(){close();const input=el('quickGalleryInput');input.value='';input.click();}
  async function open(){
    close();const token=generation;
    el('scanCaptureOverlay').classList.add('open');el('scanCaptureBtn').disabled=true;
    el('scanCameraStatus').textContent='פותח מצלמה...';
    if(!navigator.mediaDevices?.getUserMedia){el('scanCameraStatus').textContent='המצלמה הפנימית לא זמינה. בחר מצלמת מכשיר או גלריה.';return;}
    try{
      const acquired=await navigator.mediaDevices.getUserMedia({audio:false,video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1440}}});
      if(token!==generation){stop(acquired);return;}
      stream=acquired;const video=el('scanVideo');video.srcObject=acquired;await video.play();
      if(token!==generation)return;
      const track=stream.getVideoTracks()[0],caps=track.getCapabilities?.()||{};
      if(caps.focusMode?.includes('continuous'))await track.applyConstraints({advanced:[{focusMode:'continuous'}]}).catch(()=>{});
      if(token!==generation)return;
      el('scanCameraStatus').textContent='מלא את המסגרת בקלף אחד, כולל המספר בתחתית. הטה מעט אם יש השתקפות.';
      el('scanCaptureBtn').disabled=false;
    }catch(error){
      if(token!==generation)return;
      stop(stream);stream=null;
      el('scanCameraStatus').textContent='לא ניתן לפתוח מצלמה כאן. אפשר לצלם דרך מצלמת המכשיר או לבחור מהגלריה.';
    }
  }
  // Convert a CSS rectangle into source pixels for object-fit: cover, centered.
  function sourceRect(sw,sh,view,guide){
    const scale=Math.max(view.width/sw,view.height/sh);
    const offsetX=(sw*scale-view.width)/2,offsetY=(sh*scale-view.height)/2;
    const x=Math.max(0,(guide.left-view.left+offsetX)/scale),y=Math.max(0,(guide.top-view.top+offsetY)/scale);
    return {x,y,width:Math.min(sw-x,guide.width/scale),height:Math.min(sh-y,guide.height/scale)};
  }
  async function capture(){
    const video=el('scanVideo');if(capturing||!stream||!video.videoWidth||video.readyState<2)return;
    capturing=true;el('scanCaptureBtn').disabled=true;const token=generation,session=quickSessionToken;
    try{
      const rect=sourceRect(video.videoWidth,video.videoHeight,video.getBoundingClientRect(),el('scanGuide').getBoundingClientRect());
      const canvas=document.createElement('canvas');const scale=Math.min(1,1800/Math.max(rect.width,rect.height));
      canvas.width=Math.max(1,Math.round(rect.width*scale));canvas.height=Math.max(1,Math.round(rect.height*scale));
      canvas.getContext('2d',{alpha:false}).drawImage(video,rect.x,rect.y,rect.width,rect.height,0,0,canvas.width,canvas.height);
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.94));
      if(token!==generation||session!==quickSessionToken)return;
      if(!blob)throw new Error('capture');
      close();quickFilesSelected({files:[new File([blob],'hitim-card.jpg',{type:'image/jpeg',lastModified:Date.now()})],value:''});
    }catch(error){
      if(token!==generation)return;
      capturing=false;el('scanCaptureBtn').disabled=false;el('scanCameraStatus').textContent='הצילום לא נשמר. נסה שוב או בחר מצלמת מכשיר.';
    }
  }
  document.addEventListener('visibilitychange',()=>{if(document.hidden)close();});
  window.addEventListener('pagehide',close);
  document.addEventListener('keydown',event=>{if(event.key==='Escape')close();});
  return {open,close,native,gallery,capture,sourceRect};
})();
