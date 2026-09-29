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
      d.className = 'tl-auth';
      d.innerHTML = '<form class="tl-auth-form"><p class="tl-auth-kicker">TriageLine</p><h2>Enter your access code</h2>'
        + '<p class="tl-auth-copy">Your operator gave you this code. It is not an AI provider key.</p>'
        + '<input name="code" type="password" autocomplete="current-password" required aria-label="Access code" placeholder="Access code">'
        + '<p class="tl-auth-err" role="alert"></p><div class="tl-auth-row"><button type="button">Cancel</button><button type="submit">Continue</button></div></form>';
      if (!document.getElementById('tl-auth-style')) {
        const st = document.createElement('style'); st.id = 'tl-auth-style';
        st.textContent = 'dialog.tl-auth{max-width:380px;width:90%;padding:28px;border:0;border-radius:22px;background:#211e1b;color:#efe8dc;font-family:"Inter Tight",system-ui,sans-serif;box-shadow:0 30px 70px rgba(0,0,0,.6)}'
          + 'dialog.tl-auth::backdrop{background:rgba(10,9,8,.72)}.tl-auth-kicker{margin:0 0 14px;font:500 11px "IBM Plex Mono",monospace;color:#e0714a}'
          + '.tl-auth h2{margin:0 0 8px;font:400 26px/1.1 Fraunces,Georgia,serif;letter-spacing:-.01em}.tl-auth-copy{margin:0 0 20px;color:#968d80;font-size:14px;line-height:1.5}'
          + '.tl-auth input{width:100%;font-family:inherit;font-size:16px;padding:13px 16px;border-radius:12px;border:1px solid #413b35;background:#181614;color:inherit;outline:none}'
          + '.tl-auth input:focus{border-color:#e0714a}.tl-auth-err{min-height:1.2em;margin:10px 0;color:#d9695f;font-size:13px}'
          + '.tl-auth-row{display:flex;justify-content:flex-end;gap:10px}.tl-auth-row button{font-family:inherit;font-size:14px;font-weight:600;min-height:44px;padding:0 20px;border-radius:999px;border:1px solid #413b35;background:none;color:#efe8dc;cursor:pointer}'
          + '.tl-auth-row button[type=submit]{background:#e0714a;border-color:#e0714a;color:#1a0f0a}.tl-auth-row button:disabled{opacity:.5}';
        document.head.appendChild(st);
      }
      const finish = () => { d.close(); d.remove(); };
      d.querySelector('[type=button]').onclick = () => { finish(); reject(new Error('Sign-in cancelled. Use Reconnect to try again.')); };
      d.addEventListener('cancel', (e) => { e.preventDefault(); finish(); reject(new Error('Sign-in cancelled')); });
      d.querySelector('form').onsubmit = async (e) => {
        e.preventDefault();
        const button = d.querySelector('[type=submit]'); button.disabled = true;
        try {
          const r = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ access_code: d.querySelector('input').value }) });
          const data = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(r.status === 429 ? 'Too many attempts. Wait a minute and try again.'
            : (data.error || 'Sign-in failed'));
          // The server accepted the code; confirm the browser actually kept the session cookie
          // (a Secure cookie is dropped on plain http://, which otherwise looks like a wrong code).
          const check = await fetch('/api/auth/me', { credentials: 'same-origin' });
          if (!check.ok) throw new Error('Code accepted, but the browser did not keep the session cookie. '
            + 'Open the site over https:// (or localhost), and allow cookies for this site.');
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
