const signatureForm = document.getElementById('signature-form');
const pads = new Map();

for (const id of ['lender-pad', 'debtor-pad']) {
  const canvas = document.getElementById(id);
  const context = canvas.getContext('2d');
  const pad = { canvas, context, drawing: false, signed: false };
  pads.set(id, pad);

  function clear() {
    context.fillStyle = '#fff';
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.strokeStyle = '#183857';
    context.lineWidth = 4;
    context.lineCap = 'round';
    context.lineJoin = 'round';
    pad.signed = false;
  }
  pad.clear = clear;
  clear();

  function point(event) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) * canvas.width / rect.width,
      y: (event.clientY - rect.top) * canvas.height / rect.height,
    };
  }
  canvas.addEventListener('pointerdown', event => {
    event.preventDefault();
    canvas.setPointerCapture(event.pointerId);
    pad.drawing = true;
    const { x, y } = point(event);
    context.beginPath();
    context.moveTo(x, y);
  });
  canvas.addEventListener('pointermove', event => {
    if (!pad.drawing) return;
    event.preventDefault();
    const { x, y } = point(event);
    context.lineTo(x, y);
    context.stroke();
    pad.signed = true;
  });
  for (const eventName of ['pointerup', 'pointercancel']) {
    canvas.addEventListener(eventName, () => { pad.drawing = false; });
  }
}

for (const button of document.querySelectorAll('.clear-signature')) {
  button.addEventListener('click', () => pads.get(button.dataset.pad).clear());
}

signatureForm.addEventListener('submit', event => {
  if ([...pads.values()].some(pad => !pad.signed)) {
    event.preventDefault();
    alert('El prestamista y el deudor deben dibujar su firma.');
    return;
  }
  document.getElementById('lender-signature-data').value = pads.get('lender-pad').canvas.toDataURL('image/png');
  document.getElementById('debtor-signature-data').value = pads.get('debtor-pad').canvas.toDataURL('image/png');
  signatureForm.querySelector('button[type="submit"]').disabled = true;
});
