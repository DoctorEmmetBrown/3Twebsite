(function () {
  // Menu mobile
  var btn = document.querySelector('.nav-toggle');
  var nav = document.getElementById('nav');
  if (btn && nav) {
    btn.addEventListener('click', function () {
      var open = nav.classList.toggle('open');
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && nav.classList.contains('open')) {
        nav.classList.remove('open');
        btn.setAttribute('aria-expanded', 'false');
        btn.focus();
      }
    });
  }

  // Page Publications : recherche et filtre par groupe
  var bar = document.querySelector('[data-pubs-filters]');
  var list = document.querySelector('[data-pubs]');
  if (!bar || !list) return;
  var input = bar.querySelector('input');
  var chips = bar.querySelectorAll('[data-group]');
  var counter = document.querySelector('[data-pubs-count]');
  var items = list.querySelectorAll('.pub');
  var group = '';

  function strip(s) { return s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, ''); }
  function apply() {
    var q = strip(input.value.trim());
    var shown = 0;
    items.forEach(function (li) {
      var okGroup = !group || (' ' + li.dataset.groups + ' ').indexOf(' ' + group + ' ') > -1;
      var okText = !q || strip(li.dataset.search).indexOf(q) > -1;
      li.hidden = !(okGroup && okText);
      if (!li.hidden) shown++;
    });
    list.querySelectorAll('[data-year-block]').forEach(function (b) {
      b.hidden = !b.querySelector('.pub:not([hidden])');
    });
    if (counter) counter.textContent = (q || group) ? shown + ' of ' + items.length + ' publications' : '';
  }
  input.addEventListener('input', apply);
  chips.forEach(function (c) {
    c.addEventListener('click', function () {
      group = c.dataset.group;
      chips.forEach(function (o) { o.classList.toggle('on', o === c); o.setAttribute('aria-pressed', o === c ? 'true' : 'false'); });
      apply();
    });
  });
})();
