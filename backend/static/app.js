let currentTicker = 'TCS.NS';
let currentPeriod = '1y';
let mainChart = null;
let rsiChart = null;
let finChart = null;
let stockData = null; // Store current stock data for toggles

const TINT_COLORS = {
    sma20: '#10b981',
    sma50: '#f59e0b',
    sma200: '#ef4444',
    rsi: '#6366f1',
    rev: '#6366f1',
    net: '#10b981'
};

// Initialize the dashboard
async function init() {
    await loadTopStocks();
    await loadStockDetails(currentTicker, currentPeriod);
    setupEventListeners();
}

// Fetch and display the top IT stock list in the sidebar
async function loadTopStocks() {
    try {
        const response = await fetch('/api/stocks/top');
        const stocks = await response.json();
        const stockListContainer = document.getElementById('stock-list');
        stockListContainer.innerHTML = '';

        if (!stocks || !Array.isArray(stocks)) return;

        stocks.forEach(stock => {
            const item = document.createElement('div');
            item.className = `stock-item ${stock.ticker === currentTicker ? 'active' : ''}`;
            item.dataset.ticker = stock.ticker;
            
            const isPositive = (stock.change || 0) >= 0;
            const currentPrice = stock.currentPrice ? stock.currentPrice.toLocaleString('en-IN') : '---';
            const changePercent = stock.changePercent ? stock.changePercent.toFixed(2) : '0.00';

            item.innerHTML = `
                <div class="ticker">${stock.name}</div>
                <div class="price-min">
                    <div class="price-val">₹${currentPrice}</div>
                    <div class="change-val ${isPositive ? 'change-positive' : 'change-negative'}">
                        ${isPositive ? '▲' : '▼'} ${changePercent}%
                    </div>
                </div>
            `;
            
            item.onclick = () => {
                document.querySelectorAll('.stock-item').forEach(el => el.classList.remove('active'));
                item.classList.add('active');
                currentTicker = stock.ticker;
                loadStockDetails(currentTicker, currentPeriod);
            };
            
            stockListContainer.appendChild(item);
        });
    } catch (error) {
        console.error('Error loading top stocks:', error);
    }
}

// Fetch and display analytical details for a specific stock
async function loadStockDetails(ticker, period) {
    if (!ticker) {
        console.warn('loadStockDetails called without a valid ticker.');
        return;
    }
    try {
        const response = await fetch(`/api/stocks/${ticker}?period=${period}`);
        const data = await response.json();
        stockData = data; // Store globally for toggle updates
        
        if (data.error) {
            console.error(data.error);
            return;
        }

        updateDashboardUI(data);
        renderCharts(data);
    } catch (error) {
        console.error('Error loading stock details:', error);
    }
}

function updateDashboardUI(data) {
    // Update Header
    document.getElementById('main-stock-name').textContent = data.name || '---';
    document.getElementById('main-stock-ticker').textContent = data.ticker ? data.ticker.split('.')[0] : '---';
    
    const currentPrice = data.currentPrice ? data.currentPrice.toLocaleString('en-IN') : '0.00';
    document.getElementById('main-stock-price').textContent = `₹${currentPrice}`;
    
    const isPositive = (data.change || 0) >= 0;
    const changeEl = document.getElementById('main-stock-change');
    changeEl.className = `price-change ${isPositive ? 'change-positive' : 'change-negative'}`;
    
    const changeVal = data.change ? data.change.toLocaleString('en-IN') : '0.00';
    const changePct = data.changePercent ? data.changePercent.toFixed(2) : '0.00';
    
    changeEl.querySelector('.value').textContent = `${isPositive ? '+' : ''}${changeVal}`;
    changeEl.querySelector('.percent').textContent = `(${changePct}%)`;

    // Update KPIs
    document.getElementById('kpi-mcap').textContent = formatLargeNumber(data.marketCap);
    document.getElementById('kpi-pe').textContent = data.peRatio ? data.peRatio.toFixed(2) : 'N/A';
    document.getElementById('kpi-high').textContent = data.high52 ? `₹${data.high52.toLocaleString('en-IN')}` : '---';
    document.getElementById('kpi-low').textContent = data.low52 ? `₹${data.low52.toLocaleString('en-IN')}` : '---';

    // Update Sentiment Indicator
    updateSentimentUI(data.avgSentiment);

    // Update AI Advisor
    updateAdvisorUI(data.advisor);
}

function updateAdvisorUI(advisor) {
    if (!advisor) return;
    
    const statusEl = document.getElementById('advisor-status');
    const insightEl = document.getElementById('advisor-insight');
    
    statusEl.textContent = advisor.status;
    statusEl.className = `advisor-status status-${advisor.status.toLowerCase()}`;
    insightEl.textContent = advisor.summary;
}

