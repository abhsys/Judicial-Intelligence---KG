import { useCallback, useEffect, useMemo, useState } from 'react';
import './App.css';
import GraphViz from './components/GraphViz';
import { getIngestionGraph, getIngestionJob, uploadCaseFile } from './api';

const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || 'http://localhost:8000';
const HISTORY_STORAGE_KEY = 'ji_graph_history_v1';

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

function formatHistoryTime(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return '-';
  }
  return `${parsed.toLocaleDateString()} ${parsed.toLocaleTimeString()}`;
}

function labelStage(stage) {
  const mapping = {
    queued: 'Queued',
    extracting_text: 'Extracting Text',
    preparing_analysis: 'Preparing Analysis',
    ensuring_constraints: 'Preparing Graph',
    finalizing_keywords: 'AI Keyword Finalization',
    saving_upload_node: 'Saving Upload',
    cross_keyword_matching: 'AI Case Matching',
    saving_selected_cases: 'Saving Cases',
    building_graph_view: 'Building Graph',
    completed: 'Completed',
    failed: 'Failed',
  };
  return mapping[stage] || String(stage || '').replace(/_/g, ' ');
}

function getInitialHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_STORAGE_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed.filter((item) => item && item.graph && Array.isArray(item.graph.nodes) && Array.isArray(item.graph.edges));
  } catch {
    return [];
  }
}

function displayNodeType(node) {
  return node?.labels?.[0] || 'Node';
}

function displayNodeTitle(node) {
  return (
    node?.properties?.case_key ||
    node?.properties?.title ||
    node?.properties?.value ||
    node?.properties?.name ||
    node?.properties?.filename ||
    node?.properties?.result_url ||
    node?.properties?.order_key ||
    node?.id ||
    '-'
  );
}

function deriveCaseRowsFromGraph(graph) {
  const nodes = graph?.nodes || [];
  const edges = graph?.edges || [];
  if (!nodes.length) {
    return [];
  }

  const byId = new Map(nodes.map((n) => [n.id, n]));
  const courtByCaseId = new Map();

  for (const edge of edges) {
    const sourceId = typeof edge.source === 'object' ? edge.source.id : edge.source;
    const targetId = typeof edge.target === 'object' ? edge.target.id : edge.target;
    const sourceNode = byId.get(sourceId);
    const targetNode = byId.get(targetId);
    if (!sourceNode || !targetNode) {
      continue;
    }

    const sourceIsCase = (sourceNode.labels || []).includes('Case');
    const targetIsCase = (targetNode.labels || []).includes('Case');
    const sourceIsCourt = (sourceNode.labels || []).includes('Court');
    const targetIsCourt = (targetNode.labels || []).includes('Court');

    if (sourceIsCase && targetIsCourt) {
      const courtName = targetNode.properties?.name;
      if (courtName) {
        courtByCaseId.set(sourceId, courtName);
      }
    }
    if (targetIsCase && sourceIsCourt) {
      const courtName = sourceNode.properties?.name;
      if (courtName) {
        courtByCaseId.set(targetId, courtName);
      }
    }
  }

  const rows = [];
  const seenCaseKeys = new Set();
  for (const node of nodes) {
    const labels = node.labels || [];
    const props = node.properties || {};
    if (!labels.includes('Case')) {
      continue;
    }
    const caseKey = String(props.case_key || '').trim();
    if (!caseKey) {
      continue;
    }
    if (seenCaseKeys.has(caseKey)) {
      continue;
    }
    seenCaseKeys.add(caseKey);

    rows.push({
      id: node.id,
      case_key: caseKey,
      court: courtByCaseId.get(node.id) || props.court || '-',
      order_date: props.order_date || props.date || '',
      raw_case_key: caseKey,
    });
  }

  rows.sort((a, b) => {
    const aDate = a.order_date || '';
    const bDate = b.order_date || '';
    if (aDate !== bDate) {
      return aDate < bDate ? 1 : -1;
    }
    return String(a.case_key).localeCompare(String(b.case_key));
  });
  return rows;
}

const GRAPH_NODE_STYLE = {
  Case: { color: '#2e7278', radius: 12 },
  Court: { color: '#4d9499', radius: 10 },
  Party: { color: '#6aa8aa', radius: 9 },
  Order: { color: '#3f8086', radius: 8 },
  Default: { color: '#5a999e', radius: 8 },
};

