const form = document.getElementById('loan-form');
const draftKey = 'prestaya-new-loan-draft';
const newClientLink = document.getElementById('new-client-link');
newClientLink.addEventListener('click', () => {
  try {
    const draft = Object.fromEntries(['client_id', 'principal', 'rate', 'months', 'frequency', 'start_date']
      .map(name => [name, form.elements[name].value]));
    sessionStorage.setItem(draftKey, JSON.stringify(draft));
  } catch (_) { /* El enlace sigue funcionando si el navegador bloquea el almacenamiento. */ }
});
try {
  const saved = sessionStorage.getItem(draftKey);
  if (saved) {
    sessionStorage.removeItem(draftKey);
    const draft = JSON.parse(saved);
    const hasSelectedClient = new URLSearchParams(location.search).has('client_id');
    for (const name of ['client_id', 'principal', 'rate', 'months', 'frequency', 'start_date']) {
      if (name === 'client_id' && hasSelectedClient) continue;
      if (typeof draft[name] === 'string') form.elements[name].value = draft[name];
    }
  }
} catch (_) { /* El formulario conserva sus valores predeterminados. */ }
const currency = new Intl.NumberFormat('es-CO', { style: 'currency', currency: 'COP', maximumFractionDigits: 0 });
function estimate() {
  const principal = Number(form.elements.principal.value);
  const rate = Number(form.elements.rate.value);
  const months = Number(form.elements.months.value);
  const interval = { weekly: 7, fortnightly: 15, monthly: 30 }[form.elements.frequency.value];
  if (!principal || !months || rate < 0 || !interval) return;
  const interest = Math.round(principal * rate * months / 100);
  document.getElementById('estimate-interest').textContent = currency.format(interest);
  document.getElementById('estimate-total').textContent = currency.format(principal + interest);
  document.getElementById('estimate-count').textContent = Math.ceil(months * 30 / interval);
}
form.addEventListener('input', estimate);
estimate();
