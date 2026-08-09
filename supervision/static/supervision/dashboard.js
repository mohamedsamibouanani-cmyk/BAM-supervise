(() => {
  const el = document.getElementById('dashboard-data');
  if (!el || typeof Chart === 'undefined') return;

  const data = JSON.parse(el.textContent);
  Chart.defaults.font.family = 'Inter, ui-sans-serif, system-ui, sans-serif';
  Chart.defaults.color = '#667085';
  const grid = '#eef0f3';
  const bamBlue = '#244b9b';
  const bamYellow = '#ffdc00';

  const trend = document.getElementById('trendChart');
  if (trend) {
    new Chart(trend, {
      type: 'line',
      data: {
        labels: data.trend.labels,
        datasets: [{
          label: 'Anomalies',
          data: data.trend.values,
          borderColor: bamBlue,
          backgroundColor: 'rgba(36,75,155,.09)',
          fill: true,
          tension: .38,
          pointRadius: 3,
          pointHoverRadius: 5,
          pointBackgroundColor: bamYellow,
          pointBorderColor: bamBlue,
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
          backgroundColor: [bamBlue, bamYellow, '#6f8dcc'],
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