function simplifyGraphData(graphData) {
  const nodes = graphData?.nodes || [];
  const edges = graphData?.edges || [];
  if (nodes.length === 0 || edges.length === 0) {
    return graphData || { nodes: [], edges: [], node_count: 0, edge_count: 0 };
  }

  const degreeMap = new Map();
  edges.forEach((e) => {
    const sourceId = typeof e.source === 'object' ? e.source.id : e.source;
    const targetId = typeof e.target === 'object' ? e.target.id : e.target;
    degreeMap.set(sourceId, (degreeMap.get(sourceId) || 0) + 1);
    degreeMap.set(targetId, (degreeMap.get(targetId) || 0) + 1);
  });

  const mustKeep = new Set();
  nodes.forEach((n) => {
    const labels = n.labels || [];
    const props = n.properties || {};
    if (
      labels.includes('UploadedCase') ||
      labels.includes('SearchKeyword') ||
      labels.includes('ExternalCase') ||
      props.upload_id ||
      props.case_key
    ) {
      mustKeep.add(n.id);
    }
  });

  const keptNodeIds = new Set();
  nodes.forEach((n) => {
    const degree = degreeMap.get(n.id) || 0;
    if (mustKeep.has(n.id) || degree >= 2) {
      keptNodeIds.add(n.id);
    }
  });

  if (keptNodeIds.size > 170) {
    const ranked = [...keptNodeIds]
      .map((id) => ({ id, degree: degreeMap.get(id) || 0 }))
      .sort((a, b) => b.degree - a.degree);
    const limited = new Set(ranked.slice(0, 170).map((x) => x.id));
    mustKeep.forEach((id) => limited.add(id));
    keptNodeIds.clear();
    limited.forEach((id) => keptNodeIds.add(id));
  }

  let keptEdges = edges.filter((e) => {
    const sourceId = typeof e.source === 'object' ? e.source.id : e.source;
    const targetId = typeof e.target === 'object' ? e.target.id : e.target;
    return keptNodeIds.has(sourceId) && keptNodeIds.has(targetId);
  });

  if (keptEdges.length > 280) {
    keptEdges = [...keptEdges]
      .sort((a, b) => (Number(b.weight) || 1) - (Number(a.weight) || 1))
      .slice(0, 280);
  }

  const referenced = new Set();
  keptEdges.forEach((e) => {
    const sourceId = typeof e.source === 'object' ? e.source.id : e.source;
    const targetId = typeof e.target === 'object' ? e.target.id : e.target;
    referenced.add(sourceId);
    referenced.add(targetId);
  });
  mustKeep.forEach((id) => referenced.add(id));

  const keptNodes = nodes.filter((n) => referenced.has(n.id));
  return {
    nodes: keptNodes,
    edges: keptEdges,
    node_count: keptNodes.length,
    edge_count: keptEdges.length,
  };
}

