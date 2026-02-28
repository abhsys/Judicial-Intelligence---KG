import { useCallback, useEffect, useMemo, useState } from 'react';
import './App.css';
import GraphViz from './components/GraphViz';
import {
  getIngestionGraph,
  getIngestionJob,
  getUploadDetails,
  searchIndianKanoon,
  uploadCaseFile,
} from './api';

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

function pickNodeDocumentUrl(node) {
  const props = node?.properties || {};
  if (typeof props.document_url === 'string' && props.document_url.trim()) {
    return props.document_url.trim();
  }
  if (typeof props.result_url === 'string' && props.result_url.trim()) {
    return props.result_url.trim();
  }
  return '';
}

function pickNodeResultUrl(node, graphData) {
  const props = node?.properties || {};
  if (typeof props.result_url === 'string' && props.result_url.trim()) {
    return props.result_url.trim();
  }

  const nodeId = node?.id;
  if (!nodeId) {
    return '';
  }

  const nodes = graphData?.nodes || [];
  const edges = graphData?.edges || [];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  for (const edge of edges) {
    const sourceId = typeof edge.source === 'object' ? edge.source.id : edge.source;
    const targetId = typeof edge.target === 'object' ? edge.target.id : edge.target;
    if (sourceId !== nodeId && targetId !== nodeId) {
      continue;
    }
    const otherId = sourceId === nodeId ? targetId : sourceId;
    const otherNode = byId.get(otherId);
    const otherProps = otherNode?.properties || {};
    if (typeof otherProps.result_url === 'string' && otherProps.result_url.trim()) {
      return otherProps.result_url.trim();
    }
  }

  return '';
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
  const [keywordResults, setKeywordResults] = useState([]);
  const [searchingKeywords, setSearchingKeywords] = useState(false);
  const [loadingSummary, setLoadingSummary] = useState(false);
  const [loadingGraph, setLoadingGraph] = useState(false);
  const [error, setError] = useState('');
  const [graphData, setGraphData] = useState({ nodes: [], edges: [], node_count: 0, edge_count: 0 });
  const [selectedGraphNode, setSelectedGraphNode] = useState(null);
  const [simplifiedView, setSimplifiedView] = useState(true);
  const [uploadFile, setUploadFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [dragActive, setDragActive] = useState(false);
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
    const withHint = (id, label, className) => {
      const metric = byId.get(id) || {};
      const count = Number(metric.count || 0);
      const rolling = Number(metric.rolling_3d_count || 0);
      const hint = count === 0 && rolling > 0 ? `3-day rolling signal: ${rolling}` : '';
      return {
        label,
        value: count,
        className,
        hint,
      };
    };
    return [
      withHint('appeared_yesterday', 'Appeared Yesterday', 'card-cases'),
      withHint('resolved_yesterday', 'Resolved Yesterday', 'card-courts'),
      withHint('delayed_time_yesterday', 'Pushed Due To Delay', 'card-parties'),
      withHint('next_hearing_yesterday', 'Pushed To Next Hearing', 'card-orders'),
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
  };

  const visibleGraphData = useMemo(
    () => (simplifiedView ? simplifyGraphData(graphData) : graphData),
    [graphData, simplifiedView],
  );
  const selectedNodeProperties = useMemo(() => {
    if (!selectedGraphNode?.properties) {
      return [];
    }
    return Object.entries(selectedGraphNode.properties).filter(
      ([key]) => !['full_text', 'text', 'content'].includes(String(key).toLowerCase()),
    );
  }, [selectedGraphNode]);
  const selectedNodeResultUrl = useMemo(
    () => pickNodeResultUrl(selectedGraphNode, graphData),
    [selectedGraphNode, graphData],
  );
  const selectedNodeDocUrl = useMemo(() => pickNodeDocumentUrl(selectedGraphNode), [selectedGraphNode]);

  const handleSearch = async (event) => {
    event.preventDefault();
    const keyword = query.trim();
    if (!keyword) {
      setKeywordResults([]);
      return;
    }
    try {
      setSearchingKeywords(true);
      const response = await searchIndianKanoon(keyword, 10);
      setKeywordResults(response?.data || []);
      setError('');
    } catch (err) {
      setError(err.message);
      setKeywordResults([]);
    } finally {
      setSearchingKeywords(false);
    }
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
      setUploadModalOpen(false);
      setError('');
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
    }
  };

  const handleUploadFileChange = (event) => {
    setUploadFile(event.target.files?.[0] || null);
  };

  const handleDropZoneDragOver = (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (!dragActive) {
      setDragActive(true);
    }
  };

  const handleDropZoneDragLeave = (event) => {
    event.preventDefault();
    event.stopPropagation();
    setDragActive(false);
  };

  const handleDropZoneDrop = (event) => {
    event.preventDefault();
    event.stopPropagation();
    setDragActive(false);
    const file = event.dataTransfer?.files?.[0] || null;
    if (file) {
      setUploadFile(file);
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
          let uploadTitle = `Upload: ${String(intakeJob.job_id).slice(0, 8)}`;
          const uploadId = String(status.upload_id || '').trim();
          if (uploadId) {
            try {
              const details = await getUploadDetails(uploadId);
              const filename = String(details?.filename || '').trim();
              if (filename) {
                uploadTitle = `Upload: ${filename}`;
              } else {
                uploadTitle = `Upload: ${uploadId.slice(0, 8)}`;
              }
            } catch (_) {
              uploadTitle = `Upload: ${uploadId.slice(0, 8)}`;
            }
          }
          setGraphData(nextGraph);
          addGraphToHistory(nextGraph, {
            title: uploadTitle,
          });
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
          <article className={`card ${card.className}`} key={card.label} title={card.hint || ''}>
            <p className="card-label">{card.label}</p>
            <p className="card-value">{card.value}</p>
            {card.hint && <p className="card-hint">{card.hint}</p>}
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
      {uploadModalOpen && (
        <button
          type="button"
          className="upload-modal-backdrop"
          aria-label="Close upload dialog"
          onClick={() => setUploadModalOpen(false)}
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

      {uploadModalOpen && (
        <section className="upload-modal" role="dialog" aria-modal="true" aria-label="Upload case file">
          <div className="upload-modal-head">
            <h3>Upload Case File</h3>
            <button type="button" className="tone-btn history-clear" onClick={() => setUploadModalOpen(false)}>
              Close
            </button>
          </div>
          <form className="upload-modal-form" onSubmit={handleUploadSubmit}>
            <div
              className={`upload-dropzone ${dragActive ? 'drag-active' : ''}`}
              onDragOver={handleDropZoneDragOver}
              onDragEnter={handleDropZoneDragOver}
              onDragLeave={handleDropZoneDragLeave}
              onDrop={handleDropZoneDrop}
            >
              <p className="upload-dropzone-title">Drag and drop a PDF or TXT file here</p>
              <p className="muted upload-dropzone-or">or</p>
              <label className="upload-select-btn">
                Select File
                <input type="file" accept=".pdf,.txt" onChange={handleUploadFileChange} />
              </label>
              <p className="upload-file-name">{uploadFile?.name || 'No file selected'}</p>
            </div>
            <div className="upload-modal-actions">
              <button type="button" className="tone-btn" onClick={() => setUploadModalOpen(false)}>
                Cancel
              </button>
              <button type="submit" className="primary-btn" disabled={!uploadFile || uploading}>
                {uploading ? 'Uploading...' : 'Upload & Build Graph'}
              </button>
            </div>
          </form>
        </section>
      )}

      <div className="main-stack">
        <main className="content-grid">
          <section className="panel case-panel">
              <div className="case-panel-head">
                <h2>Case Explorer</h2>
                <button type="button" className="primary-btn case-upload-btn" onClick={() => setUploadModalOpen(true)}>
                  Upload PDF/TXT
                </button>
              </div>
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
                  placeholder="Search keyword in IndianKanoon"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
                <button type="submit" disabled={searchingKeywords}>
                  {searchingKeywords ? 'Searching...' : 'Search'}
                </button>
              </form>
              <p className="helper-text">Type a legal keyword and fetch top 10 IndianKanoon results.</p>
              <p className="muted">Showing {keywordResults.length} results</p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Title</th>
                      <th>Court</th>
                      <th>Date</th>
                      <th>Source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {searchingKeywords && (
                      <tr>
                        <td colSpan={4}>Searching IndianKanoon...</td>
                      </tr>
                    )}
                    {!searchingKeywords && keywordResults.length === 0 && (
                      <tr>
                        <td colSpan={4}>No results yet. Search with a keyword.</td>
                      </tr>
                    )}
                    {!searchingKeywords && keywordResults.map((item, idx) => (
                      <tr key={`${item.result_url || item.title}-${idx}`}>
                        <td>{item.title || '-'}</td>
                        <td>{item.court || '-'}</td>
                        <td>{formatDate(item.date)}</td>
                        <td>
                          {item.result_url ? (
                            <a href={item.result_url} target="_blank" rel="noreferrer">
                              Open
                            </a>
                          ) : (
                            '-'
                          )}
                        </td>
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
                    {selectedNodeResultUrl && (
                      <p>
                        <a href={selectedNodeResultUrl} target="_blank" rel="noreferrer">
                          Open Case (IndianKanoon)
                        </a>
                      </p>
                    )}
                    {selectedNodeDocUrl && (
                      <p>
                        <a href={selectedNodeDocUrl} target="_blank" rel="noreferrer">
                          Open Document
                        </a>
                      </p>
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
