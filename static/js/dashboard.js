/**
 * VenomLink Dashboard Operations, CSRF Helper & Charting
 */

window.getCsrfToken = function () {
  const meta = document.querySelector('meta[name="csrf-token"]');
  if (meta && meta.getAttribute('content') && meta.getAttribute('content') !== 'NOTPROVIDED') {
    return meta.getAttribute('content');
  }
  const cookies = document.cookie ? document.cookie.split(';') : [];
  for (let i = 0; i < cookies.length; i++) {
    const cookie = cookies[i].trim();
    if (cookie.substring(0, 10) === 'csrftoken=') {
      return decodeURIComponent(cookie.substring(10));
    }
  }
  const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
  return input ? input.value : '';
};

document.addEventListener('DOMContentLoaded', function () {
  // Global State Selector in Subheader
  const stateSelector = document.getElementById('global-state-select');
  if (stateSelector) {
    stateSelector.addEventListener('change', function () {
      const selectedVal = this.value;
      const url = new URL(window.location.href);
      if (selectedVal) {
        url.searchParams.set('state', selectedVal);
      } else {
        url.searchParams.delete('state');
      }
      window.location.href = url.toString();
    });
  }

  // Global Period Selector in Subheader (30d / 7d)
  const periodSelector = document.getElementById('period-select');
  if (periodSelector) {
    periodSelector.addEventListener('change', function () {
      const selectedPeriod = this.value || '30d';
      const url = new URL(window.location.href);
      url.searchParams.set('period', selectedPeriod);
      window.location.href = url.toString();
    });
  }

  // Facility Stock Chart on Detail Page (Clean HTML5 Canvas)
  const chartCanvas = document.getElementById('facility-stock-chart');
  if (chartCanvas) {
    const rawData = chartCanvas.getAttribute('data-points');
    let points = [];
    try {
      points = JSON.parse(rawData || '[]');
    } catch (e) {
      console.error('Failed to parse stock chart data:', e);
    }
    renderStockChart(chartCanvas, points);
  }

  function renderStockChart(canvas, dataPoints) {
    if (!dataPoints || dataPoints.length === 0) return;

    const ctx = canvas.getContext('2d');
    const width = canvas.width = canvas.parentElement.clientWidth || 600;
    const height = canvas.height = 200;
    const padding = { top: 20, right: 30, bottom: 40, left: 45 };

    const maxStock = Math.max(...dataPoints.map(p => p.stock), 15);
    const plotWidth = width - padding.left - padding.right;
    const plotHeight = height - padding.top - padding.bottom;

    ctx.clearRect(0, 0, width, height);
    ctx.strokeStyle = '#E5E9E3';
    ctx.lineWidth = 1;

    const gridSteps = 4;
    ctx.fillStyle = '#546E7A';
    ctx.font = '11px -apple-system, sans-serif';
    ctx.textAlign = 'right';

    for (let i = 0; i <= gridSteps; i++) {
      const val = Math.round((maxStock / gridSteps) * i);
      const y = padding.top + plotHeight - (val / maxStock) * plotHeight;
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(width - padding.right, y);
      ctx.stroke();
      ctx.fillText(val + ' v', padding.left - 8, y + 4);
    }

    // Critical Threshold Line (at 2 vials)
    const critY = padding.top + plotHeight - (2 / maxStock) * plotHeight;
    ctx.strokeStyle = '#FFCDD2';
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(padding.left, critY);
    ctx.lineTo(width - padding.right, critY);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#B71C1C';
    ctx.textAlign = 'right';
    ctx.fillText('Crit (2)', width - padding.right, critY - 4);

    const coords = dataPoints.map((pt, idx) => {
      const x = padding.left + (idx / Math.max(1, dataPoints.length - 1)) * plotWidth;
      const y = padding.top + plotHeight - (pt.stock / maxStock) * plotHeight;
      return { x, y, label: pt.label, stock: pt.stock };
    });

    ctx.beginPath();
    ctx.strokeStyle = '#0B5D4B';
    ctx.lineWidth = 2.5;
    coords.forEach((coord, idx) => {
      if (idx === 0) ctx.moveTo(coord.x, coord.y);
      else ctx.lineTo(coord.x, coord.y);
    });
    ctx.stroke();

    ctx.textAlign = 'center';
    coords.forEach(c => {
      ctx.beginPath();
      ctx.arc(c.x, c.y, 4.5, 0, Math.PI * 2);
      ctx.fillStyle = '#0B5D4B';
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = '#FFFFFF';
      ctx.stroke();

      ctx.fillStyle = '#263238';
      ctx.font = 'bold 11px -apple-system, sans-serif';
      ctx.fillText(c.stock, c.x, c.y - 8);

      ctx.fillStyle = '#546E7A';
      ctx.font = '10px -apple-system, sans-serif';
      ctx.fillText(c.label, c.x, height - 12);
    });
  }
});
