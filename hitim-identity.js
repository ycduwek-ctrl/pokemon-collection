let identityContext=null,identityOptions=[],identityRequest=0,identityQuery=null;
const identityElement=id=>document.getElementById(id);
function openIdentityHelp(scanId=null){
  const scan=scanId?quickScans.find(item=>item.id===scanId):null;
  if(scanId&&(!scan||scan.added||scan.saving||scan.refining||scan.loadingMore))return;
  identityContext={scanId,session:quickSessionToken,editId};identityRequest++;identityOptions=[];identityQuery=null;
  // Do not prefill identifiers from a rejected automatic match.
  ['identitySet','identityNumber','identityName'].forEach(id=>identityElement(id).value='');
  identityElement('identityLanguage').value='auto';
  identityElement('identityStatus').textContent='לדוגמה: סינית מסורתית · AS5a · 035/184';
  identityElement('identityResults').replaceChildren();identityElement('identityMore').hidden=true;
  identityElement('identityHelpOverlay').classList.add('open');
}
function closeIdentityHelp(){identityRequest++;identityContext=null;identityElement('identityHelpOverlay').classList.remove('open');}
function identityHints(){return {name:identityElement('identityName').value.trim(),number:identityElement('identityNumber').value.trim(),setCode:identityElement('identitySet').value.trim(),language:identityElement('identityLanguage').value};}
async function searchIdentityHelp(more=false){
  const hints=identityHints();
  if(!hints.name&&!hints.number){identityElement('identityStatus').textContent='הוסף מספר קלף או שם כדי לחפש.';return;}
  if(JSON.stringify(hints)!==identityQuery)more=false;
  identityQuery=JSON.stringify(hints);
  const token=++identityRequest;
  if(!more){identityOptions=[];identityElement('identityResults').replaceChildren();}
  identityElement('identityMore').hidden=true;identityElement('identityStatus').textContent='מחפש את המהדורה בקטלוג...';
  try{
    const data=await requestCatalogSearch(hints.name,hints.number,identityOptions.length,6,{language:hints.language,setCode:hints.setCode});
    if(token!==identityRequest||!identityContext)return;
    identityOptions.push(...data.candidates);
    identityElement('identityResults').innerHTML=identityOptions.map((c,i)=>`<button class="variant-option" onclick="selectIdentityHelp(${i})"><img src="${escapeHtml(c.catalogImage)}" alt=""><span><b>${escapeHtml(c.name||c.printedName||c.pokemon)}</b><br>${escapeHtml(c.setCode||c.set)} · ${escapeHtml(c.number)}<br>${escapeHtml(c.language)}</span><span>בחר</span></button>`).join('');
    identityElement('identityStatus').textContent='בחר רק אם התמונה והפרטים זהים לקלף שלך.';
    identityElement('identityMore').hidden=!data.hasMoreCandidates;
  }catch(error){
    if(token!==identityRequest||!identityContext)return;
    identityElement('identityStatus').textContent=error.status===404||error.message==='לא נמצאו אפשרויות עם תמונה'?'לא נמצאה מהדורה מתאימה. בדוק את הפרטים; ייתכן שהקלף חסר בקטלוג. אפשר לשמור אותו עם הפרטים שלך ללא מחיר.':'החיפוש לא הושלם. נסה שוב בעוד רגע.';
  }
}
function selectIdentityHelp(index){
  const candidate=identityOptions[index];if(!candidate)return;
  applyHelpIdentity({...candidate,identityConfidence:'catalog',needsConfirmation:false,matchCandidates:[]});
}
function manualIdentityHelp(){
  const hints=identityHints();
  if(!hints.name||hints.language==='auto'){identityElement('identityStatus').textContent='לשמירה ידנית הוסף שם ובחר שפה. לא ייקבע מחיר אוטומטי לפרטים לא מאומתים.';return;}
  applyHelpIdentity({...hints,pokemon:hints.name,set:hints.setCode,identityConfidence:'manual',priceStatus:'unverified',value:''});
}
function applyHelpIdentity(candidate){
  const context=identityContext;if(!context)return;
  if(context.scanId){
    const scan=quickScans.find(item=>item.id===context.scanId);
    if(!scan||scan.added||scan.saving||context.session!==quickSessionToken){closeIdentityHelp();return;}
    scan.priceRevision=(scan.priceRevision||0)+1;
    scan.data={...candidate};scan.name=candidate.name||candidate.pokemon||candidate.printedName;scan.status='done';scan.value=null;scan.candidates=[];
    scan.priceStatus=candidate.identityConfidence==='manual'?'unverified':'pending';
    if(candidate.identityConfidence!=='manual')queueQuickScanPrice(scan,quickSessionToken);
    renderQuickScans();
  }else{
    if(context.editId!==editId||!identityElement('overlay').classList.contains('open')){closeIdentityHelp();return;}
    const priceToken=++editorPriceToken;aiMeta={};editorCandidateOptions=[];
    ['fName','fPokemon','fNumber','fSet','fYear','fValue'].forEach(id=>identityElement(id).value='');
    applyIdentificationToEditor(candidate);identityElement('fLanguage').value=candidate.language||'Other';
    aiMeta.identityConfidence=candidate.identityConfidence;
    const result=identityElement('aiResult');result.style.display='block';result.textContent=candidate.identityConfidence==='manual'?'הפרטים הוזנו ידנית. אפשר לשמור ללא מחיר.':'הקלף נבחר. בודק מחיר למהדורה הזו...';
    renderPreview();
    if(candidate.identityConfidence!=='manual')resolveEditorPrice(candidate,priceToken,'aiResult');
  }
  closeIdentityHelp();
}