function App() {
  const [theme, setTheme] = useState(() => {
    const saved = localStorage.getItem('ji_theme');
    if (saved === 'theme-dark' || saved === 'theme-light') {
      return saved;
    }
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    return prefersDark ? 'theme-dark' : 'theme-light';
  });
  const [summary, setSummary] = useState(null);
  const [query, setQuery] = useState('');
  const [selectedCaseKey, setSelectedCaseKey] = useState('');
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [loadingGraph, setLoadingGraph] = useState(false);
  const [error, setError] = useState('');
  const [graphData, setGraphData] = useState({ nodes: [], edges: [], node_count: 0, edge_count: 0 });
  const [selectedGraphNode, setSelectedGraphNode] = useState(null);
  const [simplifiedView, setSimplifiedView] = useState(true);
  const [uploadFile, setUploadFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [intakeJob, setIntakeJob] = useState(null);
  const [graphHistory, setGraphHistory] = useState(() => getInitialHistory());
  const [historyOpen, setHistoryOpen] = useState(false);

  useEffect(() => {
    localStorage.setItem('ji_theme', theme);
  }, [theme]);

  useEffect(() => {
    localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(graphHistory.slice(0, 12)));
  }, [graphHistory]);

  const toggleTheme = () => {
    setTheme((prev) => (prev === 'theme-dark' ? 'theme-light' : 'theme-dark'));
  };

  const addGraphToHistory = useCallback((graph, meta = {}) => {
    if (!graph || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges) || graph.nodes.length === 0) {
      return;
    }
    const item = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      created_at: new Date().toISOString(),
      title: meta.title || (meta.caseKey ? `Case: ${meta.caseKey}` : 'Graph View'),
      case_key: meta.caseKey || '',
      node_count: graph.node_count ?? graph.nodes.length ?? 0,
      edge_count: graph.edge_count ?? graph.edges.length ?? 0,
      graph,
    };

    setGraphHistory((prev) => {
      const deduped = prev.filter(
        (x) =>
          !(
            x.case_key === item.case_key &&
            Number(x.node_count) === Number(item.node_count) &&
            Number(x.edge_count) === Number(item.edge_count)
          ),
      );
      return [item, ...deduped].slice(0, 12);
    });
  }, []);

  const summaryCards = useMemo(() => {
    if (!summary?.metrics) {
      return [];
    }

    const byId = new Map(summary.metrics.map((item) => [item.id, item]));
    return [
      {
        label: 'Appeared Yesterday',
        value: byId.get('appeared_yesterday')?.count ?? 0,
        className: 'card-cases',
      },
      {
        label: 'Resolved Yesterday',
        value: byId.get('resolved_yesterday')?.count ?? 0,
        className: 'card-courts',
      },
      {
        label: 'Pushed Due To Delay',
        value: byId.get('delayed_time_yesterday')?.count ?? 0,
        className: 'card-parties',
      },
      {
        label: 'Pushed To Next Hearing',
        value: byId.get('next_hearing_yesterday')?.count ?? 0,
        className: 'card-orders',
      },
    ];
  }, [summary]);

  const loadSummary = useCallback(async () => {
    setLoadingSummary(true);
    try {
      const data = await apiFetch('/api/dashboard/live-summary');
      setSummary(data);
      setError('');
    } catch (err) {
      setError(`Live metrics unavailable: ${err.message}`);
    } finally {
      setLoadingSummary(false);
    }
  }, []);

  const loadGraph = useCallback(async (caseKey = '') => {
    setLoadingGraph(true);
    try {
      const params = new URLSearchParams();
      params.set('limit_cases', '40');
      if (caseKey?.trim()) {
        params.set('case_key', caseKey.trim());
      }
      const data = await apiFetch(`/api/graph/network?${params.toString()}`);
      const nextGraph = data || { nodes: [], edges: [], node_count: 0, edge_count: 0 };
      setGraphData(nextGraph);
      addGraphToHistory(nextGraph, {
        caseKey: caseKey?.trim() || '',
        title: caseKey?.trim() ? `Case: ${caseKey.trim()}` : 'Graph: Current View',
      });
      setError('');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingGraph(false);
    }
  }, [addGraphToHistory]);

  useEffect(() => {
    loadSummary();
    loadGraph('');
  }, [loadSummary, loadGraph]);

  const resolveCaseKeyFromNode = (node) => {
    if (!node) {
      return '';
    }
    if (node.properties?.case_key) {
      return node.properties.case_key;
    }

    const byId = new Map((graphData.nodes || []).map((n) => [n.id, n]));
    for (const edge of graphData.edges || []) {
      const sourceId = typeof edge.source === 'object' ? edge.source.id : edge.source;
      const targetId = typeof edge.target === 'object' ? edge.target.id : edge.target;
      if (sourceId !== node.id && targetId !== node.id) {
        continue;
      }
      const otherId = sourceId === node.id ? targetId : sourceId;
      const otherNode = byId.get(otherId);
      if (otherNode?.properties?.case_key) {
        return otherNode.properties.case_key;
      }
    }

    return '';
  };

  const handleGraphNodeSelect = async (node) => {
    setSelectedGraphNode(node || null);
    const caseKey = resolveCaseKeyFromNode(node);
    if (!caseKey) {
      return;
    }
    setSelectedCaseKey(caseKey);
  };

  const visibleGraphData = useMemo(
    () => (simplifiedView ? simplifyGraphData(graphData) : graphData),
    [graphData, simplifiedView],
  );
  const graphCases = useMemo(() => deriveCaseRowsFromGraph(visibleGraphData), [visibleGraphData]);
  const filteredGraphCases = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) {
      return graphCases;
    }
    return graphCases.filter((item) =>
      `${item.case_key} ${item.court} ${item.order_date}`.toLowerCase().includes(needle),
    );
  }, [graphCases, query]);
  const selectedNodeProperties = useMemo(() => {
    if (!selectedGraphNode?.properties) {
      return [];
    }
    return Object.entries(selectedGraphNode.properties).filter(
      ([key]) => !['full_text', 'text', 'content'].includes(String(key).toLowerCase()),
    );
  }, [selectedGraphNode]);

  const handleSearch = async (event) => {
    event.preventDefault();
  };

  const handleUploadSubmit = async (event) => {
    event.preventDefault();
    if (!uploadFile || uploading) {
      return;
    }
    try {
      setUploading(true);
      const queued = await uploadCaseFile(uploadFile);
      setIntakeJob({ job_id: queued.job_id, status: queued.status, progress: 0, stage: 'queued' });
      setError('');
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
    }
  };

  useEffect(() => {
    if (!intakeJob?.job_id) {
      return undefined;
    }
    if (intakeJob.status === 'completed' || intakeJob.status === 'failed') {
      return undefined;
    }

    const timer = setInterval(async () => {
      try {
        const status = await getIngestionJob(intakeJob.job_id);
        setIntakeJob(status);
        if (status.status === 'completed') {
          const graph = await getIngestionGraph(intakeJob.job_id);
          const nextGraph = graph || { nodes: [], edges: [], node_count: 0, edge_count: 0 };
          setGraphData(nextGraph);
          addGraphToHistory(nextGraph, {
            title: `Upload: ${String(intakeJob.job_id).slice(0, 8)}`,
          });
          setSelectedCaseKey('');
          setSelectedGraphNode(null);
          clearInterval(timer);
        }
        if (status.status === 'failed') {
          clearInterval(timer);
        }
      } catch (err) {
        setError(err.message);
        clearInterval(timer);
      }
    }, 2000);

    return () => clearInterval(timer);
  }, [intakeJob, addGraphToHistory]);

  return (
    <div className={`App ${theme}`}>
      <header className="app-header">
        <div className="title-wrap">
          <p className="eyebrow">Judicial Analytics</p>
          <h1>CASELINQ</h1>
        </div>
        <div className="header-actions">
          <button className="tone-btn" type="button" onClick={() => setHistoryOpen(true)}>
            Recent Graphs
          </button>
          <button className="tone-btn" type="button" onClick={toggleTheme}>
            {theme === 'theme-dark' ? 'Light Mode' : 'Dark Mode'}
          </button>
        </div>
      </header>

      {error && <div className="error-banner">{error}</div>}

      <section className="summary-grid">
        {loadingSummary && <div className="card">Loading summary...</div>}
        {!loadingSummary && summaryCards.map((card) => (
          <article className={`card ${card.className}`} key={card.label}>
            <p className="card-label">{card.label}</p>
            <p className="card-value">{card.value}</p>
          </article>
        ))}
      </section>
      {!loadingSummary && summary?.source && (
        <p className="summary-note">
          Scope: {summary.scope || 'All Courts'} | Live Source: {summary.source} | Window: {summary.date_range} | {summary.note}
        </p>
      )}

      {historyOpen && (
        <button
          type="button"
          className="history-backdrop"
          aria-label="Close recent graphs"
          onClick={() => setHistoryOpen(false)}
        />
      )}
      <aside className={`history-drawer ${historyOpen ? 'open' : ''}`}>
        <div className="panel history-panel">
          <div className="history-head">
            <h2>Recent Graphs</h2>
            <div className="history-head-actions">
              <button
                type="button"
                className="tone-btn history-clear"
                onClick={() => setGraphHistory([])}
                disabled={graphHistory.length === 0}
              >
                Clear
              </button>
              <button
                type="button"
                className="tone-btn history-clear"
                onClick={() => setHistoryOpen(false)}
              >
                Close
              </button>
            </div>
          </div>
          <div className="history-list">
            {graphHistory.length === 0 && (
              <p className="muted">No graph history yet. Visualize a graph to save it here.</p>
            )}
            {graphHistory.map((item) => (
              <button
                key={item.id}
                type="button"
                className="history-item"
                onClick={() => {
                  setGraphData(item.graph);
                  setSelectedCaseKey(item.case_key || '');
                  setSelectedGraphNode(null);
                  setHistoryOpen(false);
                }}
              >
                <p className="history-title">{item.title}</p>
                <p className="history-meta">
                  {item.node_count} nodes | {item.edge_count} edges
                </p>
                <p className="history-meta">{formatHistoryTime(item.created_at)}</p>
              </button>
            ))}
          </div>
        </div>
      </aside>

      <div className="main-stack">
        <main className="content-grid">
          <section className="panel case-panel">
              <h2>Case Explorer</h2>
              <form className="upload-row" onSubmit={handleUploadSubmit}>
                <input
                  type="file"
                  accept=".pdf,.txt"
                  onChange={(event) => setUploadFile(event.target.files?.[0] || null)}
                />
                <button type="submit" disabled={!uploadFile || uploading}>
                  {uploading ? 'Uploading...' : 'Upload & Build Graph'}
                </button>
              </form>
              {intakeJob && (
                <div className={`upload-status ${intakeJob.status === 'failed' ? 'upload-status-failed' : ''}`}>
                  <div className="upload-status-head">
                    <p className="upload-status-title">
                      {intakeJob.status === 'completed' ? 'Processing Complete' : 'Processing Upload'}
                      {intakeJob.status === 'running' && <span className="status-spinner" aria-hidden="true" />}
                    </p>
                    <p className="upload-status-progress">{Math.max(0, Math.min(100, intakeJob.progress || 0))}%</p>
                  </div>
                  <div className="progress-track">
                    <div
                      className="progress-fill"
                      style={{ width: `${Math.max(0, Math.min(100, intakeJob.progress || 0))}%` }}
                    />
                  </div>
                  <p className="muted">
                    Stage: {labelStage(intakeJob.stage)}
                    {intakeJob.stage_detail ? ` - ${intakeJob.stage_detail}` : ''}
                  </p>
                  {intakeJob.status === 'running' && (
                    <p className="privacy-note">
                      We process uploads in-memory and do not retain your file after processing completes.
                    </p>
                  )}
                </div>
              )}
              {intakeJob?.keywords?.length > 0 && (
                <p className="muted">
                  Extracted keywords: {intakeJob.keywords.join(', ')} | Indexed results: {intakeJob.indexed_results || 0}
                </p>
              )}
              {intakeJob?.warnings?.length > 0 && (
                <p className="muted">Warnings: {intakeJob.warnings.join(' | ')}</p>
              )}
              {intakeJob?.error && (
                <p className="error-inline">Upload error: {intakeJob.error}</p>
              )}

              <form className="search-row" onSubmit={handleSearch}>
                <input
                  type="search"
                  placeholder="Search by case, court, or party"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
                <button type="submit">Search</button>
              </form>
              <p className="helper-text">Try: `WP/123/2026`, `Delhi High Court`, or party names.</p>
              <p className="muted">Showing {filteredGraphCases.length} graph cases</p>
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
                    {loadingGraph && (
                      <tr>
                        <td colSpan={3}>Loading graph cases...</td>
                      </tr>
                    )}
                    {!loadingGraph && filteredGraphCases.length === 0 && (
                      <tr>
                        <td colSpan={3}>No cases in current graph view.</td>
                      </tr>
                    )}
                    {!loadingGraph && filteredGraphCases.map((item) => (
                      <tr
                        key={item.id}
                        onClick={() => {
                          setSelectedCaseKey(item.raw_case_key || item.case_key);
                          if (item.raw_case_key) {
                            loadGraph(item.raw_case_key);
                          }
                        }}
                        className={`row-clickable ${selectedCaseKey === (item.raw_case_key || item.case_key) ? 'row-selected' : ''}`}
                      >
                        <td>{item.case_key}</td>
                        <td>{item.court || '-'}</td>
                        <td>{formatDate(item.order_date)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
          </section>
        </main>

        <section className="panel graph-panel">
          <div className="graph-header">
            <h2>Judicial Knowledge Graph</h2>
            <div className="graph-actions">
              <button type="button" onClick={() => setSimplifiedView((v) => !v)}>
                {simplifiedView ? 'Switch to Full View' : 'Switch to Simple View'}
              </button>
            </div>
          </div>
          <p className="muted">
            Nodes: {visibleGraphData.node_count} | Relationships: {visibleGraphData.edge_count}
            {simplifiedView && (
              <> (simplified from {graphData.node_count} / {graphData.edge_count})</>
            )}
          </p>
          {loadingGraph && <p>Loading graph...</p>}
          {!loadingGraph && visibleGraphData.node_count === 0 && (
            <p className="muted">
              No graph loaded yet. Upload a file or select a case to view its graph.
            </p>
          )}
          {!loadingGraph && visibleGraphData.node_count > 0 && (
            <div className="graph-layout">
              <div className="graph-canvas-wrap">
                <GraphViz
                  data={visibleGraphData}
                  height={700}
                  onNodeSelect={handleGraphNodeSelect}
                  backgroundColor={theme === 'theme-dark' ? '#111f3d' : '#eceef5'}
                />
              </div>
              <aside className="graph-side-panel">
                <h3>Selected Node</h3>
                {!selectedGraphNode && (
                  <p className="muted">Click a node to view its details.</p>
                )}
                {selectedGraphNode && (
                  <div className="node-meta">
                    <p>
                      <strong>Type:</strong> {displayNodeType(selectedGraphNode)}
                    </p>
                    <p>
                      <strong>Label:</strong> {displayNodeTitle(selectedGraphNode)}
                    </p>
                    {selectedNodeProperties.length > 0 && (
                      <div className="props-wrap">
                        {selectedNodeProperties.map(([key, value]) => (
                          <p key={key}>
                            <strong>{key}:</strong>{' '}
                            {Array.isArray(value) ? value.join(', ') : String(value ?? '-')}
                          </p>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </aside>
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
    </div>
  );
}

export default App;
