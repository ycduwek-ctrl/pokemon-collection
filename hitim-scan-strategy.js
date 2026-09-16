/* A local reading gets a short head start. Ambiguous OCR must reach vision. */
const HitimScanStrategy=(()=>{
  function samePrinting(a,b){
    const clean=v=>String(v||'').trim().toLowerCase();
    return Boolean(a?.catalogCardId&&b?.catalogCardId&&clean(a.catalogCardId)===clean(b.catalogCardId)&&clean(a.language)===clean(b.language));
  }
  async function identify({local,vision,needsName,translate,sanitize,mode='quick',forceAi=false,localBudgetMs=5000}){
    let localResult=null,localError=null,timer;
    const startLocal=()=>local().then(result=>{localResult=result;return result;},error=>{localError=error;return null;});
    try{
      if(mode==='quick'&&!forceAi){
        await Promise.race([startLocal(),new Promise(resolve=>{timer=setTimeout(resolve,localBudgetMs);})]);
        clearTimeout(timer);
        if(localResult?.identificationMode==='visual-reference'&&localResult.needsConfirmation)return localResult;
        if(localResult&&!localResult.needsConfirmation&&!needsName(localResult))return localResult;
      }
      try{
        const result=await vision();
        if(localResult&&!localResult.needsConfirmation&&needsName(localResult)&&samePrinting(localResult,result))return translate(localResult,result);
        if(result.catalogCardId&&!result.needsConfirmation)return result;
        // Preserve visual choices when vision cannot verify a printing.
        if(localResult)return sanitize(localResult);
        return result;
      }catch(error){
        if(localResult)return sanitize(localResult);
        if(mode==='deep'){
          await Promise.race([startLocal(),new Promise(resolve=>{timer=setTimeout(resolve,localBudgetMs);})]);
          if(localResult)return sanitize(localResult);
        }
        throw new Error(error?.message||localError?.message||'הזיהוי לא הושלם');
      }
    }finally{clearTimeout(timer);}
  }
  return {identify,samePrinting};
})();
