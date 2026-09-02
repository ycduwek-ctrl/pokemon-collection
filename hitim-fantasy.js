(function () {
  'use strict';

  const STORAGE_KEY = 'fantasyCardsV1';
  const MAX_SAVED_CARDS = 30;
  const TYPES = {
    electric: { colors: ['#2e1065', '#6d28d9', '#fbbf24'], icon: '⚡' },
    fire: { colors: ['#450a0a', '#dc2626', '#fbbf24'], icon: '🔥' },
    water: { colors: ['#082f49', '#0369a1', '#67e8f9'], icon: '💧' },
    grass: { colors: ['#052e16', '#15803d', '#bef264'], icon: '🍃' },
    psychic: { colors: ['#2e1065', '#7c3aed', '#f0abfc'], icon: '✦' },
    dark: { colors: ['#020617', '#312e81', '#a78bfa'], icon: '☾' },
    metal: { colors: ['#1e293b', '#64748b', '#e2e8f0'], icon: '◆' },
    fairy: { colors: ['#500724', '#db2777', '#fde68a'], icon: '✧' }
  };
  let photoFile = null;
  let photoDataUrl = '';
  let draft = null;
  let attempt = 0;
  let busy = false;

  function byId(id) { return document.getElementById(id); }
  function cleanText(value, fallback, maximum) {
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    return (text || fallback).slice(0, maximum);
  }
  function cleanNumber(value, fallback, minimum, maximum) {
    const parsed = Number.parseInt(value, 10);
    return Math.max(minimum, Math.min(Number.isFinite(parsed) ? parsed : fallback, maximum));
  }
  function cleanConcept(value, requestedName) {
    const data = value && typeof value === 'object' ? value : {};
    const type = TYPES[data.type] ? data.type : 'psychic';
    return {
      title: cleanText(requestedName || data.title, 'גיבור הדמיון ex', 46),
      subtitle: cleanText(data.subtitle, 'נולד מכוח הדמיון', 72),
      hp: cleanNumber(data.hp, 220, 60, 360),
      type,
      move1Name: cleanText(data.move1Name, 'כוח החברות', 30),
      move1Damage: cleanNumber(data.move1Damage, 80, 0, 320),
      move1Text: cleanText(data.move1Text, 'החברות מעניקה כוח לכל הצוות.', 92),
      move2Name: cleanText(data.move2Name, 'פרץ הדמיון', 30),
      move2Damage: cleanNumber(data.move2Damage, 160, 0, 360),
      move2Text: cleanText(data.move2Text, 'מתקפה מיוחדת שנולדה מהרעיון שלך.', 92),
      flavor: cleanText(data.flavor, 'כשמדמיינים ביחד, הכול הופך לאפשרי.', 110),
      rarity: cleanText(data.rarity, 'נדיר במיוחד', 20)
    };
  }
  function fallbackConcept(name, idea, version) {
    const typeNames = Object.keys(TYPES);
    const type = typeNames[(version - 1) % typeNames.length];
    const firstMoves = ['כוח החברות', 'קפיצת הכוכב', 'מגן האומץ', 'ברק של חיוך'];
    const secondMoves = ['פרץ הדמיון', 'סערת גיבורים', 'מכת החלום', 'אנרגיית על'];
    const index = (version - 1) % firstMoves.length;
    return cleanConcept({
      subtitle: cleanText(idea, 'הרפתקה שנולדה מהדמיון', 72),
      hp: 180 + ((version * 20) % 160), type,
      move1Name: firstMoves[index], move1Damage: 60 + version * 10,
      move1Text: 'הדמות צוברת כוח מכל חבר שנמצא לצידה.',
      move2Name: secondMoves[index], move2Damage: 130 + version * 10,
      move2Text: 'מתקפה מיוחדת שמגשימה את הרעיון שבתמונה.',
      flavor: 'קלף חד-פעמי שנוצר במיוחד בסטודיו הדמיון של Hitim.',
      rarity: version % 3 === 0 ? 'אגדי' : 'נדיר במיוחד'
    }, name);
  }

  function setStatus(message, isError) {
    const node = byId('fantasyStatus');
    if (!node) return;
    node.textContent = message || '';
    node.classList.toggle('error', Boolean(isError));
  }
  function setBusy(value) {
    busy = value;
    const button = byId('fantasyGenerateBtn');
    if (button) {
      button.disabled = value;
      button.textContent = value ? '✨ יוצר את הקלף...' : '✨ צור לי קלף';
    }
  }
  async function photoSelected(input) {
    const file = input.files && input.files[0];
    input.value = '';
    if (!file) return;
    if (!String(file.type || '').startsWith('image/')) {
      setStatus('נא לבחור קובץ תמונה.', true); return;
    }
    try {
      photoFile = file;
      photoDataUrl = await HitimDB.imageFileToDataUrl(file, { width: 1000, height: 760, quality: .88 });
      byId('fantasyPhotoPreview').src = photoDataUrl;
      byId('fantasyUpload').classList.add('has-photo');
      draft = null;
      byId('fantasyResult').hidden = true;
      setStatus('התמונה מוכנה. עכשיו כתוב מה תרצה לראות בקלף.');
    } catch (error) {
      console.error(error); setStatus('לא הצלחנו לפתוח את התמונה.', true);
    }
  }

  function roundedPath(context, x, y, width, height, radius) {
    const r = Math.min(radius, width / 2, height / 2);
    context.beginPath();
    context.moveTo(x + r, y);
    context.arcTo(x + width, y, x + width, y + height, r);
    context.arcTo(x + width, y + height, x, y + height, r);
    context.arcTo(x, y + height, x, y, r);
    context.arcTo(x, y, x + width, y, r);
    context.closePath();
  }
  function loadImage(source) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error('image decode failed'));
      image.src = source;
    });
  }
  function drawCover(context, image, x, y, width, height) {
    const sourceRatio = image.naturalWidth / image.naturalHeight;
    const targetRatio = width / height;
    let sx = 0, sy = 0, sw = image.naturalWidth, sh = image.naturalHeight;
    if (sourceRatio > targetRatio) { sw = sh * targetRatio; sx = (image.naturalWidth - sw) / 2; }
    else { sh = sw / targetRatio; sy = (image.naturalHeight - sh) / 2; }
    context.drawImage(image, sx, sy, sw, sh, x, y, width, height);
  }
  function wrapLines(context, text, maximumWidth, maximumLines) {
    const words = cleanText(text, '', 180).split(' ').filter(Boolean);
    const lines = [];
    let line = '';
    words.forEach(word => {
      const candidate = line ? `${line} ${word}` : word;
      if (context.measureText(candidate).width <= maximumWidth || !line) line = candidate;
      else { lines.push(line); line = word; }
    });
    if (line) lines.push(line);
    if (lines.length > maximumLines) {
      const clipped = lines.slice(0, maximumLines);
      clipped[maximumLines - 1] = `${clipped[maximumLines - 1].slice(0, -1)}…`;
      return clipped;
    }
    return lines;
  }
  function seededRandom(seed) {
    let state = seed * 9301 + 49297;
    return () => { state = (state * 9301 + 49297) % 233280; return state / 233280; };
  }
  function drawMove(context, y, theme, name, damage, description) {
    roundedPath(context, 42, y, 666, 126, 22);
    context.fillStyle = 'rgba(5,10,24,.72)'; context.fill();
    context.strokeStyle = `${theme.colors[2]}99`; context.lineWidth = 2; context.stroke();
    context.fillStyle = theme.colors[2]; context.font = '900 27px Heebo, sans-serif';
    context.textAlign = 'right'; context.fillText(name, 664, y + 38);
    context.textAlign = 'left'; context.font = '900 30px Heebo, sans-serif'; context.fillText(String(damage), 70, y + 39);
    context.fillStyle = '#e2e8f0'; context.font = '700 18px Heebo, sans-serif'; context.textAlign = 'right';
    wrapLines(context, description, 570, 2).forEach((line, index) => context.fillText(line, 665, y + 75 + index * 24));
  }
  async function composeCard(photo, concept, version) {
    if (document.fonts && document.fonts.ready) await document.fonts.ready;
    const image = await loadImage(photo);
    const canvas = document.createElement('canvas');
    canvas.width = 750; canvas.height = 1050;
    const context = canvas.getContext('2d', { alpha: false });
    const theme = TYPES[concept.type] || TYPES.psychic;
    const background = context.createLinearGradient(0, 0, 750, 1050);
    background.addColorStop(0, theme.colors[0]); background.addColorStop(.58, theme.colors[1]); background.addColorStop(1, '#050a18');
    context.fillStyle = background; context.fillRect(0, 0, 750, 1050);
    const random = seededRandom(version + concept.title.length);
    for (let i = 0; i < 34; i += 1) {
      const x = random() * 750, y = random() * 1050, size = 1 + random() * 4;
      context.globalAlpha = .18 + random() * .4; context.fillStyle = i % 3 ? theme.colors[2] : '#fff';
      context.beginPath(); context.arc(x, y, size, 0, Math.PI * 2); context.fill();
    }
    context.globalAlpha = 1;
    roundedPath(context, 18, 18, 714, 1014, 42);
    context.strokeStyle = theme.colors[2]; context.lineWidth = 7; context.stroke();
    roundedPath(context, 30, 30, 690, 990, 34);
    context.strokeStyle = 'rgba(255,255,255,.34)'; context.lineWidth = 2; context.stroke();

    context.fillStyle = '#fff'; context.font = '900 34px Heebo, sans-serif'; context.textAlign = 'right';
    const titleLines = wrapLines(context, concept.title, 485, 1);
    context.fillText(titleLines[0] || concept.title, 535, 80);
    context.textAlign = 'left'; context.font = '900 25px Heebo, sans-serif';
    context.fillStyle = theme.colors[2]; context.fillText(`${concept.hp} HP ${theme.icon}`, 55, 80);
    context.textAlign = 'center'; context.fillStyle = '#cbd5e1'; context.font = '700 17px Heebo, sans-serif';
    context.fillText(concept.subtitle, 375, 113);

    roundedPath(context, 42, 135, 666, 452, 28); context.save(); context.clip();
    context.filter = 'saturate(1.25) contrast(1.07)'; drawCover(context, image, 42, 135, 666, 452); context.filter = 'none';
    const wash = context.createLinearGradient(42, 135, 708, 587);
    wash.addColorStop(0, `${theme.colors[1]}33`); wash.addColorStop(.62, 'transparent'); wash.addColorStop(1, `${theme.colors[2]}44`);
    context.fillStyle = wash; context.fillRect(42, 135, 666, 452);
    context.restore();
    roundedPath(context, 42, 135, 666, 452, 28); context.strokeStyle = `${theme.colors[2]}bb`; context.lineWidth = 4; context.stroke();
    context.font = '900 48px sans-serif'; context.textAlign = 'center'; context.fillStyle = '#fff';
    context.globalAlpha = .9; context.fillText(theme.icon, 650, 550); context.globalAlpha = 1;

    drawMove(context, 608, theme, concept.move1Name, concept.move1Damage, concept.move1Text);
    drawMove(context, 748, theme, concept.move2Name, concept.move2Damage, concept.move2Text);
    context.textAlign = 'center'; context.fillStyle = '#dbeafe'; context.font = '700 17px Heebo, sans-serif';
    wrapLines(context, concept.flavor, 630, 2).forEach((line, index) => context.fillText(line, 375, 914 + index * 23));
    context.fillStyle = theme.colors[2]; context.font = '900 15px Heebo, sans-serif';
    context.fillText(`HITIM FANTASY • ${concept.rarity} • FAN CARD לא רשמי`, 375, 991);
    return canvas.toDataURL('image/webp', .9);
  }

  async function requestConcept(name, idea, version) {
    const form = new FormData();
    const compressedPhoto = await (await fetch(photoDataUrl)).blob();
    form.append('photo', compressedPhoto, photoFile && photoFile.name ? photoFile.name : 'portrait.webp');
    form.append('prompt', idea); form.append('card_name', name); form.append('attempt', String(version));
    const response = await HitimAuth.fetch('/fantasy-card/concept', { method: 'POST', body: form, timeoutMs: 46000 });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || 'שירות ה-AI אינו זמין');
    return cleanConcept(payload.concept, name);
  }
  async function generateFantasyCard() {
    if (busy) return;
    const name = cleanText(byId('fantasyCardName').value, '', 46);
    const idea = cleanText(byId('fantasyPrompt').value, '', 500);
    if (!photoDataUrl) { setStatus('קודם צריך לצלם או לבחור תמונה.', true); return; }
    if (!name || !idea) { setStatus('נא למלא שם לקלף ורעיון קצר.', true); return; }
    attempt += 1; setBusy(true); setStatus('ה-AI בונה כוחות וסיפור לפי הרעיון שלך...');
    let concept; let source = 'ai';
    try { concept = await requestConcept(name, idea, attempt); }
    catch (error) {
      console.debug('Fantasy AI fallback', error); source = 'local'; concept = fallbackConcept(name, idea, attempt);
    }
    try {
      const imageDataUrl = await composeCard(photoDataUrl, concept, attempt);
      draft = { id: '', name: concept.title, prompt: idea, concept, imageDataUrl, createdAt: new Date().toISOString(), source };
      byId('fantasyCardPreview').src = imageDataUrl;
      byId('fantasyResult').hidden = false;
      byId('fantasyRetryBtn').disabled = false;
      byId('fantasySaveBtn').textContent = '💾 שמור בגלריית AI';
      setStatus(source === 'ai' ? 'הקלף מוכן ✨ אפשר לנסות גרסה אחרת או לשמור.' : 'שירות ה-AI היה עמוס, אז נוצרה גרסת ניסיון מקומית. אפשר לנסות שוב.');
      byId('fantasyResult').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (error) {
      console.error(error); setStatus('לא הצלחנו להרכיב את הקלף. נסה תמונה אחרת.', true);
    } finally { setBusy(false); }
  }

  async function getSavedCards() {
    const value = await HitimDB.getSetting(STORAGE_KEY, []);
    return Array.isArray(value) ? value : [];
  }
  async function renderFantasyGallery() {
    const cards = await getSavedCards();
    const gallery = byId('fantasyGallery');
    byId('fantasyGalleryCount').textContent = `${cards.length} קלפים`;
    if (!cards.length) { gallery.innerHTML = '<div class="fantasy-empty">הקלפים שתשמור יופיעו כאן.</div>'; return; }
    gallery.innerHTML = cards.map(card => `<article class="fantasy-saved-card" onclick="viewFantasyCard('${escapeHtml(card.id)}')">
      <img src="${escapeHtml(card.imageDataUrl)}" loading="lazy" decoding="async" alt="${escapeHtml(card.name || 'קלף דמיון')}">
      <button class="fantasy-delete" type="button" aria-label="מחיקת קלף" onclick="event.stopPropagation();deleteFantasyCard('${escapeHtml(card.id)}')">✕</button>
      <div class="fantasy-saved-card-name">${escapeHtml(card.name || 'קלף דמיון')}</div>
    </article>`).join('');
  }
  async function saveFantasyCard() {
    if (!draft || !draft.imageDataUrl) return;
    const cards = await getSavedCards();
    if (!draft.id) draft.id = `fantasy-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    const next = [draft, ...cards.filter(card => card.id !== draft.id)].slice(0, MAX_SAVED_CARDS);
    await HitimDB.setSetting(STORAGE_KEY, next);
    byId('fantasySaveBtn').textContent = '✅ נשמר בגלריית AI';
    await renderFantasyGallery();
    toast('✨ קלף הדמיון נשמר במכשיר');
  }
  async function viewFantasyCard(id) {
    const saved = (await getSavedCards()).find(card => card.id === id);
    if (!saved) return;
    draft = saved; byId('fantasyCardPreview').src = saved.imageDataUrl;
    byId('fantasyResult').hidden = false; byId('fantasyRetryBtn').disabled = !photoDataUrl;
    byId('fantasySaveBtn').textContent = '✅ שמור בגלריית AI';
    byId('fantasyResult').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  async function deleteFantasyCard(id) {
    if (!confirm('למחוק את קלף הדמיון הזה?')) return;
    const cards = (await getSavedCards()).filter(card => card.id !== id);
    await HitimDB.setSetting(STORAGE_KEY, cards);
    if (draft && draft.id === id) { draft = null; byId('fantasyResult').hidden = true; }
    await renderFantasyGallery(); toast('הקלף נמחק מגלריית ה-AI');
  }
  function downloadFantasyCard() {
    if (!draft || !draft.imageDataUrl) return;
    const link = document.createElement('a'); link.href = draft.imageDataUrl;
    link.download = `${cleanText(draft.name, 'hitim-fantasy-card', 40).replace(/[^\p{L}\p{N}_-]+/gu, '-')}.webp`;
    document.body.appendChild(link); link.click(); link.remove();
  }
  async function shareFantasyCard() {
    if (!draft || !draft.imageDataUrl) return;
    try {
      const blob = await (await fetch(draft.imageDataUrl)).blob();
      const file = new File([blob], 'hitim-fantasy-card.webp', { type: 'image/webp' });
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        await navigator.share({ title: draft.name, text: 'קלף דמיון שיצרתי ב-Hitim', files: [file] });
      } else downloadFantasyCard();
    } catch (error) { if (error.name !== 'AbortError') toast('לא הצלחנו לשתף — אפשר להוריד את הקלף'); }
  }
  function openFantasyStudio() {
    if (window.closeNavigationViews) window.closeNavigationViews();
    if (window.setBottomNavActive) window.setBottomNavActive('studio');
    byId('fantasyStudioOverlay').classList.add('open');
    renderFantasyGallery().catch(error => console.debug('Fantasy gallery', error));
  }
  function closeFantasyStudio() {
    byId('fantasyStudioOverlay').classList.remove('open');
    if (window.setBottomNavActive) window.setBottomNavActive('gallery');
  }
  function fantasyOverlayClick(event) { if (event.target === byId('fantasyStudioOverlay')) closeFantasyStudio(); }

  Object.assign(window, {
    openFantasyStudio, closeFantasyStudio, fantasyOverlayClick, fantasyPhotoSelected: photoSelected,
    generateFantasyCard, retryFantasyCard: generateFantasyCard, saveFantasyCard, viewFantasyCard,
    deleteFantasyCard, downloadFantasyCard, shareFantasyCard
  });
})();
