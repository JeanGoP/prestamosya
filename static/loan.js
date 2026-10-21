const form = document.getElementById('loan-form');
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
