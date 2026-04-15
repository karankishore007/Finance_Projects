/**
 * Agent Dashboard — Frontend logic for the AI Trading Agent panels.
 *
 * Handles: signal display, model votes, paper portfolio,
 * backtest triggering, and equity curve rendering.
 */

// ============================================================================
// State
// ============================================================================
let currentSignal = null;
let backtestChart = null;
let agentInitialized = false;

// ============================================================================
// View Switcher (extend the existing one for the Agent tab)
// ============================================================================
document.addEventListener('DOMContentLoaded', () => {
    const viewMarket = document.getElementById('view-market');
    const viewAgent = document.getElementById('view-agent');
    const viewPortfolio = document.getElementById('view-portfolio');
    const marketNav = document.getElementById('market-nav');
    const agentNav = document.getElementById('agent-nav');
    const portfolioNav = document.getElementById('portfolio-nav');

    if (!viewAgent) return; // graceful exit if HTML not updated yet

    function switchView(active) {
        [viewMarket, viewAgent, viewPortfolio].forEach(b => b.classList.remove('active'));
        [marketNav, agentNav, portfolioNav].forEach(n => n.classList.add('hidden'));
        active.classList.add('active');
        if (active === viewMarket) marketNav.classList.remove('hidden');
        if (active === viewAgent) {
            agentNav.classList.remove('hidden');
            if (!agentInitialized) {
                loadPaperPortfolio();
                agentInitialized = true;
            }
        }
        if (active === viewPortfolio) portfolioNav.classList.remove('hidden');
    }

    viewMarket.onclick = () => switchView(viewMarket);
    viewAgent.onclick = () => switchView(viewAgent);
    viewPortfolio.onclick = () => switchView(viewPortfolio);

    // Agent action buttons
    document.getElementById('btn-get-signal').onclick = () => fetchSignal(currentTicker);
    document.getElementById('btn-run-backtest').onclick = () => runBacktest(currentTicker);
    document.getElementById('btn-paper-trade').onclick = () => placePaperTradeFromSignal();
});

