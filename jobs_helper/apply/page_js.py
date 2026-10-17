"""JavaScript run inside application pages: form schema extraction, success detection, confirmation banner."""

# Tags every visible control with data-jh-id, groups radio buttons into one question and
# marks the most likely submit button with data-jh-submit.
EXTRACT_FORM = r"""
(rootSelector) => {
  const root = rootSelector ? document.querySelector(rootSelector) : document;
  if (!root) return { fields: [], captcha: false, submit: false, url: location.href };
  // querySelectorAll that also walks open shadow roots (web-component forms, e.g. SmartRecruiters)
  const deepAll = (node, sel) => {
    const out = [...node.querySelectorAll(sel)];
    node.querySelectorAll('*').forEach(el => { if (el.shadowRoot) out.push(...deepAll(el.shadowRoot, sel)); });
    return out;
  };
  const visible = el => { const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none' && parseFloat(st.opacity) > 0.05; };
  const clean = t => (t || '').replace(/\s+/g, ' ').trim().slice(0, 200);
  const labelOf = el => {
    const rn = el.getRootNode && el.getRootNode().querySelector ? el.getRootNode() : document;
    if (el.id) { const l = rn.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return clean(l.innerText); }
    const wrap = el.closest('label'); if (wrap) return clean(wrap.innerText);
    if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'));
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const n = (rn.getElementById ? rn : document).getElementById(lb.split(' ')[0]); if (n) return clean(n.innerText); }
    const box = el.closest('.field, .form-group, .application-question, .application-field, [class*="field"], [class*="question"], [class*="input"]');
    if (box) { const l = box.querySelector('label, legend, .label, [class*="label"], [class*="title"]'); if (l && clean(l.innerText)) return clean(l.innerText); }
    return clean(el.placeholder || el.name || '');
  };
  const questionOf = el => {
    const fs = el.closest('fieldset'); if (fs) { const lg = fs.querySelector('legend'); if (lg) return clean(lg.innerText); }
    const box = el.closest('[class*="question"], [class*="field"], .form-group, [role="radiogroup"], [role="group"]');
    if (box) { const l = box.querySelector('legend, label:not(:has(input)), [class*="label"], [class*="title"]'); if (l) return clean(l.innerText); }
    return clean(el.name);
  };
  const fields = [], groups = {};
  let i = 0;
  deepAll(root, 'input, textarea, select').forEach(el => {
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'search', 'image', 'reset'].includes(type)) return;
    if ((el.readOnly || el.disabled) && type !== 'file') return;   // mirrors / composed widgets: fill the visible parts instead
    // custom checkbox / radio widgets hide the real <input>: judge by its label or wrapper instead
    const shown = visible(el) || type === 'file' ||
      (['checkbox', 'radio'].includes(type) && [el.closest('label'), el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`),
        el.parentElement, el.parentElement && el.parentElement.parentElement].some(x => x && visible(x)));
    if (!shown) return;
    if (/honeypot|leave this field blank/i.test(el.name + ' ' + labelOf(el))) return;
    const id = 'f' + (i++);
    el.setAttribute('data-jh-id', id);
    // a standalone "*" (or a .required marker) inside the nearest ancestors that hold only this control
    let starNear = false, anc = el.parentElement;
    for (let k = 0; k < 4 && anc && !starNear; k++, anc = anc.parentElement) {
      if (anc.querySelectorAll('input:not([type=hidden]), textarea, select').length > (type === 'radio' ? 99 : 1)) break;
      starNear = [...anc.querySelectorAll('span, abbr, sup, i, div')].some(x => x.children.length === 0 &&
        (/^\s*[*✱]\s*$/.test(x.textContent) || /(^|\s|-)required(\s|$)/i.test(x.className + ' ' + (x.title || ''))));
    }
    const required = el.required || el.getAttribute('aria-required') === 'true' || /[*✱]\s*$/.test(labelOf(el)) || starNear;
    if (type === 'radio') {
      const key = el.name || id;
      const g = groups[key] || (groups[key] = { dom: [], name: key, type: 'radio', label: questionOf(el), required: false, options: [] });
      g.dom.push(id); g.options.push(labelOf(el) || el.value); g.required = g.required || required;
      return;
    }
    fields.push({ dom: [id], name: el.name || el.id || '', type: el.tagName === 'TEXTAREA' ? 'textarea' : (el.tagName === 'SELECT' ? 'select' : type),
                  label: labelOf(el), required,
                  options: el.tagName === 'SELECT' ? [...el.options].map(o => clean(o.text)).filter(Boolean).slice(0, 60) : [],
                  range: type === 'range' || type === 'number' ? { min: el.min, max: el.max, step: el.step } : undefined });
  });
  Object.values(groups).forEach(g => fields.push(g));
  const scope = root.querySelector ? root : document;
  let submit = deepAll(scope, 'button[type="submit"], input[type="submit"]').find(visible);
  if (!submit) submit = deepAll(scope, 'button, a[role="button"]').find(b =>
      /submit|apply|send|надіслати|відправити|подати|відгукнутися/i.test(b.innerText) && visible(b));
  if (submit) submit.setAttribute('data-jh-submit', '1');
  const captcha = [...document.querySelectorAll('iframe')].some(f =>
      /recaptcha\/api2\/(anchor|bframe)|hcaptcha\.com/.test(f.src) && f.getBoundingClientRect().height > 30) ||
      !!document.querySelector('.h-captcha:not([data-size="invisible"]), .g-recaptcha:not([data-size="invisible"])');
  return { fields, captcha, submit: !!submit, url: location.href };
}
"""

