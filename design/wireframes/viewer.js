/* viewer.js — lays every board in boards.js out on one pannable, zoomable canvas.
 *
 * Plain browser JS, no build, no framework, no network. Each board is its own file in an iframe at
 * its native size; zoom is a CSS transform on the one element that holds them all, so Chrome
 * re-rasterises the boards at the zoomed scale and text stays crisp instead of being blown up as
 * pixels. Nothing here reaches into a board, and no board file is modified to be viewable. */
(function () {
  'use strict';

  var ROUTES = window.WIREFRAME_BOARDS || [];
  var SIZES = window.WIREFRAME_SIZES || {};
  var DESKTOP = SIZES.desktop || { w: 1440, h: 900, name: 'Desktop' };
  var MOBILE = SIZES.mobile || { w: 390, h: 960, name: 'iPhone' };

  var GAP_PAIR = 56;    // desktop board ↔ its phone board
  var GAP_X = 240;      // between route groups
  var GAP_Y = 210;
  var LABEL_H = 108;    // the route label band above each group
  var PAD = 48;         // breathing room a fit leaves around what it fits
  var MIN_K = 0.02, MAX_K = 4;

  var viewport = document.getElementById('viewport');
  var world = document.getElementById('world');
  var railList = document.getElementById('rail-list');
  var railSub = document.getElementById('rail-sub');
  var zoomLabel = document.getElementById('zoom');
  var here = document.getElementById('here');

  var boards = [];      // flat, in reading order: each route's desktop then its phone
  var groups = [];      // one per route, with its rect on the canvas
  var view = { x: 0, y: 0, k: 1 };
  var current = 0;

  /* ---- layout ---------------------------------------------------------- */

  var groupW = DESKTOP.w + GAP_PAIR + MOBILE.w;
  var groupH = LABEL_H + Math.max(DESKTOP.h, MOBILE.h);
  var cols = Math.max(1, Math.round(Math.sqrt(ROUTES.length || 1)));
  var rows = Math.ceil((ROUTES.length || 1) / cols);
  var worldW = cols * groupW + (cols - 1) * GAP_X;
  var worldH = rows * groupH + (rows - 1) * GAP_Y;

  function build() {
    world.style.width = worldW + 'px';
    world.style.height = worldH + 'px';

    ROUTES.forEach(function (route, i) {
      var gx = (i % cols) * (groupW + GAP_X);
      var gy = Math.floor(i / cols) * (groupH + GAP_Y);
      var el = document.createElement('section');
      el.className = 'group';
      el.style.cssText = 'left:' + gx + 'px;top:' + gy + 'px;width:' + groupW + 'px;height:' + groupH + 'px';
      var h2 = document.createElement('h2');
      h2.textContent = route.label;
      el.appendChild(h2);

      var group = { route: route, el: el, label: route.label, rect: { x: gx, y: gy, w: groupW, h: groupH }, boards: [] };
      [['desktop', route.desktop, route.desktopSize || DESKTOP, 0],
       ['mobile', route.mobile, route.mobileSize || MOBILE, DESKTOP.w + GAP_PAIR]].forEach(function (spec) {
        if (!spec[1]) return;
        var size = spec[2];
        var board = {
          kind: spec[0], file: spec[1], group: group, index: boards.length,
          name: route.label + ' · ' + (size.name || spec[0]),
          rect: { x: gx + spec[3], y: gy + LABEL_H, w: size.w, h: size.h }
        };
        el.appendChild(boardEl(board, size, spec[3]));
        boards.push(board);
        group.boards.push(board);
      });
      groups.push(group);
      world.appendChild(el);
    });

    countBoards();
  }

  function countBoards() {
    var gone = boards.filter(function (b) { return b.missing; }).length;
    railSub.textContent = ROUTES.length + ' routes · ' + boards.length + ' boards' +
      (gone ? ' · ' + gone + ' file' + (gone === 1 ? '' : 's') + ' missing' : '');
  }

  function boardEl(board, size, dx) {
    var el = document.createElement('div');
    el.className = 'board';
    el.style.cssText = 'left:' + dx + 'px;top:' + LABEL_H + 'px;width:' + size.w + 'px;height:' + size.h + 'px';

    var cap = document.createElement('div');
    cap.className = 'cap';
    cap.innerHTML = '<b></b> <span></span>';
    cap.firstChild.textContent = size.name || board.kind;
    cap.lastChild.textContent = size.w + '×' + size.h + ' · ' + board.file;
    el.appendChild(cap);

    var frame = document.createElement('iframe');   // every board loads at once: the 16 of them are
    frame.src = board.file;                         // ~340 ms and ~23 MB, so there is nothing that
    frame.title = board.name;                       // lazy loading would buy
    frame.setAttribute('scrolling', 'no');
    el.appendChild(frame);

    var hit = document.createElement('div');
    hit.className = 'hit';
    el.appendChild(hit);

    board.el = el;
    board.frame = frame;
    return el;
  }

  /* ---- a listed file that is not there --------------------------------- */

  /* Chrome hands a file:// page an opaque origin: fetch() is blocked and a sibling iframe's document
   * reads back as null whether the file loaded or 404'd, so neither can tell a missing board from a
   * present one. A <script> tag can: a script that cannot be fetched fires `error`, and one that is
   * fetched fires `load` even though a board's first `<` makes it a SyntaxError. That SyntaxError is
   * the probe working, so it is kept out of the console while probes are in flight. */
  var probing = 0;
  var probeUrls = Object.create(null);

  window.addEventListener('error', function (ev) {
    if (probing > 0 && (!ev.filename || probeUrls[ev.filename])) ev.preventDefault();
  });

  function probe(url, done) {
    var s = document.createElement('script');
    probing++;
    probeUrls[new URL(url, location.href).href] = true;
    function finish(ok) { probing--; s.remove(); done(ok); }
    s.onload = function () { finish(true); };
    s.onerror = function () { finish(false); };
    s.src = url;
    document.head.appendChild(s);
  }

  function checkFiles() {
    boards.forEach(function (board) {
      probe(board.file, function (ok) {
        if (ok) return;
        board.missing = true;
        board.frame.remove();
        var warn = document.createElement('div');
        warn.className = 'gone';
        warn.innerHTML = '<b>File not found</b><code></code>' +
          '<small>boards.js lists this file, but it is not beside index.html. ' +
          'Rename it there, or drop the entry.</small>';
        warn.querySelector('code').textContent = board.file;
        board.el.appendChild(warn);
        if (board.railButton) board.railButton.classList.add('gone');
        countBoards();
      });
    });
  }

  /* ---- the side list --------------------------------------------------- */

  function buildRail() {
    groups.forEach(function (group) {
      var wrap = document.createElement('div');
      wrap.className = 'rail-route';
      var head = document.createElement('button');
      head.type = 'button';
      head.textContent = group.label;
      head.addEventListener('click', function () { showGroup(group); });
      wrap.appendChild(head);

      var kids = document.createElement('div');
      kids.className = 'rail-kids';
      group.boards.forEach(function (board) {
        var b = document.createElement('button');
        b.type = 'button';
        b.textContent = (board.kind === 'mobile' ? MOBILE.name : DESKTOP.name);
        b.addEventListener('click', function () { showBoard(board.index); });
        board.railButton = b;
        if (board.missing) b.classList.add('gone');
        kids.appendChild(b);
      });
      wrap.appendChild(kids);
      group.railEl = wrap;
      railList.appendChild(wrap);
    });
  }

  function markCurrent() {
    var board = boards[current];
    boards.forEach(function (b) {
      b.el.classList.toggle('on', b === board);
      if (b.railButton) b.railButton.classList.toggle('on', b === board);
    });
    groups.forEach(function (g) { g.railEl.classList.toggle('on', g === board.group); });
    here.textContent = board.name + (board.missing ? ' — file missing' : '');
  }

  /* ---- the transform --------------------------------------------------- */

  function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }

  function apply(animate) {
    world.style.transition = animate ? 'transform .3s cubic-bezier(.22,.61,.36,1)' : 'none';
    world.style.transform = 'translate(' + view.x + 'px,' + view.y + 'px) scale(' + view.k + ')';
    zoomLabel.value = Math.round(view.k * 100) + '%';
  }

  function fitRect(rect, animate) {
    var vw = viewport.clientWidth, vh = viewport.clientHeight;
    var k = clamp(Math.min((vw - 2 * PAD) / rect.w, (vh - 2 * PAD) / rect.h), MIN_K, MAX_K);
    view = { k: k, x: (vw - rect.w * k) / 2 - rect.x * k, y: (vh - rect.h * k) / 2 - rect.y * k };
    apply(animate !== false);
  }

  function zoomTo(k, px, py) {
    k = clamp(k, MIN_K, MAX_K);
    var wx = (px - view.x) / view.k, wy = (py - view.y) / view.k;
    view.k = k;
    view.x = px - wx * k;
    view.y = py - wy * k;
    apply(false);
  }

  function zoomBy(factor) {
    zoomTo(view.k * factor, viewport.clientWidth / 2, viewport.clientHeight / 2);
  }

  var mode = 'all';   // what a window resize should re-fit

  function fitAll(animate) {
    mode = 'all';
    fitRect({ x: 0, y: 0, w: worldW, h: worldH }, animate);
  }

  function showGroup(group, animate) {
    mode = 'route';
    current = group.boards.length ? group.boards[0].index : current;
    fitRect(group.rect, animate);
    markCurrent();
  }

  function showBoard(i, animate) {
    mode = 'board';
    current = (i + boards.length) % boards.length;
    fitRect(boards[current].rect, animate);
    markCurrent();
  }

  function actualSize(animate) {   // the current board at 1:1, top-aligned when it is taller than
    mode = 'actual';                // the viewport, so the board starts where a browser would show it
    var rect = boards[current].rect;
    var vw = viewport.clientWidth, vh = viewport.clientHeight;
    view = { k: 1, x: (vw - rect.w) / 2 - rect.x, y: Math.max(PAD - rect.y, (vh - rect.h) / 2 - rect.y) };
    apply(animate !== false);
    markCurrent();
  }

  function refit() {   // a resize or a rail toggle keeps whatever the last deliberate fit framed;
    if (mode === 'all') fitAll(false);              // a hand-panned view ('free') is left alone
    else if (mode === 'route') fitRect(boards[current].group.rect, false);
    else if (mode === 'board') fitRect(boards[current].rect, false);
    else if (mode === 'actual') actualSize(false);
  }

  function boardAt(px, py) {
    var wx = (px - view.x) / view.k, wy = (py - view.y) / view.k;
    for (var i = 0; i < boards.length; i++) {
      var r = boards[i].rect;
      if (wx >= r.x && wx <= r.x + r.w && wy >= r.y && wy <= r.y + r.h) return boards[i];
    }
    return null;
  }

  /* ---- pointer, wheel, keyboard ---------------------------------------- */

  function local(ev) {
    var r = viewport.getBoundingClientRect();
    return { x: ev.clientX - r.left, y: ev.clientY - r.top };
  }

  /* Wheels do not agree on units: a trackpad sends pixels, a mouse notch can arrive as lines or as
   * one 120px jump. Everything is normalised to pixels, and the zoom step is capped so one notch of
   * a mouse wheel is a step rather than a leap while a trackpad pinch stays smooth. */
  function wheelPixels(v, unit) { return unit === 1 ? v * 16 : (unit === 2 ? v * 400 : v); }

  viewport.addEventListener('wheel', function (ev) {
    ev.preventDefault();
    mode = 'free';
    var p = local(ev);
    var dy = wheelPixels(ev.deltaY, ev.deltaMode);
    if (ev.ctrlKey || ev.metaKey) {          // ctrl/⌘+wheel, and a trackpad pinch, which Chrome
      zoomTo(view.k * Math.exp(-clamp(dy, -30, 30) * 0.01), p.x, p.y);   // reports as a ctrl wheel
    } else {
      view.x -= wheelPixels(ev.deltaX, ev.deltaMode);
      view.y -= dy;
      apply(false);
    }
  }, { passive: false });

  var pointers = new Map();
  var pinch = null;

  function pointerList() {
    var out = [];
    pointers.forEach(function (p) { out.push(p); });
    return out;
  }

  viewport.addEventListener('pointerdown', function (ev) {
    try { viewport.setPointerCapture(ev.pointerId); } catch (e) { /* a pointer already gone */ }
    var p = local(ev);
    pointers.set(ev.pointerId, { x: p.x, y: p.y, t: Date.now(), moved: 0 });
    pinch = pointers.size === 2 ? gestureState() : null;
    viewport.classList.add('dragging');
    viewport.focus({ preventScroll: true });
  });

  function gestureState() {
    var a = pointerList()[0], b = pointerList()[1];
    return { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  }

  viewport.addEventListener('pointermove', function (ev) {
    var p = pointers.get(ev.pointerId);
    if (!p) return;
    var n = local(ev);
    var dx = n.x - p.x, dy = n.y - p.y;
    p.moved += Math.hypot(dx, dy);
    p.x = n.x; p.y = n.y;
    mode = 'free';
    if (pointers.size >= 2) {          // two fingers: the distance between them zooms, their
      var now = gestureState();        // midpoint pans, so the canvas stays under both
      if (pinch) {
        zoomTo(view.k * (now.d / pinch.d), pinch.x, pinch.y);
        view.x += now.x - pinch.x;
        view.y += now.y - pinch.y;
        apply(false);
      }
      pinch = now;
    } else {
      view.x += dx;
      view.y += dy;
      apply(false);
    }
  });

  function endPointer(ev) {
    var p = pointers.get(ev.pointerId);
    pointers.delete(ev.pointerId);
    pinch = pointers.size === 2 ? gestureState() : null;
    if (!pointers.size) viewport.classList.remove('dragging');
    if (!p) return;
    if (p.moved < 8 && Date.now() - p.t < 600 && !pointers.size) {   // a tap or a click, not a drag
      var board = boardAt(p.x, p.y);
      if (board) showBoard(board.index);
    }
  }
  viewport.addEventListener('pointerup', endPointer);
  viewport.addEventListener('pointercancel', endPointer);

  document.addEventListener('keydown', function (ev) {
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
    var k = ev.key;
    if (k === 'ArrowRight') showBoard(current + 1);
    else if (k === 'ArrowLeft') showBoard(current - 1);
    else if (k === 'ArrowDown' || k === 'ArrowUp') {
      var i = groups.indexOf(boards[current].group) + (k === 'ArrowDown' ? 1 : -1);
      showGroup(groups[(i + groups.length) % groups.length]);
    } else if (k === '0' || k === 'Home' || k === 'Escape') fitAll();
    else if (k === '1') actualSize();
    else if (k === 'r' || k === 'R') showGroup(boards[current].group);
    else if (k === '+' || k === '=') zoomBy(1.25);
    else if (k === '-' || k === '_') zoomBy(1 / 1.25);
    else if (k === 'l' || k === 'L') toggleRail();
    else return;
    ev.preventDefault();
  });

  function toggleRail() {
    document.body.classList.toggle('no-rail');
    refit();                       // the canvas just grew or shrank by the width of the list
  }

  document.getElementById('btn-all').addEventListener('click', function () { fitAll(); });
  document.getElementById('btn-route').addEventListener('click', function () { showGroup(boards[current].group); });
  document.getElementById('btn-100').addEventListener('click', function () { actualSize(); });
  document.getElementById('btn-in').addEventListener('click', function () { zoomBy(1.25); });
  document.getElementById('btn-out').addEventListener('click', function () { zoomBy(1 / 1.25); });
  document.getElementById('btn-rail').addEventListener('click', toggleRail);
  window.addEventListener('resize', refit);

  /* ---- go -------------------------------------------------------------- */

  if (!ROUTES.length) {
    railSub.textContent = 'boards.js lists no boards';
    here.textContent = 'boards.js lists no boards.';
    return;
  }
  if (window.innerWidth < 760) document.body.classList.add('no-rail');
  build();
  buildRail();
  checkFiles();
  markCurrent();
  fitAll(false);
})();
