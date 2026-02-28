const API_BASE_URL = process.env.REACT_APP_API_BASE_URL || "http://127.0.0.1:8000";

async function request(path, options = {}) {
  const isFormData = options.body instanceof FormData;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: isFormData
      ? {
          ...(options.headers || {}),
        }
      : {
          "Content-Type": "application/json",
          ...(options.headers || {}),
        },
    ...options,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }

  return response.json();
}

export const fetchCases = async (search = "") => {
  const params = new URLSearchParams();
  params.set("limit", "50");
  if (search.trim()) {
    params.set("query", search.trim());
  }
  return request(`/api/cases?${params.toString()}`);
};

export const buildGraph = async () =>
  request("/api/graph/build", {
    method: "POST",
    body: JSON.stringify({}),
  });

export const fetchCaseGraph = async (caseKey) => {
  const params = new URLSearchParams();
  params.set("case_key", caseKey);
  params.set("limit_cases", "40");
  return request(`/api/graph/network?${params.toString()}`);
};

export const uploadCaseFile = async (file) => {
  const body = new FormData();
  body.append("file", file);
  return request("/api/intake/upload", {
    method: "POST",
    body,
  });
};

export const getIngestionJob = async (jobId) =>
  request(`/api/intake/jobs/${encodeURIComponent(jobId)}`);

export const getIngestionGraph = async (jobId) =>
  request(`/api/intake/jobs/${encodeURIComponent(jobId)}/graph`);

export const getUploadDetails = async (uploadId) =>
  request(`/api/intake/uploads/${encodeURIComponent(uploadId)}/details`);

export const searchIndianKanoon = async (keyword, limit = 10) => {
  const params = new URLSearchParams();
  params.set("q", keyword);
  params.set("limit", String(limit));
  return request(`/api/indiankanoon/search?${params.toString()}`);
};
