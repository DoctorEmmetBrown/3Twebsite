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

  // Formulaire de contact : envoi en JSON vers le service configuré (hugo.toml)
  var form = document.querySelector('[data-contact-form]');
  if (form) {
    var status = form.querySelector('[data-status]');
    var submit = form.querySelector('[data-submit]');
    var opened = Date.now();
    var say = function (msg, kind) { status.textContent = msg; status.className = 'form-status ' + (kind || ''); };
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      if (!form.reportValidity()) return;
      var data = {};
      new FormData(form).forEach(function (v, k) { data[k] = v; });
      // Robots : pot de miel rempli ou formulaire envoyé en moins de 3 s -> faux succès, rien n'est envoyé.
      if (data._honey || Date.now() - opened < 3000) { say('Thank you, your message has been sent.', 'ok'); form.reset(); return; }
      data._subject = '[3T website] ' + data.topic + ' - ' + data.name;
      data._replyto = data.email;
      submit.disabled = true;
      say('Sending…');
      fetch(form.dataset.endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify(data)
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!r.ok || j.success === false || j.success === 'false' || j.ok === false) throw new Error(j.message || r.status);
        });
      }).then(function () {
        say('Thank you, your message has been sent. We will get back to you soon.', 'ok');
        form.reset();
      }).catch(function () {
        say('Sorry, the message could not be sent. Please try again later.', 'err');
      }).then(function () { submit.disabled = false; });
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
