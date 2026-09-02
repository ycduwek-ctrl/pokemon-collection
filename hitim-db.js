(function () {
  'use strict';

  const DB_PREFIX = 'hitim-local-gallery';
  const DB_VERSION = 1;
  const CARD_STORE = 'cards';
  const SETTING_STORE = 'settings';
  let namespace = 'anonymous';
  let dbPromise;

  function setNamespace(value) {
    const nextNamespace = String(value || 'anonymous').replace(/[^a-zA-Z0-9_-]/g, '_');
    if (dbPromise && nextNamespace !== namespace) {
      throw new Error('Hitim storage was already opened for another user');
    }
    namespace = nextNamespace;
  }

  function requestResult(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error('IndexedDB request failed'));
    });
  }

  function transactionDone(transaction) {
    return new Promise((resolve, reject) => {
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error || new Error('IndexedDB transaction failed'));
      transaction.onabort = () => reject(transaction.error || new Error('IndexedDB transaction aborted'));
    });
  }

  function open() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      const request = indexedDB.open(`${DB_PREFIX}-${namespace}`, DB_VERSION);
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains(CARD_STORE)) {
          const store = db.createObjectStore(CARD_STORE, { keyPath: 'id' });
          store.createIndex('updatedAt', 'localUpdatedAt');
        }
        if (!db.objectStoreNames.contains(SETTING_STORE)) {
          db.createObjectStore(SETTING_STORE, { keyPath: 'key' });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error('Could not open Hitim storage'));
    });
    return dbPromise;
  }

  function cleanCard(card) {
    const clean = { ...card };
    Object.keys(clean).forEach(key => {
      if (key.startsWith('_')) delete clean[key];
    });
    clean.id = String(clean.id || `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
    clean.localUpdatedAt = new Date().toISOString();
    clean.localCreatedAt = clean.localCreatedAt || clean.localUpdatedAt;
    return clean;
  }

  async function listCards() {
    const db = await open();
    const tx = db.transaction(CARD_STORE, 'readonly');
    const cards = await requestResult(tx.objectStore(CARD_STORE).getAll());
    await transactionDone(tx);
    return cards.sort((a, b) =>
      String(b.localCreatedAt || b.id).localeCompare(String(a.localCreatedAt || a.id))
    );
  }

  async function getCard(id) {
    const db = await open();
    const tx = db.transaction(CARD_STORE, 'readonly');
    const card = await requestResult(tx.objectStore(CARD_STORE).get(String(id)));
    await transactionDone(tx);
    return card || null;
  }

  async function putCard(card) {
    const db = await open();
    const tx = db.transaction(CARD_STORE, 'readwrite');
    const clean = cleanCard(card);
    tx.objectStore(CARD_STORE).put(clean);
    await transactionDone(tx);
    return clean;
  }

  async function putCards(cards) {
    const db = await open();
    const tx = db.transaction(CARD_STORE, 'readwrite');
    const store = tx.objectStore(CARD_STORE);
    const cleaned = cards.map(cleanCard);
    cleaned.forEach(card => store.put(card));
    await transactionDone(tx);
    return cleaned;
  }

  async function deleteCard(id) {
    const db = await open();
    const tx = db.transaction(CARD_STORE, 'readwrite');
    tx.objectStore(CARD_STORE).delete(String(id));
    await transactionDone(tx);
  }

  async function clearCards() {
    const db = await open();
    const tx = db.transaction(CARD_STORE, 'readwrite');
    tx.objectStore(CARD_STORE).clear();
    await transactionDone(tx);
  }

  async function getSetting(key, fallback = null) {
    const db = await open();
    const tx = db.transaction(SETTING_STORE, 'readonly');
    const value = await requestResult(tx.objectStore(SETTING_STORE).get(key));
    await transactionDone(tx);
    return value ? value.value : fallback;
  }

  async function setSetting(key, value) {
    const db = await open();
    const tx = db.transaction(SETTING_STORE, 'readwrite');
    tx.objectStore(SETTING_STORE).put({ key, value });
    await transactionDone(tx);
  }

  function loadImage(source) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error('Image could not be decoded'));
      image.src = source;
    });
  }

  async function imageFileToDataUrl(file, options = {}) {
    if (!file) return '';
    const source = URL.createObjectURL(file);
    try {
      const image = await loadImage(source);
      const width = options.width || 700;
      const height = options.height || 980;
      const targetRatio = width / height;
      const sourceRatio = image.naturalWidth / image.naturalHeight;
      let sx = 0, sy = 0, sw = image.naturalWidth, sh = image.naturalHeight;
      if (sourceRatio > targetRatio) {
        sw = Math.round(sh * targetRatio);
        sx = Math.round((image.naturalWidth - sw) / 2);
      } else {
        sh = Math.round(sw / targetRatio);
        sy = Math.round((image.naturalHeight - sh) / 2);
      }
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      canvas.getContext('2d', { alpha: false }).drawImage(image, sx, sy, sw, sh, 0, 0, width, height);
      return canvas.toDataURL('image/webp', options.quality || 0.84);
    } finally {
      URL.revokeObjectURL(source);
    }
  }

  async function remoteImageToDataUrl(url, options = {}) {
    const value = String(url || '').trim();
    if (!value) return '';
    if (value.startsWith('data:image/')) return value;
    try {
      const response = await fetch(value, { mode: 'cors', cache: 'force-cache' });
      if (!response.ok) return '';
      const blob = await response.blob();
      return await imageFileToDataUrl(blob, options);
    } catch (_) {
      return '';
    }
  }

  async function requestPersistence() {
    if (!navigator.storage?.persist) return false;
    try {
      return await navigator.storage.persist();
    } catch (_) {
      return false;
    }
  }

  async function storageEstimate() {
    if (!navigator.storage?.estimate) return { usage: 0, quota: 0 };
    return navigator.storage.estimate();
  }

  async function exportPayload() {
    return {
      format: 'hitim-backup',
      version: 1,
      exportedAt: new Date().toISOString(),
      cards: await listCards(),
      comments: await getSetting('comments', {}),
      fantasyCards: await getSetting('fantasyCardsV1', [])
    };
  }

  async function downloadBackup() {
    const payload = await exportPayload();
    const blob = new Blob([JSON.stringify(payload)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `hitim-backup-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function importBackup(file, replace = false) {
    const payload = JSON.parse(await file.text());
    if (payload?.format !== 'hitim-backup' || !Array.isArray(payload.cards)) {
      throw new Error('Invalid Hitim backup');
    }
    if (replace) await clearCards();
    await putCards(payload.cards);
    if (payload.comments && typeof payload.comments === 'object') {
      const existing = replace ? {} : await getSetting('comments', {});
      await setSetting('comments', { ...existing, ...payload.comments });
    }
    if (Array.isArray(payload.fantasyCards)) {
      const existingFantasy = replace ? [] : await getSetting('fantasyCardsV1', []);
      const byId = new Map(
        [...payload.fantasyCards, ...existingFantasy]
          .filter(card => card && card.id)
          .map(card => [String(card.id), card])
      );
      await setSetting('fantasyCardsV1', [...byId.values()].slice(0, 30));
    }
    return payload.cards.length;
  }

  window.HitimDB = {
    setNamespace,
    open,
    listCards,
    getCard,
    putCard,
    putCards,
    deleteCard,
    clearCards,
    getSetting,
    setSetting,
    imageFileToDataUrl,
    remoteImageToDataUrl,
    requestPersistence,
    storageEstimate,
    exportPayload,
    downloadBackup,
    importBackup
  };
})();
