(function () {
  'use strict';

  let client = null;
  let session = null;
  let access = null;
  let readyHandler = null;
  let readyUserId = null;
  let initializationPromise = null;
  const CONFIG_CACHE_KEY = 'hitim-auth-config-v1';
  const ACCESS_CACHE_KEY = 'hitim-approved-access-v1';

  function apiBase() {
    return String(window.HITIM_API_URL || '').replace(/\/$/, '');
  }

  function showGate(view, message = '') {
    const gate = document.getElementById('authGate');
    if (!gate) return;
    gate.classList.add('show');
    gate.querySelectorAll('[data-auth-view]').forEach(element => {
      element.hidden = element.dataset.authView !== view;
    });
    const messageNode = gate.querySelector(`[data-auth-message="${view}"]`);
    if (messageNode && message) messageNode.textContent = message;
    document.body.classList.add('auth-locked');
  }

  function unlockApp() {
    const gate = document.getElementById('authGate');
    if (gate) gate.classList.remove('show');
    document.body.classList.remove('auth-locked');
  }

  function readCache(key) {
    try { return JSON.parse(localStorage.getItem(key) || 'null'); }
    catch (_) { return null; }
  }

  function writeCache(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); }
    catch (_) {}
  }

  function clearCachedAccess() {
    try { localStorage.removeItem(ACCESS_CACHE_KEY); }
    catch (_) {}
  }

  async function fetchWithTimeout(url, options = {}, timeoutMs = 30000) {
    const controller = new AbortController();
    const externalSignal = options.signal;
    const abort = () => controller.abort();
    if (externalSignal) {
      if (externalSignal.aborted) controller.abort();
      else externalSignal.addEventListener('abort', abort, { once: true });
    }
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } catch (error) {
      if (error?.name === 'AbortError' && !externalSignal?.aborted) {
        const timeoutError = new Error('השרת מתעכב כרגע. נסה שוב בעוד רגע.');
        timeoutError.name = 'TimeoutError';
        throw timeoutError;
      }
      throw error;
    } finally {
      clearTimeout(timer);
      externalSignal?.removeEventListener?.('abort', abort);
    }
  }

  async function rawFetch(path, options = {}) {
    const { timeoutMs = 30000, ...fetchOptions } = options;
    const headers = new Headers(options.headers || {});
    if (session?.access_token) headers.set('Authorization', `Bearer ${session.access_token}`);
    const response = await fetchWithTimeout(
      `${apiBase()}${path}`,
      { ...fetchOptions, headers },
      timeoutMs
    );
    if (response.status === 401) {
      access = null;
      clearCachedAccess();
      showGate('login', 'החיבור פג. התחבר מחדש באמצעות Google.');
    } else if (response.status === 403 && session) {
      response.clone().json().then(payload => {
        if (String(payload?.detail || '').toLowerCase().includes('access')) resolveAccess();
      }).catch(() => {});
    }
    return response;
  }

  async function jsonFetch(path, options = {}) {
    const response = await rawFetch(path, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(payload.detail || payload.error || 'Request failed');
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  async function registerAccessRequest() {
    return jsonFetch('/access/request', { method: 'POST', timeoutMs: 20000 });
  }

  async function activateApproved(nextAccess) {
    access = nextAccess;
    const userId = access.userId || session.user.id;
    HitimDB.setNamespace(userId);
    writeCache(ACCESS_CACHE_KEY, {
      ...access,
      userId,
      sessionUserId: session.user.id,
      cachedAt: Date.now()
    });
    unlockApp();
    if (readyHandler && readyUserId !== userId) {
      readyUserId = userId;
      await readyHandler(access);
    }
    return access;
  }

  async function resolveAccess({ background = false } = {}) {
    if (!session?.user) {
      showGate('login');
      return null;
    }
    if (!background) showGate('checking');
    try {
      const nextAccess = await registerAccessRequest();
      const email = nextAccess.email || session.user.email || '';
      if (nextAccess.status === 'approved') {
        return activateApproved(nextAccess);
      }
      access = nextAccess;
      clearCachedAccess();
      if (nextAccess.status === 'blocked') {
        showGate('blocked', `הגישה של ${email} חסומה.`);
        return nextAccess;
      }
      showGate('pending', `הבקשה של ${email} ממתינה לאישור מנהל.`);
      return nextAccess;
    } catch (error) {
      if (background && access?.status === 'approved') {
        console.debug('Hitim access refresh will retry later', error);
        return access;
      }
      if (error.status === 401) return null;
      showGate('error', error.message || 'לא הצלחנו לבדוק את ההרשאה.');
      return null;
    }
  }

  async function fetchRemoteConfig() {
    const response = await fetchWithTimeout(`${apiBase()}/auth/config`, {}, 20000);
    const config = await response.json();
    if (!response.ok || !config.configured || !config.supabaseUrl || !config.supabaseAnonKey) {
      throw new Error('חיבור Google עדיין לא הוגדר');
    }
    writeCache(CONFIG_CACHE_KEY, config);
    return config;
  }

  async function init(onReady) {
    if (initializationPromise) return initializationPromise;
    readyHandler = onReady;
    initializationPromise = (async () => {
      showGate('checking');
      try {
        const cachedConfig = readCache(CONFIG_CACHE_KEY);
        const hasCachedConfig = Boolean(
          cachedConfig?.configured && cachedConfig.supabaseUrl && cachedConfig.supabaseAnonKey
        );
        const configPromise = fetchRemoteConfig().catch(error => {
          if (!hasCachedConfig) throw error;
          console.debug('Using cached Hitim auth configuration', error);
          return cachedConfig;
        });
        const config = hasCachedConfig ? cachedConfig : await configPromise;
        if (hasCachedConfig) configPromise.catch(() => {});
        if (!window.supabase?.createClient) throw new Error('Supabase client did not load');
        client = window.supabase.createClient(config.supabaseUrl, config.supabaseAnonKey, {
          auth: {
            persistSession: true,
            autoRefreshToken: true,
            detectSessionInUrl: true
          }
        });
        const result = await client.auth.getSession();
        session = result.data.session;
        client.auth.onAuthStateChange((_event, nextSession) => {
          const changedUser = session?.user?.id !== nextSession?.user?.id;
          session = nextSession;
          if (!nextSession) {
            access = null;
            clearCachedAccess();
            showGate('login');
          } else if (changedUser && !access) {
            setTimeout(resolveAccess, 0);
          }
        });
        const cachedAccess = readCache(ACCESS_CACHE_KEY);
        if (
          session?.user
          && cachedAccess?.status === 'approved'
          && cachedAccess.sessionUserId === session.user.id
        ) {
          await activateApproved(cachedAccess);
          resolveAccess({ background: true });
          return access;
        }
        return resolveAccess();
      } catch (error) {
        const view = String(error.message || '').includes('לא הוגדר') ? 'setup' : 'error';
        showGate(view, error.message || 'שגיאה בהפעלת Hitim');
        return null;
      }
    })();
    return initializationPromise;
  }

  async function signInWithGoogle() {
    if (!client) return;
    const button = document.getElementById('googleLoginBtn');
    if (button) button.disabled = true;
    const { error } = await client.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: `${location.origin}${location.pathname}` }
    });
    if (error) {
      if (button) button.disabled = false;
      showGate('error', error.message);
    }
  }

  async function signOut() {
    if (client) await client.auth.signOut();
    session = null;
    access = null;
    clearCachedAccess();
    location.reload();
  }

  async function refreshAccess() {
    if (!client) return;
    const result = await client.auth.getSession();
    session = result.data.session;
    return resolveAccess({ background: true });
  }

  async function adminListUsers() {
    return jsonFetch('/admin/users');
  }

  async function adminUpdateUser(userId, status) {
    return jsonFetch(`/admin/users/${encodeURIComponent(userId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status })
    });
  }

  async function adminRemoveUser(userId) {
    return jsonFetch(`/admin/users/${encodeURIComponent(userId)}`, { method: 'DELETE' });
  }

  window.HitimAuth = {
    init,
    signInWithGoogle,
    signOut,
    refreshAccess,
    fetch: rawFetch,
    jsonFetch,
    adminListUsers,
    adminUpdateUser,
    adminRemoveUser,
    getSession: () => session,
    getAccess: () => access,
    isAdmin: () => access?.role === 'admin'
  };
})();