function updateSentimentUI(score) {
    const indicator = document.getElementById('sentiment-indicator');
    const statusText = document.getElementById('sentiment-text');
    
    // Convert score (-1 to 1) to percentage width (0 to 100) starting from center (50%)
    const percentage = ((score + 1) / 2) * 100;
    
    indicator.style.left = score >= 0 ? '50%' : `${percentage}%`;
    indicator.style.width = `${Math.abs(score) * 50}%`;
    
    if (score >= 0.05) {
        indicator.style.background = 'var(--success-color)';
        statusText.textContent = 'Bullish';
        statusText.style.color = 'var(--success-color)';
    } else if (score <= -0.05) {
        indicator.style.background = 'var(--danger-color)';
        statusText.textContent = 'Bearish';
        statusText.style.color = 'var(--danger-color)';
    } else {
        indicator.style.background = 'var(--text-secondary)';
        statusText.textContent = 'Neutral';
        statusText.style.color = 'var(--text-secondary)';
    }
}

let forecastChart = null;

function renderCharts(data) {
    if (!data.history || data.history.length === 0) return;

    renderMainChart(data);
    renderForecastChart(data);
    renderRSIChart(data);
    renderFinancialsChart(data);
    renderNewsFeed(data.news);
}

function renderForecastChart(data) {
    if (!data.forecast || data.forecast.length === 0) return;

    const options = {
        series: [{
            name: 'Projected Price',
            data: data.forecast.map(item => ({ x: new Date(item.date), y: item.yhat }))
        }],
        chart: {
            id: 'forecast-chart',
            type: 'line',
            height: 280,
            toolbar: { show: false },
            animations: { enabled: true }
        },
        colors: ['#10b981'],
        stroke: { curve: 'smooth', width: 3, dashArray: [5] },
        xaxis: { type: 'datetime', labels: { style: { colors: '#94a3b8' } } },
        yaxis: { labels: { style: { colors: '#94a3b8' }, formatter: val => `₹${val.toFixed(0)}` } },
        grid: { borderColor: 'rgba(255, 255, 255, 0.05)' },
        tooltip: { theme: 'dark', x: { format: 'dd MMM' } },
        subtitle: {
            text: '* AI-generated 7-day projection',
            align: 'right',
            style: { color: '#64748b', fontSize: '10px' }
        }
    };

    if (forecastChart) {
        forecastChart.updateOptions(options);
    } else {
        forecastChart = new ApexCharts(document.querySelector("#forecast-chart"), options);
        forecastChart.render();
    }
}

function renderNewsFeed(news) {
    const newsFeed = document.getElementById('news-feed');
    newsFeed.innerHTML = '';
    
    if (!news || news.length === 0) {
        newsFeed.innerHTML = '<div class="status-badge">No recent news available.</div>';
        return;
    }

    news.forEach(item => {
        const sentimentClass = `badge-${item.sentiment.toLowerCase()}`;
        const card = document.createElement('a');
        card.href = item.link;
        card.target = '_blank';
        card.className = 'news-card';
        card.innerHTML = `
            <div class="news-title">${item.title}</div>
            <div class="news-meta">
                <span class="news-source">${item.publisher} • ${item.time}</span>
                <span class="sentiment-badge ${sentimentClass}">${item.sentiment}</span>
            </div>
        `;
        newsFeed.appendChild(card);
    });
}

function renderMainChart(data) {
    const series = [{
        name: 'Price',
        type: 'area',
        data: data.history.map(item => ({ x: new Date(item.date), y: item.price }))
    }];

    // Add enabled overlays
    document.querySelectorAll('.toggle-sm:checked').forEach(toggle => {
        const indicator = toggle.dataset.indicator;
        if (data.technicals && data.technicals[indicator]) {
            series.push({
                name: indicator.toUpperCase(),
                type: 'line',
                data: data.history.map((item, idx) => ({ 
                    x: new Date(item.date), 
                    y: data.technicals[indicator][idx] 
                }))
            });
        }
    });

    const options = {
        series: series,
        chart: {
            id: 'main-chart',
            type: 'line',
            height: 440,
            toolbar: { show: false },
            zoom: { enabled: false },
            animations: { enabled: true }
        },
        colors: ['#6366f1', TINT_COLORS.sma20, TINT_COLORS.sma50, TINT_COLORS.sma200],
        stroke: { curve: 'smooth', width: [3, 2, 2, 2] },
        fill: {
            type: ['gradient', 'solid', 'solid', 'solid'],
            gradient: { opacityFrom: 0.4, opacityTo: 0.05 }
        },
        xaxis: { type: 'datetime', labels: { style: { colors: '#94a3b8' } } },
        yaxis: { labels: { style: { colors: '#94a3b8' }, formatter: val => `₹${val.toFixed(0)}` } },
        grid: { borderColor: 'rgba(255, 255, 255, 0.05)' },
        tooltip: { theme: 'dark', x: { format: 'dd MMM yyyy' }, shared: true }
    };

    if (mainChart) {
        mainChart.updateOptions(options);
    } else {
        mainChart = new ApexCharts(document.querySelector("#main-chart"), options);
        mainChart.render();
    }
}

