/* The app-switcher bar. Loaded by all three apps from <script src="/shell.js">.
 *
 * Reps used to keep three bookmarks and three passwords. This bar is what
 * replaces the toggling: one tap moves between Canvass, Pipeline, and
 * Estimate with the session intact, because after the merge they are all the
 * same origin and the same cookie.
 *
 * Kept dependency-free and defensive on purpose — it runs inside three
 * different apps with three different frameworks-worth of global state, so it
 * touches nothing outside #p1-shell and never throws into the host app.
 */
(function () {
  'use strict';

  if (window.__p1ShellLoaded) return;
  window.__p1ShellLoaded = true;

  var SHELL_H = 44;

  // Flagged immediately rather than after /api/me resolves: each app scopes
  // its header offset to this class, and waiting would paint the app once at
  // the wrong offset and then jump.
  //
  // The value must stay a calc() including the safe-area inset, and must match
  // the :root default in shell.css. This is an inline style on <html>, so it
  // beats the stylesheet — setting a bare '44px' here is what previously
  // pinned every app's offset to 44 while the bar itself rendered taller than
  // that on notched phones.
  document.documentElement.style.setProperty(
    '--p1-shell-h', 'calc(' + SHELL_H + 'px + env(safe-area-inset-top, 0px))');
  document.documentElement.classList.add('p1-has-shell');

  // Fallback list, used if /api/me is unreachable (offline, expired session
  // mid-render). Must stay in sync with portal/mounts.py.
  var FALLBACK_APPS = [
    { key: 'canvass',  prefix: '/canvass',  label: 'Canvass',  icon: '📍', accent: '#10B981' },
    { key: 'crm',      prefix: '/crm',      label: 'Pipeline', icon: '📋', accent: '#F97316' },
    { key: 'estimate', prefix: '/estimate', label: 'Estimate', icon: '🏠', accent: '#00A8B5' }
  ];

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function activeKey(apps) {
    var path = window.location.pathname;
    for (var i = 0; i < apps.length; i++) {
      var p = apps[i].prefix;
      if (path === p || path.indexOf(p + '/') === 0) return apps[i].key;
    }
    return null;
  }

  function render(me) {
    // Admins get Nimbus in the switcher bar too. Reps get an empty
    // admin_apps list so they never see it, matching the launcher grid.
    var baseApps = (me && me.apps && me.apps.length) ? me.apps : FALLBACK_APPS;
    var apps = baseApps.concat((me && me.admin_apps) || []);
    var current = activeKey(apps);

    var bar = el('div');
    bar.id = 'p1-shell';

    var home = el('a', 'p1-home', 'P1');
    home.href = '/';
    home.title = 'All tools';
    bar.appendChild(home);

    var list = el('div', 'p1-apps');
    apps.forEach(function (a) {
      var link = el('a', 'p1-app' + (a.key === current ? ' active' : ''));
      link.href = a.prefix + '/';
      link.style.setProperty('--p1-accent', a.accent || 'transparent');
      link.appendChild(el('span', 'p1-icon', a.icon || ''));
      link.appendChild(el('span', 'p1-label', a.label));
      list.appendChild(link);
    });
    bar.appendChild(list);

    var right = el('div', 'p1-right');
    if (me && me.username) {
      right.appendChild(el('span', 'p1-who', me.full_name || me.username));
    }
    var out = el('a', 'p1-out', 'Sign out');
    out.href = '/logout';
    right.appendChild(out);
    bar.appendChild(right);

    document.body.insertBefore(bar, document.body.firstChild);
  }

  function mount() {
    // The stylesheet is injected rather than required in each app's <head>,
    // so adding the shell to an app is a one-line <script> change.
    if (!document.querySelector('link[data-p1-shell]')) {
      var css = document.createElement('link');
      css.rel = 'stylesheet';
      css.href = '/shell.css';
      css.setAttribute('data-p1-shell', '');
      document.head.appendChild(css);
    }

    fetch('/api/me', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (me) {
        if (me && me.authenticated === false) { window.location = '/login'; return; }
        render(me);
      })
      .catch(function () { render(null); });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
