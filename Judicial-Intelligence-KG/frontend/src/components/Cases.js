import { useEffect, useState } from "react";
import { fetchCases } from "../api";

function Cases({ onSelectCase }) {
  const [cases, setCases] = useState([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const loadCases = async (term = "") => {
    setLoading(true);
    try {
      const res = await fetchCases(term);
      setCases(res.data || []);
      setError("");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadCases();
  }, []);

  const handleSubmit = async (event) => {
    event.preventDefault();
    await loadCases(search);
  };

  return (
    <div className="panel">
      <h2>1. Search Case Files</h2>
      <form className="search-row" onSubmit={handleSubmit}>
        <input
          type="search"
          placeholder="Search case / court / party"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <button type="submit">Search</button>
      </form>

      {loading && <p className="muted">Loading cases...</p>}
      {error && <p className="error-inline">{error}</p>}

      <div className="case-list">
        {cases.map((c) => (
          <button
            key={c.case_key}
            type="button"
            className="case-item"
            onClick={() => onSelectCase(c.case_key)}
          >
            <strong>{c.case_key}</strong>
            <span>{c.courts?.[0] || "Unknown Court"}</span>
          </button>
        ))}
        {!loading && !cases.length && <p className="muted">No case files found.</p>}
      </div>
    </div>
  );
}

export default Cases;