SUCCESS_RE = (r"thank you|thanks for applying|application (has been |was )?(submitted|received|sent)|"
              r"we('ve| have) received your application|дякуємо|відгук (надіслано|відправлено)|заявку (надіслано|прийнято)|"
              r"успішно (надіслано|відправлено)|ви (вже )?відгукнулися")

VALIDATION_ERRORS = r"""
() => {
  const bad = [...document.querySelectorAll('[data-jh-id]')].filter(el => el.matches(':invalid') || el.getAttribute('aria-invalid') === 'true');
  return bad.map(el => el.getAttribute('data-jh-id'));
}
"""

# Fixed panel shown on top of the filled form. Sets window.__jh_decision to 'yes' | 'no'.
CONFIRM_BANNER = r"""
([title, text, yes, no]) => {
  if (document.getElementById('jh-banner')) return;
  const b = document.createElement('div');
  b.id = 'jh-banner';
  b.style.cssText = 'position:fixed;z-index:2147483647;left:50%;top:16px;transform:translateX(-50%);max-width:560px;' +
    'background:#0f172a;color:#fff;font:15px/1.4 system-ui,sans-serif;padding:16px 18px;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.35)';
  b.innerHTML = `<div style="font-weight:700;margin-bottom:6px">${title}</div><div style="opacity:.9;margin-bottom:12px">${text}</div>`;
  const mk = (label, val, bg) => { const x = document.createElement('button'); x.textContent = label;
    x.style.cssText = `margin-right:8px;padding:8px 14px;border:0;border-radius:8px;cursor:pointer;font-weight:600;background:${bg};color:#fff`;
    x.onclick = () => { window.__jh_decision = val; b.remove(); }; return x; };
  if (yes) b.appendChild(mk(yes, 'yes', '#16a34a'));
  if (no) b.appendChild(mk(no, 'no', '#475569'));
  document.body.appendChild(b);
  const s = document.querySelector('[data-jh-submit]');
  if (s) { s.style.outline = '4px solid #f59e0b'; s.style.outlineOffset = '3px'; }
}
"""

# Range / number inputs: set a numeric value rounded to the input's step and fire the events frameworks listen to.
SET_NUMBER = r"""
(el, raw) => {
  const num = parseFloat(String(raw).replace(/[^0-9.]/g, ''));
  if (isNaN(num)) return null;
  const min = el.min !== '' ? parseFloat(el.min) : -Infinity, max = el.max !== '' ? parseFloat(el.max) : Infinity;
  const step = el.step && el.step !== 'any' ? parseFloat(el.step) : 0;
  let v = step ? Math.ceil((num - (isFinite(min) ? min : 0)) / step) * step + (isFinite(min) ? min : 0) : num;
  v = Math.min(max, Math.max(min, v));
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(el, String(v));
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return v;
}
"""

PAGE_EXCERPT = r"""() => (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').trim().slice(0, 600)"""
