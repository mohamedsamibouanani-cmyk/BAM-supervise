(() => {
  const el = document.getElementById('dashboard-data');
  if (!el || typeof Chart === 'undefined') return;

  const data = JSON.parse(el.textContent);
  Chart.defaults.font.family = 'Inter, ui-sans-serif, system-ui, sans-serif';
  Chart.defaults.color = '#667085';
  const grid = '#eef0f3';

  const trend = document.getElementById('trendChart');
  if (trend) {
    new Chart(trend, {
      type: 'line',
      data: {
        labels: data.trend.labels,
        datasets: [{
          label: 'Anomalies',
          data: data.trend.values,
          borderColor: '#a31f34',
          backgroundColor: 'rgba(163,31,52,.08)',
          fill: true,
          tension: .38,
          pointRadius: 3,
          pointHoverRadius: 5,
          borderWidth: 2,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {mode: 'index', intersect: false},
        plugins: {legend: {display: false}, tooltip: {displayColors: false}},
        scales: {
          x: {grid: {display: false}, border: {display: false}},
          y: {beginAtZero: true, ticks: {precision: 0}, grid: {color: grid}, border: {display: false}},
        },
      },
    });
  }

  const level = document.getElementById('levelChart');
  if (level) {
    new Chart(level, {
      type: 'doughnut',
      data: {
        labels: data.levels.labels,
        datasets: [{
          data: data.levels.values,
          backgroundColor: ['#a31f34', '#475467', '#98a2b3'],
          borderWidth: 0,
          hoverOffset: 5,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '72%',
        plugins: {
          legend: {
            position: 'bottom',
            labels: {usePointStyle: true, boxWidth: 7, padding: 18, font: {size: 11}},
          },
        },
      },
    });
  }
})();
