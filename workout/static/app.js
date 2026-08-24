/* P1 Lift — the whole front end. Vanilla, no build step, no dependencies.
 *
 * Same shape as the other three apps in this repo: derive the mount prefix from
 * the URL, put every request through one api() helper, keep state in one object
 * and re-render from it.
 */
(function () {
  'use strict';

  // '' standalone, '/whatever' if this is ever mounted behind a prefix. Derived
  // rather than hardcoded so the same bundle works either way — the trap the
  // other three apps' index.html files fall into.
  var BASE = location.pathname.replace(/\/index\.html$/, '').replace(/\/+$/, '');

  var S = {
    tab: 'lift',
    workout: null,      // the active session, or null
    exercises: [],      // the library
    records: {},        // exercise_id -> record, for PR detection
    last: {},           // exercise_id -> previous session's sets
    unit: 'lb',
    restEnd: 0,
    restTotal: 0
  };

  // ── plumbing ──────────────────────────────────────────────────────────────

  function api(path, opts) {
    opts = opts || {};
    if (opts.body && typeof opts.body !== 'string') {
      opts.body = JSON.stringify(opts.body);
      opts.headers = { 'Content-Type': 'application/json' };
    }
    return fetch(BASE + path, opts).then(function (r) {
      // The session expired, or was never there. This app owns its own login,
      // so the redirect goes through BASE like every other path here.
      if (r.status === 401) {
        location.href = BASE + '/login';
        throw new Error('signed out');
      }
      // WORKOUT_PASSWORD is missing on the server. Reloading shows the notice
      // that says so, rather than leaving the app looking merely broken.
      if (r.status === 503) {
        location.reload();
        throw new Error('not configured');
      }
      if (!r.ok) return r.json().catch(function () { return {}; })
                    .then(function (e) { throw new Error(e.error || r.status); });
      return r.status === 204 ? null : r.json();
    });
  }

  function $(id) { return document.getElementById(id); }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  var toastTimer;
  function toast(msg) {
    var t = $('toast') || el('div');
    t.id = 'toast';
    t.textContent = msg;
    document.body.appendChild(t);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.remove(); }, 2600);
  }

  // The lifter's own calendar date, sent to the server on create. Streaks and
  // "this week" are questions about the day you would say you trained on, and a
  // 7pm Sunday session is already Monday in UTC.
  function localDate() {
    var d = new Date();
    return d.getFullYear() + '-' +
           String(d.getMonth() + 1).padStart(2, '0') + '-' +
           String(d.getDate()).padStart(2, '0');
  }

  function fmtDate(iso) {
    if (!iso) return '';
    var p = iso.split('-');
    var d = new Date(+p[0], +p[1] - 1, +p[2]);   // local, not UTC-parsed
    var days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    var mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    var today = localDate();
    if (iso === today) return 'Today';
    var y = new Date(); y.setDate(y.getDate() - 1);
    if (iso === y.getFullYear() + '-' + String(y.getMonth() + 1).padStart(2, '0') +
                '-' + String(y.getDate()).padStart(2, '0')) return 'Yesterday';
    return days[d.getDay()] + ' ' + mon[d.getMonth()] + ' ' + d.getDate();
  }

  function fmtVol(n) {
    n = Number(n) || 0;
    if (n >= 100000) return Math.round(n / 1000) + 'k';
    if (n >= 10000) return (n / 1000).toFixed(1) + 'k';
    return Math.round(n).toLocaleString();
  }

  function clock(secs) {
    secs = Math.max(0, Math.round(secs));
    return Math.floor(secs / 60) + ':' + String(secs % 60).padStart(2, '0');
  }

  // ── rest timer ────────────────────────────────────────────────────────────

  var REST_DEFAULT = 90;

  function startRest(secs) {
    S.restTotal = secs;
    S.restEnd = Date.now() + secs * 1000;
    $('rest').classList.remove('hidden');
    // The bar is fixed above the tab bar, so it lands squarely on the Finish
    // button unless the page makes room for it.
    document.body.classList.add('resting');
    drawRest();
  }

  function stopRest() {
    S.restEnd = 0;
    $('rest').classList.add('hidden');
    document.body.classList.remove('resting');
  }

  function drawRest() {
    if (!S.restEnd) return;
    var left = (S.restEnd - Date.now()) / 1000;
    $('rest-t').textContent = clock(left);
    $('rest-bar').style.width = Math.max(0, Math.min(100, left / S.restTotal * 100)) + '%';
    if (left <= 0) { restDone(); }
  }

  function restDone() {
    stopRest();
    if (navigator.vibrate) navigator.vibrate([180, 90, 180]);
    // A short synthesized tone rather than an audio file: no asset to ship, no
    // request to make, and it works from the offline shell.
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      var ctx = new Ctx(), osc = ctx.createOscillator(), gain = ctx.createGain();
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.0001, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.45);
      osc.connect(gain); gain.connect(ctx.destination);
      osc.start(); osc.stop(ctx.currentTime + 0.5);
    } catch (e) { /* a silent timer still counts down */ }
  }

  // ── tabs ──────────────────────────────────────────────────────────────────

  function showTab(name) {
    S.tab = name;
    ['lift', 'history', 'records'].forEach(function (t) {
      $('tab-' + t).classList.toggle('hidden', t !== name);
    });
    Array.prototype.forEach.call(document.querySelectorAll('nav button'), function (b) {
      b.classList.toggle('on', b.dataset.tab === name);
    });
    if (name === 'history') loadHistory();
    if (name === 'records') loadRecords();
  }

  // ── the active workout ────────────────────────────────────────────────────

  function loadActive() {
    return api('/api/workouts/active').then(function (w) {
      S.workout = w;
      if (w) {
        // Previous numbers for every movement already in the session, fetched
        // in parallel — this is the line the lifter actually reads.
        return Promise.all(w.exercise_list.map(function (g) {
          return loadLast(g.exercise_id);
        })).then(renderLift);
      }
      renderLift();
      return loadIdle();
    });
  }

  function loadLast(exId) {
    var q = '?before=' + (S.workout ? S.workout.id : 0);
    return api('/api/exercises/' + exId + '/last' + q).then(function (r) {
      S.last[exId] = r;
    }).catch(function () { S.last[exId] = null; });
  }

  function loadIdle() {
    return Promise.all([
      api('/api/routines'),
      api('/api/workouts?limit=1')
    ]).then(function (res) {
      var routines = res[0], recent = res[1];
      var box = $('routine-list');
      box.innerHTML = '';
      if (routines.length) {
        box.appendChild(el('div', 'small muted', 'Or run a routine'));
        routines.forEach(function (r) {
          var b = el('button', 'btn btn-wide', r.name + '  ·  ' + r.items.length + ' movements');
          b.style.marginTop = '7px';
          b.onclick = function () { startWorkout({ routine_id: r.id }); };
          box.appendChild(b);
        });
      }
      var lastBox = $('last-session');
      lastBox.innerHTML = '';
      if (!recent.length) {
        lastBox.className = 'card empty';
        lastBox.innerHTML = '<span class="big">🏋️</span>No workouts yet. ' +
          'Start one and log your first set — everything else builds off that.';
      } else {
        var w = recent[0];
        lastBox.className = 'card';
        lastBox.appendChild(el('h2', null, 'Last session'));
        lastBox.appendChild(el('div', null, (w.name || 'Workout') + ' · ' + fmtDate(w.local_date)));
        lastBox.appendChild(el('div', 'small muted',
          w.exercises + ' movements · ' + w.sets + ' sets · ' +
          fmtVol(w.volume) + ' ' + S.unit + ' volume' +
          (w.duration_min ? ' · ' + w.duration_min + ' min' : '')));
      }
    });
  }

  function startWorkout(opts) {
    var body = { local_date: localDate() };
    if (opts && opts.routine_id) body.routine_id = opts.routine_id;
    api('/api/workouts', { method: 'POST', body: body }).then(function (w) {
      S.workout = w;
      return Promise.all(w.exercise_list.map(function (g) {
        return loadLast(g.exercise_id);
      }));
    }).then(renderLift).catch(function (e) { toast(e.message); });
  }

  function renderLift() {
    var active = !!S.workout;
    $('lift-active').classList.toggle('hidden', !active);
    $('lift-idle').classList.toggle('hidden', active);
    if (!active) return;

    var w = S.workout;
    if ($('w-name').value !== w.name) $('w-name').value = w.name || '';
    $('w-sets').textContent = w.sets;
    $('w-volume').textContent = fmtVol(w.volume);
    tickElapsed();

    var box = $('exercises');
    box.innerHTML = '';
    w.exercise_list.forEach(function (g) { box.appendChild(exerciseCard(g)); });
    if (!w.exercise_list.length) {
      var e = el('div', 'empty');
      e.innerHTML = '<span class="big">➕</span>Add your first movement.';
      box.appendChild(e);
    }
  }

  function exerciseCard(g) {
    var card = el('div', 'card ex');
    var head = el('div', 'ex-head');
    head.appendChild(el('h3', null, g.name));
    var pr = S.records[g.exercise_id];
    if (pr && pr.best_e1rm) {
      head.appendChild(el('span', 'pill', 'PR ' + pr.best_e1rm + ' ' + S.unit));
    }
    card.appendChild(head);

    var last = S.last[g.exercise_id];
    var lastLine = el('div', 'ex-last');
    if (last && last.sets.length) {
      var work = last.sets.filter(function (s) { return s.set_type === 'work'; });
      lastLine.innerHTML = 'Last (' + fmtDate(last.local_date) + '): <b>' +
        work.map(function (s) { return s.weight + '×' + s.reps; }).join(', ') + '</b>';
    } else {
      lastLine.textContent = 'First time logging this one.';
    }
    card.appendChild(lastLine);

    var labels = el('div', 'set-labels');
    ['', S.unit, 'reps', 'rpe', ''].forEach(function (t) {
      labels.appendChild(el('div', null, t));
    });
    card.appendChild(labels);

    var n = 0;
    g.sets.forEach(function (s) {
      if (s.set_type === 'work') n++;
      card.appendChild(setRow(s, s.set_type === 'warmup' ? 'W' : String(n), g));
    });

    var add = el('button', 'btn btn-sm', '+ Set');
    add.onclick = function () { addSet(g); };
    var warm = el('button', 'btn btn-sm', '+ Warm-up');
    warm.onclick = function () { addSet(g, 'warmup'); };
    var row = el('div', 'row');
    row.appendChild(add); row.appendChild(warm);
    card.appendChild(row);
    return card;
  }

  function setRow(s, label, g) {
    var row = el('div', 'set' + (s.set_type === 'warmup' ? ' warmup' : ''));
    row.appendChild(el('div', 'idx', label));

    function input(field, value, placeholder) {
      var i = el('input');
      // inputMode over type=number: it brings up the numeric keypad without
      // the spinner arrows, and without Safari silently discarding a value it
      // considers malformed while the lifter is still typing it.
      i.setAttribute('inputmode', 'decimal');
      i.value = value ? String(value) : '';
      i.placeholder = placeholder;
      // 'change', not 'input' — one request per finished edit rather than one
      // per keystroke.
      i.onchange = function () {
        var body = {};
        body[field] = i.value === '' ? 0 : Number(i.value);
        api('/api/sets/' + s.id, { method: 'PATCH', body: body })
          .then(function (updated) {
            var wasEmpty = !s.reps;
            s.weight = updated.weight; s.reps = updated.reps; s.rpe = updated.rpe;
            refreshTotals();
            // A set becomes "logged" the moment it has reps. That is when the
            // rest starts — not when the weight is typed, which happens first.
            if (field === 'reps' && updated.reps > 0 && wasEmpty) {
              startRest(REST_DEFAULT);
              checkPR(g, updated);
            }
          }).catch(function (e) { toast(e.message); });
      };
      return i;
    }

    row.appendChild(input('weight', s.weight, '0'));
    row.appendChild(input('reps', s.reps, '0'));
    row.appendChild(input('rpe', s.rpe, '–'));

    var kill = el('button', 'kill', '×');
    kill.onclick = function () {
      api('/api/sets/' + s.id, { method: 'DELETE' })
        .then(reloadWorkout).catch(function (e) { toast(e.message); });
    };
    row.appendChild(kill);
    return row;
  }

  function addSet(g, type) {
    // Pre-filled from the last set of this movement in this session: sets come
    // in threes and fives at the same weight, and re-typing 185 four times is
    // how logging stops happening halfway through a session.
    var prev = g.sets.filter(function (s) {
      return s.set_type === (type || 'work');
    }).slice(-1)[0];
    var seed = prev || (S.last[g.exercise_id] && S.last[g.exercise_id].sets.slice(-1)[0]);
    api('/api/workouts/' + S.workout.id + '/sets', {
      method: 'POST',
      body: {
        exercise_id: g.exercise_id,
        weight: seed ? seed.weight : 0,
        reps: 0,                       // never pre-fill reps: that is the claim
        set_type: type || 'work'
      }
    }).then(reloadWorkout).catch(function (e) { toast(e.message); });
  }

  function checkPR(g, s) {
    var rec = S.records[g.exercise_id];
    if (!rec || !s.e1rm) return;
    if (s.e1rm > rec.best_e1rm) {
      rec.best_e1rm = s.e1rm;
      toast('🏆 PR on ' + g.name + ' — ' + s.e1rm + ' ' + S.unit + ' e1RM');
      if (navigator.vibrate) navigator.vibrate(60);
    }
  }

  function refreshTotals() {
    return api('/api/workouts/' + S.workout.id).then(function (w) {
      S.workout = w;
      $('w-sets').textContent = w.sets;
      $('w-volume').textContent = fmtVol(w.volume);
    });
  }

  function reloadWorkout() {
    return api('/api/workouts/' + S.workout.id).then(function (w) {
      S.workout = w;
      var missing = w.exercise_list.filter(function (g) {
        return !(g.exercise_id in S.last);
      });
      return Promise.all(missing.map(function (g) { return loadLast(g.exercise_id); }));
    }).then(renderLift);
  }

  function tickElapsed() {
    if (!S.workout) return;
    var started = Date.parse(S.workout.started_at);
    if (!started) return;
    $('w-elapsed').textContent = clock((Date.now() - started) / 1000 | 0)
      .replace(/^(\d+):/, function (m, mins) {
        return mins >= 60 ? Math.floor(mins / 60) + 'h' +
               String(mins % 60).padStart(2, '0') + ':' : m;
      });
  }

  // ── exercise picker ───────────────────────────────────────────────────────

  function openPicker() {
    $('sheet').classList.remove('hidden');
    $('sheet-search').value = '';
    $('sheet-search').focus();
    drawPicker('');
  }

  function drawPicker(q) {
    var list = $('sheet-list');
    list.innerHTML = '';
    var ql = q.toLowerCase();
    var matches = S.exercises.filter(function (e) {
      return !ql || e.name.toLowerCase().indexOf(ql) >= 0 ||
             (e.muscle || '').toLowerCase().indexOf(ql) >= 0;
    });
    matches.slice(0, 60).forEach(function (e) {
      var b = el('button', 'item');
      b.appendChild(document.createTextNode(e.name));
      var s = el('small', null, [e.muscle, e.equipment].filter(Boolean).join(' · '));
      b.appendChild(s);
      b.onclick = function () { pick(e); };
      list.appendChild(b);
    });
    var exact = matches.some(function (e) { return e.name.toLowerCase() === ql; });
    var create = $('sheet-create');
    create.classList.toggle('hidden', !q || exact);
    create.textContent = 'Create "' + q + '"';
    create.onclick = function () {
      api('/api/exercises', { method: 'POST', body: { name: q } })
        .then(function (ex) {
          return api('/api/exercises').then(function (all) {
            S.exercises = all;
            pick(ex);
          });
        }).catch(function (e) { toast(e.message); });
    };
  }

  function pick(ex) {
    $('sheet').classList.add('hidden');
    // An exercise with no sets has nothing to store, so adding one writes an
    // empty set as its placeholder. Finishing the workout deletes any that are
    // still empty, so a movement you thought about and skipped leaves no trace.
    api('/api/workouts/' + S.workout.id + '/sets', {
      method: 'POST', body: { exercise_id: ex.id, weight: 0, reps: 0 }
    }).then(function () {
      return loadLast(ex.id);
    }).then(reloadWorkout).catch(function (e) { toast(e.message); });
  }

  // ── history ───────────────────────────────────────────────────────────────

  function loadHistory() {
    api('/api/workouts?limit=60').then(function (list) {
      var box = $('history');
      box.innerHTML = '';
      if (!list.length) {
        box.className = 'empty';
        box.innerHTML = '<span class="big">📅</span>Nothing logged yet.';
        return;
      }
      box.className = '';
      list.forEach(function (w) { box.appendChild(historyRow(w)); });
    });
  }

  function historyRow(w) {
    var row = el('div', 'hist');
    var head = el('div', 'spread');
    var left = el('div');
    left.appendChild(el('h3', null, (w.name || 'Workout') +
      (w.active ? ' · in progress' : '')));
    left.appendChild(el('div', 'meta', fmtDate(w.local_date) + ' · ' +
      w.exercises + ' movements · ' + w.sets + ' sets · ' +
      fmtVol(w.volume) + ' ' + S.unit +
      (w.duration_min ? ' · ' + w.duration_min + ' min' : '')));
    head.appendChild(left);
    var open = el('button', 'btn btn-sm', 'View');
    head.appendChild(open);
    row.appendChild(head);

    var detail = el('div', 'hist-detail hidden');
    row.appendChild(detail);
    open.onclick = function () {
      if (!detail.classList.contains('hidden')) {
        detail.classList.add('hidden'); open.textContent = 'View'; return;
      }
      open.textContent = 'Hide';
      detail.classList.remove('hidden');
      detail.innerHTML = 'Loading…';
      api('/api/workouts/' + w.id).then(function (full) {
        detail.innerHTML = '';
        full.exercise_list.forEach(function (g) {
          var line = el('div', 'line');
          line.innerHTML = g.name + ' — <b>' + g.sets.filter(function (s) {
            return s.set_type === 'work';
          }).map(function (s) { return s.weight + '×' + s.reps; }).join(', ') + '</b>';
          detail.appendChild(line);
        });
        if (full.notes) detail.appendChild(el('div', 'line muted', full.notes));
        var actions = el('div', 'row');
        actions.style.marginTop = '8px';
        var save = el('button', 'btn btn-sm', 'Save as routine');
        save.onclick = function () {
          var name = prompt('Routine name', full.name || 'Routine');
          if (!name) return;
          api('/api/routines', { method: 'POST', body: {
            name: name, from_workout_id: full.id } })
            .then(function () { toast('Routine saved'); })
            .catch(function (e) { toast(e.message); });
        };
        var del = el('button', 'btn btn-sm btn-danger', 'Delete');
        del.onclick = function () {
          if (!confirm('Delete this workout? The sets go with it.')) return;
          api('/api/workouts/' + full.id, { method: 'DELETE' }).then(function () {
            if (S.workout && S.workout.id === full.id) { S.workout = null; renderLift(); }
            loadHistory();
          });
        };
        actions.appendChild(save); actions.appendChild(del);
        detail.appendChild(actions);
      });
    };
    return row;
  }

  // ── records & stats ───────────────────────────────────────────────────────

  function loadRecords() {
    return Promise.all([api('/api/records'), api('/api/stats?today=' + localDate())])
      .then(function (res) {
        var recs = res[0], stats = res[1];
        S.records = {};
        recs.forEach(function (r) { S.records[r.exercise_id] = r; });

        $('hdr-stat').innerHTML = stats.this_week.workouts + ' this week' +
          (stats.week_streak > 1 ? ' · <b>' + stats.week_streak + 'wk</b> streak' : '');

        var tiles = $('stat-tiles');
        tiles.innerHTML = '';
        [['Workouts', stats.total_workouts],
         ['Week streak', stats.week_streak],
         ['This week', stats.this_week.workouts],
         ['Total volume', fmtVol(stats.total_volume)]].forEach(function (t) {
          var d = el('div', null, t[0]);
          d.appendChild(el('b', null, String(t[1])));
          tiles.appendChild(d);
        });

        var note = $('stat-note');
        note.classList.toggle('hidden', !S.workout);
        note.textContent = 'Today\u2019s session is still open — it counts here ' +
          'once you finish it.';

        var spark = $('spark'), xs = $('spark-x');
        spark.innerHTML = ''; xs.innerHTML = '';
        var max = Math.max.apply(null, stats.weeks.map(function (w) {
          return w.volume; }).concat([1]));
        stats.weeks.forEach(function (w, i) {
          var bar = el('div');
          bar.style.height = Math.max(3, w.volume / max * 72) + 'px';
          bar.title = w.week + ' · ' + fmtVol(w.volume) + ' ' + S.unit;
          if (i === stats.weeks.length - 1) bar.className = 'now';
          spark.appendChild(bar);
          xs.appendChild(el('div', null, w.week.slice(5).replace('-', '/')));
        });
        if (!stats.weeks.length) {
          spark.style.display = 'none';
          xs.appendChild(el('div', null, 'Nothing finished yet.'));
        } else {
          spark.style.display = '';
        }

        var box = $('records');
        box.innerHTML = '';
        if (!recs.length) {
          box.className = 'empty';
          box.innerHTML = '<span class="big">🏆</span>Log a few sets and your ' +
            'records show up here.';
          return;
        }
        box.className = '';
        recs.forEach(function (r) {
          var row = el('div', 'rec');
          var n = el('div', 'n', r.name);
          n.appendChild(el('small', null, 'best ' + r.top_weight + ' ' + S.unit +
            ' × ' + r.top_weight_reps + ' · ' + fmtDate(r.top_weight_date)));
          var v = el('div', 'v');
          v.appendChild(el('b', null, r.best_e1rm + ''));
          v.appendChild(el('small', null, 'est. 1RM ' + S.unit));
          row.appendChild(n); row.appendChild(v);
          box.appendChild(row);
        });
      });
  }

  // ── wiring ────────────────────────────────────────────────────────────────

  function bind() {
    Array.prototype.forEach.call(document.querySelectorAll('nav button'), function (b) {
      b.onclick = function () { showTab(b.dataset.tab); };
    });
    $('start-empty').onclick = function () { startWorkout(); };
    $('add-exercise').onclick = openPicker;
    $('sheet-close').onclick = function () { $('sheet').classList.add('hidden'); };
    $('sheet').onclick = function (e) {
      if (e.target === $('sheet')) $('sheet').classList.add('hidden');
    };
    $('sheet-search').oninput = function () { drawPicker(this.value.trim()); };
    $('w-name').onchange = function () {
      api('/api/workouts/' + S.workout.id, {
        method: 'PATCH', body: { name: this.value } });
    };
    $('finish').onclick = function () {
      if (!S.workout.sets) {
        if (!confirm('Nothing logged. Finish anyway?')) return;
      }
      api('/api/workouts/' + S.workout.id, {
        method: 'PATCH', body: { finish: true } }).then(function (w) {
        toast('Done — ' + w.sets + ' sets, ' + fmtVol(w.volume) + ' ' + S.unit);
        S.workout = null; S.last = {};
        stopRest();
        loadRecords();
        return loadActive();
      }).catch(function (e) { toast(e.message); });
    };
    $('scrap').onclick = function () {
      if (!confirm('Scrap this workout? Everything logged in it is deleted.')) return;
      api('/api/workouts/' + S.workout.id, { method: 'DELETE' }).then(function () {
        S.workout = null; S.last = {}; stopRest(); loadActive();
      });
    };
    $('rest-add').onclick = function () { S.restEnd += 30000; S.restTotal += 30; };
    $('rest-skip').onclick = stopRest;
    $('unit-toggle').onclick = function () {
      var next = S.unit === 'lb' ? 'kg' : 'lb';
      api('/api/settings', { method: 'PATCH', body: { unit: next } })
        .then(function (r) {
          S.unit = r.unit;
          $('unit-toggle').textContent = 'Unit: ' + S.unit;
          toast('Showing ' + S.unit + '. Stored weights are unchanged.');
          renderLift(); loadRecords();
        });
    };

    // One second tick drives both the elapsed clock and the rest countdown.
    setInterval(function () {
      if (S.workout) tickElapsed();
      drawRest();
    }, 1000);
  }

  // ── boot ──────────────────────────────────────────────────────────────────

  bind();
  Promise.all([
    api('/api/exercises').then(function (r) { S.exercises = r; }),
    api('/api/settings').then(function (r) { S.unit = r.unit || 'lb'; })
  ]).then(function () {
    $('unit-toggle').textContent = 'Unit: ' + S.unit;
    return loadRecords();           // records first: PR pills need them
  }).then(loadActive).catch(function (e) {
    toast('Could not reach the server: ' + e.message);
  });
})();
