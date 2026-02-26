import { useEffect, useMemo, useState } from 'react';
import './App.css';
import GraphViz from './components/GraphViz';

const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || 'http://localhost:8000';

async function apiFetch(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    ...options,
  });

  if (!response.ok) {
    let errorMessage = `Request failed with status ${response.status}`;
    try {
      const payload = await response.json();
      if (payload?.detail) {
        errorMessage = payload.detail;
      }
    } catch (_) {
      // Keep fallback error message when response body is not JSON.
    }
    throw new Error(errorMessage);
  }

  return response.json();
}

function formatDate(value) {
  if (!value) {
    return '-';
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleDateString();
}

const GRAPH_NODE_STYLE = {
  Case: { color: '#1f4b99', radius: 12 },
  Court: { color: '#1d8348', radius: 10 },
  Party: { color: '#b9770e', radius: 9 },
  Order: { color: '#7d3c98', radius: 8 },
  Default: { color: '#5f6d8a', radius: 8 },
};

function App() {
  const [summary, setSummary] = useState(null);
  const [cases, setCases] = useState([]);
  const [totalCases, setTotalCases] = useState(0);
  const [query, setQuery] = useState('');
  const [selectedCase, setSelectedCase] = useState(null);
  const [loadingCases, setLoadingCases] = useState(false);
  const [loadingDetails, setLoadingDetails] = useState(false);
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [loadingGraph, setLoadingGraph] = useState(false);
  const [error, setError] = useState('');
  const [graphData, setGraphData] = useState({ nodes: [], edges: [], node_count: 0, edge_count: 0 });

  const summaryCards = useMemo(() => {
    if (!summary) {
      return [];
    }

    return [
      { label: 'Cases', value: summary.cases },
      { label: 'Courts', value: summary.courts },
      { label: 'Parties', value: summary.parties },
      { label: 'Orders', value: summary.orders },
    ];
  }, [summary]);

  const loadSummary = async () => {
    setLoadingSummary(true);
    try {
      const data = await apiFetch('/api/dashboard/summary');
      setSummary(data);
      setError('');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingSummary(false);
    }
  };

  const loadCases = async (searchTerm = '') => {
    setLoadingCases(true);
    try {
      const params = new URLSearchParams();
      params.set('limit', '25');
      if (searchTerm.trim()) {
        params.set('query', searchTerm.trim());
      }
      const data = await apiFetch(`/api/cases?${params.toString()}`);
      setCases(data.data || []);
      setTotalCases(data.total || 0);
      setError('');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingCases(false);
    }
  };

  const loadCaseDetails = async (caseKey) => {
    setLoadingDetails(true);
    try {
      const data = await apiFetch(`/api/cases/${encodeURIComponent(caseKey)}`);
      setSelectedCase(data);
      setError('');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingDetails(false);
    }
  };

  const loadGraph = async (caseKey = '') => {
    setLoadingGraph(true);
    try {
      const params = new URLSearchParams();
      params.set('limit_cases', '40');
      if (caseKey?.trim()) {
        params.set('case_key', caseKey.trim());
      }
      const data = await apiFetch(`/api/graph/network?${params.toString()}`);
      setGraphData(data || { nodes: [], edges: [], node_count: 0, edge_count: 0 });
      setError('');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingGraph(false);
    }
  };

  const rebuildGraph = async () => {
    try {
      await apiFetch('/api/graph/build', {
        method: 'POST',
        body: JSON.stringify({}),
      });
      await Promise.all([loadSummary(), loadCases(query), loadGraph(selectedCase?.case_key || '')]);
    } catch (err) {
      setError(err.message);
    }
  };

  useEffect(() => {
    loadSummary();
    loadCases();
    loadGraph();
  }, []);

  const handleSearch = async (event) => {
    event.preventDefault();
    await loadCases(query);
  };

  return (
    <div className="App">
      <header className="app-header">
        <div>
          <h1>Judicial Intelligence Dashboard</h1>
          <p className="subtitle">Backend: {API_BASE_URL}</p>
        </div>
        <button className="primary-btn" onClick={rebuildGraph}>Rebuild Graph</button>
      </header>

      {error && <div className="error-banner">{error}</div>}

      <section className="summary-grid">
        {loadingSummary && <div className="card">Loading summary...</div>}
        {!loadingSummary && summaryCards.map((card) => (
          <article className="card" key={card.label}>
            <p className="card-label">{card.label}</p>
            <p className="card-value">{card.value}</p>
          </article>
        ))}
      </section>

      <main className="content-grid">
        <section className="panel">
          <form className="search-row" onSubmit={handleSearch}>
            <input
              type="search"
              placeholder="Search by case, court, or party"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <button type="submit">Search</button>
          </form>
          <p className="muted">Showing {cases.length} of {totalCases} cases</p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Case</th>
                  <th>Court</th>
                  <th>Order Date</th>
                </tr>
              </thead>
              <tbody>
                {loadingCases && (
                  <tr>
                    <td colSpan={3}>Loading cases...</td>
                  </tr>
                )}
                {!loadingCases && cases.length === 0 && (
                  <tr>
                    <td colSpan={3}>No cases found.</td>
                  </tr>
                )}
                {!loadingCases && cases.map((item) => (
                  <tr
                    key={item.case_key}
                    onClick={() => {
                      loadCaseDetails(item.case_key);
                      loadGraph(item.case_key);
                    }}
                    className="row-clickable"
                  >
                    <td>{item.case_key}</td>
                    <td>{item.courts?.[0] || '-'}</td>
                    <td>{formatDate(item.order_date)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel">
          <h2>Case Details</h2>
          {loadingDetails && <p>Loading case details...</p>}
          {!loadingDetails && !selectedCase && (
            <p className="muted">Select a case from the table to view details.</p>
          )}
          {!loadingDetails && selectedCase && (
            <div className="details">
              <div>
                <p className="detail-label">Case Key</p>
                <p>{selectedCase.case_key}</p>
              </div>
              <div>
                <p className="detail-label">Courts</p>
                <p>{selectedCase.courts?.join(', ') || '-'}</p>
              </div>
              <div>
                <p className="detail-label">Petitioner(s)</p>
                <p>{selectedCase.petitioners?.join(', ') || '-'}</p>
              </div>
              <div>
                <p className="detail-label">Respondent(s)</p>
                <p>{selectedCase.respondents?.join(', ') || '-'}</p>
              </div>
              <div>
                <p className="detail-label">Orders</p>
                <ul>
                  {(selectedCase.orders || []).map((order) => (
                    <li key={order.order_key}>
                      {formatDate(order.order_date)}
                      {order.document_url && (
                        <>
                          {' '}
                          - <a href={order.document_url} target="_blank" rel="noreferrer">document</a>
                        </>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </section>
      </main>

      <section className="panel graph-panel">
        <div className="graph-header">
          <h2>Judicial Knowledge Graph</h2>
          <button type="button" onClick={() => loadGraph('')}>Show Global Graph</button>
        </div>
        <p className="muted">
          Nodes: {graphData.node_count} | Relationships: {graphData.edge_count}
        </p>
        {loadingGraph && <p>Loading graph...</p>}
        {!loadingGraph && graphData.node_count === 0 && (
          <p className="muted">
            Graph is empty. Click "Rebuild Graph" to materialize the Judicial Knowledge Graph.
          </p>
        )}
        {!loadingGraph && graphData.node_count > 0 && (
          <div className="graph-canvas-wrap">
            <GraphViz data={graphData} height={420} />
          </div>
        )}
        <div className="legend">
          {Object.entries(GRAPH_NODE_STYLE)
            .filter(([key]) => key !== 'Default')
            .map(([key, value]) => (
              <span key={key} className="legend-item">
                <span className="legend-dot" style={{ backgroundColor: value.color }} />
                {key}
              </span>
            ))}
        </div>
      </section>
    </div>
  );
}

export default App;