// ============================================================================
// Fetch Agent Signal
// ============================================================================
async function fetchSignal(ticker) {
    const card = document.getElementById('agent-signal-card');
    card.innerHTML = '<div class="agent-loading"><div class="spinner"></div>Analyzing models...</div>';

    try {
        const response = await fetch(`/api/agent/signal/${ticker}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        currentSignal = data;
        renderSignalCard(data);
    } catch (err) {
        console.error('Signal fetch error:', err);
        card.innerHTML = `<div class="agent-loading" style="color:var(--danger-color)">Signal failed: ${err.message}</div>`;
    }
}

// ============================================================================
// Render Signal Card
// ============================================================================
function renderSignalCard(signal) {
    const card = document.getElementById('agent-signal-card');
    const dir = signal.signal === -1 ? 'buy' : signal.signal === 1 ? 'sell' : 'hold';
    const confPct = (signal.confidence * 100).toFixed(0);
    const confColor = signal.confidence >= 0.75 ? 'var(--success-color)' :
                      signal.confidence >= 0.5 ? 'var(--accent-color)' : 'var(--text-secondary)';

    // Model votes HTML
    const allSignals = [...(signal.legacy_signals || []), ...(signal.sota_signals || [])];
    const modelNames = ['Mean Reversion', 'Bollinger Bands', 'Fibonacci', 'Transformer', 'Sentiment'];
    let votesHtml = '';
    allSignals.forEach((m, i) => {
        const strength = Math.abs(m.score) * 100;
        const voteDir = m.score < -0.1 ? 'buy' : m.score > 0.1 ? 'sell' : 'neutral';
        votesHtml += `
            <div class="model-vote-row">
                <span class="model-name">${modelNames[i] || m.model_name}</span>
                <div class="vote-bar-track">
                    <div class="vote-bar-fill ${voteDir}" style="width:${strength}%"></div>
                </div>
                <span class="vote-score" style="color:${voteDir === 'buy' ? 'var(--success-color)' : voteDir === 'sell' ? 'var(--danger-color)' : 'var(--text-secondary)'}">
                    ${m.score > 0 ? '+' : ''}${m.score.toFixed(2)}
                </span>
            </div>`;
    });

    // SL/TP levels
    let levelsHtml = '';
    if (signal.entry_price) {
        levelsHtml = `
            <div class="signal-levels">
                <div class="level-item">
                    <div class="level-label">Entry</div>
                    <div class="level-value entry">₹${signal.entry_price.toFixed(0)}</div>
                </div>
                <div class="level-item">
                    <div class="level-label">Stop Loss</div>
                    <div class="level-value sl">₹${signal.stop_loss.toFixed(0)}</div>
                </div>
                <div class="level-item">
                    <div class="level-label">Target</div>
                    <div class="level-value tp">₹${signal.take_profit.toFixed(0)}</div>
                </div>
            </div>`;
    }

    card.innerHTML = `
        <div class="signal-header">
            <div class="signal-direction ${dir}">${signal.signal_label}</div>
            <div class="signal-confidence" style="color:${confColor}">${confPct}%</div>
        </div>
        <div class="confidence-bar-track">
            <div class="confidence-bar-fill" style="width:${confPct}%; background:${confColor}"></div>
        </div>
        ${levelsHtml}
        <div class="model-votes">
            <div class="model-votes-title">Council Votes</div>
            ${votesHtml}
        </div>
        ${signal.reasoning ? `<div class="signal-reasoning">${signal.reasoning}</div>` : ''}
        <div style="margin-top:8px;font-size:10px;color:var(--text-secondary)">
            Latency: ${signal.latency_ms?.toFixed(0) || '—'}ms
        </div>
    `;
}

// ============================================================================
// Backtest
// ============================================================================
async function runBacktest(ticker) {
    const section = document.getElementById('backtest-section');
    const metricsEl = document.getElementById('bt-metrics');
    const tickerEl = document.getElementById('bt-ticker');

    section.classList.remove('hidden');
    tickerEl.textContent = ticker;
    metricsEl.innerHTML = '<div class="agent-loading"><div class="spinner"></div>Running backtest...</div>';

    try {
        const response = await fetch('/api/agent/backtest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ticker, strategy: 'meta_learner' })
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        renderBacktestResults(data);
    } catch (err) {
        console.error('Backtest error:', err);
        metricsEl.innerHTML = `<div class="agent-loading" style="color:var(--danger-color)">Backtest failed: ${err.message}</div>`;
    }
}

function renderBacktestResults(data) {
    const metricsEl = document.getElementById('bt-metrics');

    const metrics = [
        { label: 'CAGR', value: `${(data.cagr * 100).toFixed(1)}%`, color: data.cagr >= 0 ? 'var(--success-color)' : 'var(--danger-color)' },
        { label: 'Sharpe', value: data.sharpe_ratio.toFixed(2), color: data.sharpe_ratio >= 1 ? 'var(--success-color)' : 'var(--text-primary)' },
        { label: 'Max DD', value: `${(data.max_drawdown * 100).toFixed(1)}%`, color: 'var(--danger-color)' },
        { label: 'Win Rate', value: `${(data.win_rate * 100).toFixed(0)}%`, color: data.win_rate >= 0.55 ? 'var(--success-color)' : 'var(--text-primary)' },
        { label: 'Trades', value: data.total_trades, color: 'var(--text-primary)' },
        { label: 'Profit Factor', value: data.profit_factor === Infinity ? '∞' : data.profit_factor.toFixed(2), color: 'var(--accent-color)' },
    ];

    metricsEl.innerHTML = metrics.map(m => `
        <div class="bt-metric">
            <div class="bt-metric-label">${m.label}</div>
            <div class="bt-metric-value" style="color:${m.color}">${m.value}</div>
        </div>
    `).join('');

    // Render equity curve
    if (data.equity_curve && data.equity_curve.length > 0) {
        renderEquityCurve(data);
    }
}

function renderEquityCurve(data) {
    const options = {
        series: [{
            name: 'Equity',
            data: data.equity_dates.map((d, i) => ({ x: new Date(d), y: data.equity_curve[i] }))
        }],
        chart: {
            type: 'area',
            height: 280,
            toolbar: { show: false },
            animations: { enabled: true }
        },
        colors: ['#6366f1'],
        stroke: { curve: 'smooth', width: 2 },
        fill: {
            type: 'gradient',
            gradient: { opacityFrom: 0.35, opacityTo: 0.05 }
        },
        xaxis: { type: 'datetime', labels: { style: { colors: '#94a3b8' } } },
        yaxis: { labels: { style: { colors: '#94a3b8' }, formatter: v => `₹${(v/100000).toFixed(1)}L` } },
        grid: { borderColor: 'rgba(255,255,255,0.05)' },
        tooltip: { theme: 'dark', x: { format: 'dd MMM yyyy' } }
    };

    if (backtestChart) {
        backtestChart.updateOptions(options);
    } else {
        backtestChart = new ApexCharts(document.querySelector('#backtest-chart'), options);
        backtestChart.render();
    }
}

// ============================================================================
// Paper Portfolio
// ============================================================================
async function loadPaperPortfolio() {
    try {
        const response = await fetch('/api/agent/paper/portfolio');
        const data = await response.json();

        document.getElementById('paper-nav').textContent = `₹${(data.nav || 0).toLocaleString('en-IN')}`;
        document.getElementById('paper-return').textContent = `${(data.total_return_pct || 0).toFixed(1)}%`;
        document.getElementById('paper-return').style.color = (data.total_return_pct || 0) >= 0 ? 'var(--success-color)' : 'var(--danger-color)';
        document.getElementById('paper-cash').textContent = `₹${((data.cash || 0) / 100000).toFixed(1)}L`;
        document.getElementById('paper-open').textContent = data.open_positions || 0;
    } catch (err) {
        console.error('Paper portfolio load error:', err);
    }
}

async function placePaperTradeFromSignal() {
    if (!currentSignal || currentSignal.signal === 0) {
        alert('No active signal. Get a signal first, then place a trade.');
        return;
    }

    const side = currentSignal.signal === -1 ? 'buy' : 'sell';
    const qty = prompt(`Enter quantity for ${side.toUpperCase()} ${currentSignal.ticker}:`, '1');
    if (!qty || isNaN(parseInt(qty))) return;

    try {
        const response = await fetch('/api/agent/paper/trade', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ticker: currentSignal.ticker,
                side: side,
                quantity: parseInt(qty),
                entry_price: currentSignal.entry_price,
                stop_loss: currentSignal.stop_loss,
                take_profit: currentSignal.take_profit,
                confidence: currentSignal.confidence,
                reasoning: currentSignal.reasoning
            })
        });
        const result = await response.json();

        if (result.status === 'filled') {
            alert(`Paper trade placed: ${result.order.order_id}`);
        } else {
            alert(`Trade rejected: ${result.reason || result.message}`);
        }
        loadPaperPortfolio();
    } catch (err) {
        console.error('Paper trade error:', err);
        alert('Failed to place paper trade');
    }
}