function renderRSIChart(data) {
    if (!data.technicals || !data.technicals.rsi) return;

    const options = {
        series: [{
            name: 'RSI',
            data: data.history.map((item, idx) => ({ x: new Date(item.date), y: data.technicals.rsi[idx] }))
        }],
        chart: {
            id: 'rsi-chart',
            type: 'line',
            height: 180,
            toolbar: { show: false },
            brush: { enabled: false, target: 'main-chart' }
        },
        colors: [TINT_COLORS.rsi],
        stroke: { width: 2 },
        yaxis: { min: 0, max: 100, labels: { style: { colors: '#94a3b8' } } },
        xaxis: { type: 'datetime', labels: { show: false } },
        annotations: {
            yaxis: [
                { y: 70, borderColor: '#ef4444', label: { text: 'Overbought', style: { color: '#ef4444', background: 'transparent' } } },
                { y: 30, borderColor: '#10b981', label: { text: 'Oversold', style: { color: '#10b981', background: 'transparent' } } }
            ]
        },
        grid: { borderColor: 'rgba(255, 255, 255, 0.05)' },
        tooltip: { theme: 'dark', x: { show: false } }
    };

    if (rsiChart) {
        rsiChart.updateOptions(options);
    } else {
        rsiChart = new ApexCharts(document.querySelector("#rsi-chart"), options);
        rsiChart.render();
    }
}

function renderFinancialsChart(data) {
    if (!data.financials || data.financials.length === 0) return;

    const options = {
        series: [
            { name: 'Revenue', data: data.financials.map(f => (f.revenue / 10000000).toFixed(0)) },
            { name: 'Net Income', data: data.financials.map(f => (f.netIncome / 10000000).toFixed(0)) }
        ],
        chart: { type: 'bar', height: 320, toolbar: { show: false } },
        colors: [TINT_COLORS.rev, TINT_COLORS.net],
        plotOptions: { bar: { horizontal: false, columnWidth: '55%', borderRadius: 4 } },
        dataLabels: { enabled: false },
        xaxis: { categories: data.financials.map(f => f.period), labels: { style: { colors: '#94a3b8' } } },
        yaxis: { labels: { style: { colors: '#94a3b8' } } },
        legend: { labels: { colors: '#94a3b8' } },
        grid: { borderColor: 'rgba(255, 255, 255, 0.05)' },
        tooltip: { theme: 'dark' }
    };

    if (finChart) {
        finChart.updateOptions(options);
    } else {
        finChart = new ApexCharts(document.querySelector("#fin-chart"), options);
        finChart.render();
    }
}

function setupEventListeners() {
    // Period Toggles
    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.onclick = () => {
            document.querySelectorAll('.filter-btn').forEach(el => el.classList.remove('active'));
            btn.classList.add('active');
            currentPeriod = btn.dataset.period;
            loadStockDetails(currentTicker, currentPeriod);
        };
    });

    // Indicator Toggles
    document.querySelectorAll('.toggle-sm').forEach(toggle => {
        toggle.onchange = () => {
            if (stockData) renderMainChart(stockData);
        };
    });

    // Search Logic
    const searchInput = document.getElementById('search-input');
    const searchResults = document.getElementById('search-results');
    let searchTimeout = null;

    searchInput.addEventListener('input', (e) => {
        const query = e.target.value.trim();
        clearTimeout(searchTimeout);
        
        if (query.length < 2) {
            searchResults.classList.add('hidden');
            return;
        }

        searchTimeout = setTimeout(async () => {
            try {
                const response = await fetch(`/api/stocks/search?q=${encodeURIComponent(query)}`);
                const items = await response.json();
                renderSearchResults(items);
            } catch (err) {
                console.error('Search error:', err);
            }
        }, 300);
    });

    // Close search results when clicking outside
    document.addEventListener('click', (e) => {
        if (!searchInput.contains(e.target) && !searchResults.contains(e.target)) {
            searchResults.classList.add('hidden');
        }
    });
}

function renderSearchResults(items) {
    const searchResults = document.getElementById('search-results');
    searchResults.innerHTML = '';
    
    if (items.length === 0) {
        searchResults.innerHTML = '<div class="status-badge">No results found.</div>';
    } else {
        items.forEach(item => {
            const el = document.createElement('div');
            el.className = 'search-result-item';
            el.innerHTML = `
                <div class="result-ticker">${item.ticker}</div>
                <div class="result-name">${item.name} (${item.exchange || 'Stock'})</div>
            `;
            el.onclick = () => {
                currentTicker = item.ticker;
                loadStockDetails(currentTicker, currentPeriod);
                document.getElementById('search-input').value = '';
                searchResults.classList.add('hidden');
                
                // Refresh Top Stocks to highlight if it was one of them
                loadTopStocks();
            };
            searchResults.appendChild(el);
        });
    }
    searchResults.classList.remove('hidden');
}

function formatLargeNumber(num) {
    if (num === null || num === undefined) return '---';
    if (num >= 10000000) return `₹${(num / 10000000).toFixed(2)} Cr`;
    if (num >= 100000) return `₹${(num / 100000).toFixed(2)} L`;
    return `₹${num.toLocaleString('en-IN')}`;
}

// Start app
init();
