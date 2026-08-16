(() => {
  const el = document.getElementById('dashboard-data');
  if (!el || typeof Chart === 'undefined') return;

  const data = JSON.parse(el.textContent);
  Chart.defaults.font.family = 'Inter, ui-sans-serif, system-ui, sans-serif';
  Chart.defaults.color = '#667085';
  const grid = '#eef0f3';
  const bamBlue = '#244b9b';
  const resolvedGreen = '#1f9d72';

  const trend = document.getElementById('trendChart');
  if (trend) {
    const detected = data.trend.detected || [];
    const resolved = data.trend.resolved || [];
    const maximum = Math.max(0, ...detected, ...resolved);
    new Chart(trend, {
      type: 'line',
      data: {
        labels: data.trend.labels,
        datasets: [
          {
            label: 'Détectées',
            data: detected,
            borderColor: bamBlue,
            backgroundColor: 'rgba(36,75,155,.08)',
            fill: true,
            tension: .3,
            pointRadius: 3,
            pointHoverRadius: 5,
            pointBackgroundColor: '#ffffff',
            pointBorderColor: bamBlue,
            pointBorderWidth: 2,
            borderWidth: 2,
          },
          {
            label: 'Résolues',
            data: resolved,
            borderColor: resolvedGreen,
            backgroundColor: 'transparent',
            fill: false,
            tension: .3,
            pointRadius: 3,
            pointHoverRadius: 5,
            pointBackgroundColor: '#ffffff',
            pointBorderColor: resolvedGreen,
            pointBorderWidth: 2,
            borderWidth: 2,
            borderDash: [5, 4],
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {mode: 'index', intersect: false},
        animation: {duration: 450},
        plugins: {
          legend: {display: false},
          tooltip: {
            displayColors: true,
            backgroundColor: '#102a56',
            padding: 10,
            callbacks: {
              title: (items) => `Date : ${items[0].label}`,
              label: (item) => ` ${item.dataset.label} : ${item.formattedValue}`,
            },
          },
        },
        scales: {
          x: {
            grid: {display: false},
            border: {display: false},
            ticks: {maxRotation: 0, autoSkip: true, maxTicksLimit: 7},
          },
          y: {
            beginAtZero: true,
            suggestedMax: Math.max(3, maximum + 1),
            ticks: {precision: 0, stepSize: 1},
            grid: {color: grid},
            border: {display: false},
          },
        },
      },
    });
  }

})();
