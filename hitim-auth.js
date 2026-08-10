(function () {
  'use strict';

  let client = null;
  let session = null;
  let access = null;
  let readyHandler = null;
  let readyUserId = null;
  let initializationPromise = null;

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

  async function rawFetch(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (session?.access_token) headers.set('Authorization', `Bearer ${session.access_token}`);
    const response = await fetch(`${apiBase()}${path}`, { ...options, headers });
    if (response.status === 401) {
      access = null;
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
    return jsonFetch('/access/request', { method: 'POST' });
  }

  async function resolveAccess() {
    if (!session?.user) {
      showGate('login');
      return null;
    }
    showGate('checking');
    try {
      access = await registerAccessRequest();
      const email = access.email || session.user.email || '';
      if (access.status === 'approved') {
        const userId = access.userId || session.user.id;
        HitimDB.setNamespace(userId);
        unlockApp();
        if (readyHandler && readyUserId !== userId) {
          readyUserId = userId;
          await readyHandler(access);
        }
        return access;
      }
      if (access.status === 'blocked') {
        showGate('blocked', `הגישה של ${email} חסומה.`);
        return access;
      }
      showGate('pending', `הבקשה של ${email} ממתינה לאישור מנהל.`);
      return access;
    } catch (error) {
      showGate('error', error.message || 'לא הצלחנו לבדוק את ההרשאה.');
      return null;
    }
  }

  async function init(onReady) {
    if (initializationPromise) return initializationPromise;
    readyHandler = onReady;
    initializationPromise = (async () => {
      showGate('checking');
      try {
        const response = await fetch(`${apiBase()}/auth/config`);
        const config = await response.json();
        if (!response.ok || !config.configured || !config.supabaseUrl || !config.supabaseAnonKey) {
          showGate('setup', 'חיבור Google עדיין לא הוגדר. האתר הפעיל לא הושפע.');
          return null;
        }
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
            showGate('login');
          } else if (changedUser && !access) {
            setTimeout(resolveAccess, 0);
          }
        });
        return resolveAccess();
      } catch (error) {
        showGate('error', error.message || 'שגיאה בהפעלת Hitim');
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
    location.reload();
  }

  async function refreshAccess() {
    if (!client) return;
    const result = await client.auth.getSession();
    session = result.data.session;
    return resolveAccess();
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
