(function () {
  'use strict';
  if (location.pathname !== '/listings') return;
  function currentContext() {
    var raw = new URLSearchParams(location.search), clean = new URLSearchParams();
    var task = raw.get('task_id');
    if (task && /^[0-9]+$/.test(task) && Number(task) > 0 && Number(task) <= 2147483647) clean.set('task_id', String(Number(task)));
    var show = raw.get('show'), sort = raw.get('sort');
    clean.set('show', ['active','gone','blocked','all','unseen'].includes(show) ? show : 'active');
    clean.set('sort', ['satisfaction','price_asc','price_desc','newest'].includes(sort) ? sort : 'satisfaction');
    var state = raw.get('price_state');
    if (['exact','pending'].includes(state)) clean.set('price_state', state);
    var loaded = raw.get('loaded');
    if (loaded && /^[0-9]+$/.test(loaded) && Number(loaded) > 100) clean.set('loaded', String(Math.min(1000, Number(loaded))));
    return '/listings?' + clean.toString();
  }
  var key = function () { return 'second-eye:list:' + currentContext(); };
  var lastInteraction = 0;
  function save(anchor) {
    try {
      sessionStorage.setItem(key(), JSON.stringify({y: window.scrollY, anchor: anchor || null}));
    } catch (_) {}
  }
  function restore() {
    try {
      var state = JSON.parse(sessionStorage.getItem(key()) || 'null');
      if (!state) return;
      requestAnimationFrame(function () { requestAnimationFrame(function () {
        var anchor = state.anchor && document.getElementById('listing-' + state.anchor);
        if (anchor && Math.abs(anchor.offsetTop - state.y) < 500) anchor.scrollIntoView();
        else window.scrollTo(0, state.y || 0);
      }); });
    } catch (_) {}
  }
  function updateLinks() {
    var context = currentContext();
    document.querySelectorAll('#listings-grid a[href^="/listings/"], #listings-grid form[action^="/listings/"]').forEach(function(el) {
      var attr = el.tagName === 'FORM' ? 'action' : 'href';
      var url = new URL(el.getAttribute(attr), location.origin);
      url.searchParams.set('return_to', context);
      el.setAttribute(attr, url.pathname + url.search);
    });
    var poller = document.getElementById('list-poller');
    if (poller) {
      var pollURL = new URL(context, location.origin);
      pollURL.searchParams.set('partial', '1');
      poller.setAttribute('hx-get', pollURL.pathname + pollURL.search);
      if (window.htmx) htmx.process(poller);
    }
    var refresh = document.getElementById('new-list-results');
    if (refresh) refresh.href = context;
  }
  document.addEventListener('click', function(event) {
    lastInteraction = Date.now();
    var card = event.target.closest('[data-listing-id]');
    save(card && card.dataset.listingId);
  });
  document.addEventListener('input', function() { lastInteraction = Date.now(); });
  window.addEventListener('pagehide', function() { save(); });
  window.addEventListener('pageshow', restore);
  document.addEventListener('htmx:beforeSwap', function(event) {
    if (!event.detail.target || event.detail.target.id !== 'listings-grid') return;
    var poller = event.detail.requestConfig && event.detail.requestConfig.elt;
    if (poller && poller.id === 'list-poller' && (
      document.querySelector('.row-check:checked') || window.scrollY > 100 ||
      Date.now() - lastInteraction < 15000 || document.activeElement.closest('form'))) {
      event.detail.shouldSwap = false;
      document.getElementById('new-list-results').hidden = false;
    }
  });
  document.addEventListener('htmx:afterSettle', function() {
    var seen = new Set();
    document.querySelectorAll('[data-listing-id]').forEach(function(card) {
      if (seen.has(card.dataset.listingId)) card.remove();
      else seen.add(card.dataset.listingId);
    });
    updateLinks();
    save();
  });
  updateLinks();
})();
