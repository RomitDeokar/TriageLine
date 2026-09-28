/* Cookie authentication for the browser; no provider or application secret is stored here. */
(() => {
  let pending;
  async function login() {
    const config = await fetch('/api/auth/config', { credentials: 'same-origin' });
    if (config.status === 404) return; // local-only legacy evaluation server
    if (!config.ok) throw new Error('Authentication service is unavailable');
    const { access_code_required: required } = await config.json();
    if (!required) {
      const r = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      if (!r.ok) throw new Error('Session access unavailable; try again shortly');
      return;
    }
    return new Promise((resolve, reject) => {
      const d = document.createElement('dialog');
      d.setAttribute('aria-label', 'Sign in to TriageLine');
      d.style.cssText = 'max-width:360px;width:90%;padding:24px;border:1px solid #334155;border-radius:16px;background:#101827;color:#eef2ff';
      d.innerHTML = '<form><h2>Welcome to TriageLine</h2><p>Enter the access code provided by your operator. Never enter an AI provider key here.</p><label>Access code <input name="code" type="password" autocomplete="current-password" required style="width:100%;padding:12px;margin:12px 0"></label><p role="alert"></p><button type="submit">Continue</button> <button type="button">Cancel</button></form>';
      const finish = () => { d.close(); d.remove(); };
      d.querySelector('[type=button]').onclick = () => { finish(); reject(new Error('Sign-in cancelled. Use Reconnect to try again.')); };
      d.addEventListener('cancel', (e) => { e.preventDefault(); finish(); reject(new Error('Sign-in cancelled')); });
      d.querySelector('form').onsubmit = async (e) => {
        e.preventDefault();
        const button = d.querySelector('[type=submit]'); button.disabled = true;
        try {
          const r = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ access_code: d.querySelector('input').value }) });
          const data = await r.json();
          if (!r.ok) throw new Error(data.error || 'Sign-in failed');
          finish(); resolve();
        } catch (err) { d.querySelector('[role=alert]').textContent = err.message; }
        finally { button.disabled = false; }
      };
      document.body.appendChild(d); d.showModal(); d.querySelector('input').focus();
    });
  }
  window.TriageAuth = {
    ensure() {
      if (pending) return pending;
      pending = (async () => {
        const r = await fetch('/api/auth/me', { credentials: 'same-origin' });
        if (r.ok) return;
        await login();
      })().finally(() => { pending = null; });
      return pending;
    }
  };
})();
