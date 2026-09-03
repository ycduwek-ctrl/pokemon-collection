(function () {
  'use strict';

  const STORAGE_KEY = 'fantasyCardsV1';
  const MAX_SAVED_CARDS = 30;
  const IMAGE_MODEL = 'gemini-3.1-flash-image-preview';
  const NAME_FONT = '"Gill Sans MT", "Gill Sans", "Trebuchet MS", Arial, sans-serif';
  const NUMBER_FONT = '"Almoni DL AAA", "Arial Narrow", Heebo, Arial, sans-serif';
  const TYPES = {
    electric: { colors: ['#2e1065', '#7c3aed', '#fde047'], icon: '⚡' },
    fire: { colors: ['#3f0712', '#e11d48', '#fbbf24'], icon: '🔥' },
    water: { colors: ['#082f49', '#2563eb', '#67e8f9'], icon: '💧' },
    grass: { colors: ['#052e16', '#16a34a', '#bef264'], icon: '🍃' },
    psychic: { colors: ['#2e1065', '#7c3aed', '#f0abfc'], icon: '✦' },
    dark: { colors: ['#020617', '#312e81', '#a78bfa'], icon: '☾' },
    metal: { colors: ['#0f172a', '#64748b', '#e2e8f0'], icon: '◆' },
    fairy: { colors: ['#500724', '#db2777', '#fde68a'], icon: '✧' }
  };
  const ENERGY_SYMBOL_CROPS = {
    grass: [0, 0, 126, 128], dark: [242, 0, 127, 128], fairy: [488, 0, 126, 128],
    fire: [965, 0, 127, 128], electric: [126, 193, 121, 128], water: [366, 193, 121, 128],
    psychic: [605, 193, 122, 128], metal: [846, 193, 121, 128]
  };
  const ENERGY_PARTNERS = {
    electric: 'fire', fire: 'electric', water: 'psychic', grass: 'electric',
    psychic: 'dark', dark: 'psychic', metal: 'electric', fairy: 'psychic'
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
  function ensureExTitle(value) {
    const title = cleanText(value, 'Fantasy Hero', 42).replace(/\s+ex$/i, '').trim();
    return `${title} ex`;
  }
  function cleanEnglishMove(value, fallback) {
    const move = cleanText(value, fallback, 30);
    return /[A-Za-z]/.test(move) && !/[\u0590-\u05ff]/.test(move) ? move : fallback;
  }
  function cleanConcept(value, requestedName) {
    const data = value && typeof value === 'object' ? value : {};
    const type = TYPES[data.type] ? data.type : 'psychic';
    return {
      title: ensureExTitle(requestedName || data.title),
      hp: cleanNumber(data.hp, 220, 60, 360),
      type,
      move1Name: cleanEnglishMove(data.move1Name, 'Friendship Strike'),
      move1Damage: cleanNumber(data.move1Damage, 80, 0, 320),
      move2Name: cleanEnglishMove(data.move2Name, 'Imagination Burst'),
      move2Damage: cleanNumber(data.move2Damage, 160, 0, 360)
    };
  }
  function fallbackConcept(name, version) {
    const typeNames = Object.keys(TYPES);
    const type = typeNames[(version - 1) % typeNames.length];
    const firstMoves = ['Friendship Strike', 'Hero Spark', 'Brave Shield', 'Power Dash'];
    const secondMoves = ['Imagination Burst', 'Dream Storm', 'Final Flash', 'Victory Pulse'];
    const index = (version - 1) % firstMoves.length;
    return cleanConcept({
      hp: 180 + ((version * 20) % 160), type,
      move1Name: firstMoves[index], move1Damage: 60 + version * 10,
      move2Name: secondMoves[index], move2Damage: 130 + version * 10,
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
      button.textContent = value ? '✨ יוצר תמונת AI חדשה...' : '✨ צור קלף HITIM';
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
      photoDataUrl = await HitimDB.imageFileToDataUrl(file, { width: 768, height: 1024, quality: .9 });
      byId('fantasyPhotoPreview').src = photoDataUrl;
      byId('fantasyUpload').classList.add('has-photo');
      draft = null;
      byId('fantasyResult').hidden = true;
      setStatus('תמונת המקור מוכנה. כתוב את הסצנה שה-AI ייצור ממנה.');
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
  function setFittedFont(context, text, maximumWidth, startingSize, minimumSize, family) {
    let size = startingSize;
    while (size > minimumSize) {
      context.font = `900 ${size}px ${family || 'Heebo, sans-serif'}`;
      if (context.measureText(text).width <= maximumWidth) break;
      size -= 1;
    }
    return size;
  }
  function drawEnergyIcon(context, x, y, theme, size) {
    const glow = context.createRadialGradient(x - size * .2, y - size * .25, 1, x, y, size);
    glow.addColorStop(0, '#fff7c2'); glow.addColorStop(.36, theme.colors[2]); glow.addColorStop(1, theme.colors[1]);
    context.beginPath(); context.arc(x, y, size, 0, Math.PI * 2);
    context.fillStyle = glow; context.fill();
    context.strokeStyle = 'rgba(255,255,255,.8)'; context.lineWidth = 2; context.stroke();
    context.fillStyle = '#111827'; context.font = `900 ${Math.round(size * 1.05)}px sans-serif`;
    context.textAlign = 'center'; context.textBaseline = 'middle'; context.fillText(theme.icon, x, y + 1);
    context.textBaseline = 'alphabetic';
  }
  function drawEnergySymbol(context, sprite, type, x, y, radius, theme) {
    const crop = ENERGY_SYMBOL_CROPS[type];
    if (!sprite || !crop) { drawEnergyIcon(context, x, y, theme, radius); return; }
    context.save();
    context.beginPath(); context.arc(x, y, radius, 0, Math.PI * 2); context.clip();
    context.drawImage(sprite, crop[0], crop[1], crop[2], crop[3], x - radius, y - radius, radius * 2, radius * 2);
    context.restore();
  }
  function drawPrimaryMove(context, sprite, concept, theme) {
    const y = 835;
    const symbolTypes = [concept.type, ENERGY_PARTNERS[concept.type] || 'electric'];
    symbolTypes.forEach((type, index) => drawEnergySymbol(context, sprite, type, 78 + index * 48, y, 20, theme));
    context.lineJoin = 'round'; context.textBaseline = 'alphabetic';
    context.font = `900 29px ${NAME_FONT}`; context.textAlign = 'left';
    context.strokeStyle = '#fff'; context.lineWidth = 5; context.strokeText(concept.move1Name, 158, y + 10);
    context.fillStyle = '#050505'; context.fillText(concept.move1Name, 158, y + 10);
    context.font = `900 39px ${NUMBER_FONT}`; context.textAlign = 'right';
    context.strokeStyle = '#fff'; context.lineWidth = 5; context.strokeText(`${concept.move1Damage}×`, 681, y + 12);
    context.fillStyle = '#050505'; context.fillText(`${concept.move1Damage}×`, 681, y + 12);
  }

  function imagePrompt(name, idea, version) {
    return `Create a completely new vertical 3:4 full-art fantasy collectible illustration using the person in the input photo as the facial and identity reference. Keep the person clearly recognizable, with a natural face, but transform the whole photo into one cohesive premium animated fantasy scene. Do not paste, frame, or reuse the original background. Scene requested by the user: ${idea}. Hero name: ${name}. Use dynamic cinematic lighting, vivid energy, depth, expressive action and richly detailed surroundings. Keep every important requested character fully visible and integrated naturally with the person. Compose for a full-art trading card: the face and main action remain clear in the upper two thirds, while the lower quarter is slightly calmer and darker for an information overlay. Family-friendly. Variation ${version}: make a noticeably different pose, camera angle and lighting. Output illustration only: no card frame, no text, no letters, no logos, no numbers, no watermark.`;
  }
  async function ensureImageGenerator() {
    if (!navigator.onLine) throw new Error('offline');
    if (!window.puter || !window.puter.ai || !window.puter.ai.txt2img) throw new Error('sdk_missing');
    if (window.puter.auth && window.puter.auth.isSignedIn && !window.puter.auth.isSignedIn()) {
      await window.puter.auth.signIn({ attempt_temp_user_creation: true });
    }
  }
  async function generateIllustration(name, idea, version) {
    await ensureImageGenerator();
    const image = await window.puter.ai.txt2img(imagePrompt(name, idea, version), {
      model: IMAGE_MODEL,
      provider: 'gemini',
      quality: '1K',
      ratio: { w: 3, h: 4 },
      input_images: [photoDataUrl]
    });
    const source = image && typeof image.src === 'string' ? image.src : '';
    if (!source) throw new Error('empty_image');
    return source;
  }
  function generationErrorMessage(error) {
    const code = String((error && (error.error || error.code || error.message)) || '').toLowerCase();
    if (code.includes('offline')) return 'צריך חיבור לאינטרנט כדי ליצור תמונת AI.';
    if (code.includes('sdk_missing')) return 'מחולל התמונות עדיין נטען. המתן רגע ולחץ שוב.';
    if (code.includes('popup_blocked')) return 'הטלפון חסם את חלון החיבור ל-AI. אפשר חלונות קופצים ולחץ שוב.';
    if (code.includes('auth_window_closed') || code.includes('cancel')) return 'החיבור ל-AI בוטל. בלי החיבור לא נוצרת תמונה חדשה.';
    if (code.includes('insufficient') || code.includes('402') || code.includes('credit')) return 'המכסה החינמית של יצירת התמונות הסתיימה. אפשר לנסות שוב כשהיא מתחדשת.';
    return 'מחולל התמונות לא הצליח ליצור איור חדש. נסה שוב או שנה מעט את הפרומפט.';
  }

  async function composeCard(illustration, concept, version) {
    if (document.fonts && document.fonts.ready) await document.fonts.ready;
    const [art, logo, energySprite] = await Promise.all([
      loadImage(illustration),
      loadImage('/hitim-icon-193.png').catch(() => null),
      loadImage('/hitim-energy-symbols.png').catch(() => null)
    ]);
    const canvas = document.createElement('canvas');
    canvas.width = 750; canvas.height = 1050;
    const context = canvas.getContext('2d', { alpha: false });
    context.direction = 'ltr';
    const theme = TYPES[concept.type] || TYPES.psychic;

    const frame = context.createLinearGradient(0, 0, 750, 1050);
    frame.addColorStop(0, '#fff0a3'); frame.addColorStop(.2, '#fff8bd');
    frame.addColorStop(.42, '#65ddf4'); frame.addColorStop(.62, '#b9a1ff');
    frame.addColorStop(.8, '#fff2a8'); frame.addColorStop(1, '#b996f4');
    context.fillStyle = '#050815'; context.fillRect(0, 0, 750, 1050);
    roundedPath(context, 0, 0, 750, 1050, 48); context.fillStyle = frame; context.fill();

    roundedPath(context, 29, 30, 692, 990, 18); context.save(); context.clip();
    drawCover(context, art, 29, 30, 692, 990);
    const topShade = context.createLinearGradient(0, 30, 0, 150);
    topShade.addColorStop(0, 'rgba(2,6,23,.6)'); topShade.addColorStop(1, 'rgba(2,6,23,0)');
    context.fillStyle = topShade; context.fillRect(29, 30, 692, 150);
    const lowerShade = context.createLinearGradient(0, 655, 0, 1020);
    lowerShade.addColorStop(0, 'rgba(2,6,23,0)');
    lowerShade.addColorStop(.42, 'rgba(4,8,25,.38)');
    lowerShade.addColorStop(1, 'rgba(2,6,23,.91)');
    context.fillStyle = lowerShade; context.fillRect(29, 655, 692, 365);
    context.restore();
    roundedPath(context, 29, 30, 692, 990, 18);
    context.strokeStyle = 'rgba(255,255,255,.74)'; context.lineWidth = 2; context.stroke();

    if (logo) {
      roundedPath(context, 58, 42, 55, 55, 12); context.save(); context.clip();
      context.drawImage(logo, 58, 42, 55, 55); context.restore();
    } else {
      context.fillStyle = theme.colors[2]; context.font = `900 35px ${NAME_FONT}`;
      context.textAlign = 'left'; context.fillText('H', 68, 82);
    }

    const baseTitle = concept.title.replace(/\s+ex$/i, '').trim();
    const titleLeft = 136;
    const titleSize = setFittedFont(context, baseTitle, 342, 45, 24, NAME_FONT);
    const baseWidth = context.measureText(baseTitle).width;
    context.lineJoin = 'round'; context.textAlign = 'left';
    context.strokeStyle = '#fff'; context.lineWidth = 7; context.strokeText(baseTitle, titleLeft, 90);
    context.fillStyle = '#050505'; context.fillText(baseTitle, titleLeft, 90);
    const exSize = Math.max(27, Math.round(titleSize * .78));
    context.font = `italic 900 ${exSize}px ${NAME_FONT}`;
    const exLeft = titleLeft + baseWidth + 7;
    context.strokeStyle = '#050505'; context.lineWidth = 6; context.strokeText('ex', exLeft, 90);
    const exFill = context.createLinearGradient(exLeft, 55, exLeft + 56, 91);
    exFill.addColorStop(0, theme.colors[2]); exFill.addColorStop(.55, '#f3c884'); exFill.addColorStop(1, theme.colors[1]);
    context.fillStyle = exFill; context.fillText('ex', exLeft, 90);

    context.font = `900 53px ${NUMBER_FONT}`; context.textAlign = 'right';
    context.strokeStyle = '#fff'; context.lineWidth = 7; context.strokeText(String(concept.hp), 649, 91);
    context.fillStyle = '#050505'; context.fillText(String(concept.hp), 649, 91);
    drawEnergySymbol(context, energySprite, concept.type, 682, 70, 28, theme);

    drawPrimaryMove(context, energySprite, concept, theme);

    const footerLine = context.createLinearGradient(65, 0, 686, 0);
    footerLine.addColorStop(0, '#f9cf4a'); footerLine.addColorStop(.55, '#fff'); footerLine.addColorStop(1, '#c9b7ff');
    context.fillStyle = footerLine; context.fillRect(65, 975, 621, 2);
    context.fillStyle = '#fff'; context.font = `900 11px ${NAME_FONT}`; context.textAlign = 'left';
    context.fillText(`HITIM • ${String(version).padStart(3, '0')}/999`, 65, 1005);
    context.fillStyle = '#ffd21c'; context.strokeStyle = '#6b4300'; context.lineWidth = 2;
    context.font = '900 26px sans-serif'; context.strokeText('★ ★', 178, 1008); context.fillText('★ ★', 178, 1008);
    return canvas.toDataURL('image/webp', .92);
  }

  async function requestConcept(name, idea, version) {
    const form = new FormData();
    const compressedPhoto = await (await fetch(photoDataUrl)).blob();
    form.append('photo', compressedPhoto, photoFile && photoFile.name ? photoFile.name : 'portrait.webp');
    form.append('prompt', idea); form.append('card_name', name); form.append('attempt', String(version));
    const response = await HitimAuth.fetch('/fantasy-card/concept', { method: 'POST', body: form, timeoutMs: 46000 });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || 'שירות כתיבת הקלף אינו זמין');
    return cleanConcept(payload.concept, name);
  }
  async function generateFantasyCard() {
    if (busy) return;
    const name = cleanText(byId('fantasyCardName').value, '', 46);
    const idea = cleanText(byId('fantasyPrompt').value, '', 500);
    if (!photoDataUrl) { setStatus('קודם צריך לצלם או לבחור תמונת פנים.', true); return; }
    if (!name || !idea) { setStatus('נא למלא שם לקלף ורעיון קצר.', true); return; }
    if (!window.puter || !window.puter.ai || !window.puter.ai.txt2img) {
      setStatus('מחולל התמונות עדיין נטען. המתן רגע ולחץ שוב.', true); return;
    }

    attempt += 1; setBusy(true); setStatus('יוצר איור AI חדש מהפנים ומהפרומפט — זה יכול לקחת עד דקה...');
    try {
      await ensureImageGenerator();
      const conceptPromise = requestConcept(name, idea, attempt)
        .catch(error => { console.debug('Fantasy copy fallback', error); return fallbackConcept(name, attempt); });
      const illustrationPromise = generateIllustration(name, idea, attempt);
      const [concept, illustration] = await Promise.all([conceptPromise, illustrationPromise]);
      setStatus('האיור נוצר. מרכיב עליו את קלף ה-Full Art של HITIM...');
      const imageDataUrl = await composeCard(illustration, concept, attempt);
      draft = {
        id: '', name: concept.title, prompt: idea, concept, imageDataUrl,
        createdAt: new Date().toISOString(), source: 'ai-image', style: 'hitim-full-art'
      };
      byId('fantasyCardPreview').src = imageDataUrl;
      byId('fantasyResult').hidden = false;
      byId('fantasyRetryBtn').disabled = false;
      byId('fantasySaveBtn').textContent = '💾 שמור בגלריית AI';
      setStatus('קלף ה-HITIM החדש מוכן ✨ אפשר לנסות תמונת AI אחרת או לשמור.');
      byId('fantasyResult').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (error) {
      console.error(error); setStatus(generationErrorMessage(error), true);
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
    toast('✨ קלף ה-HITIM נשמר במכשיר');
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
    link.download = `${cleanText(draft.name, 'hitim-full-art', 40).replace(/[^\p{L}\p{N}_-]+/gu, '-')}.webp`;
    document.body.appendChild(link); link.click(); link.remove();
  }
  async function shareFantasyCard() {
    if (!draft || !draft.imageDataUrl) return;
    try {
      const blob = await (await fetch(draft.imageDataUrl)).blob();
      const file = new File([blob], 'hitim-full-art.webp', { type: 'image/webp' });
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        await navigator.share({ title: draft.name, text: 'קלף Full Art שיצרתי ב-HITIM', files: [file] });
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
