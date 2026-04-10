const storage = {
  get apiBase() {
    const savedBase = localStorage.getItem("paperlens_api_base");
    if (savedBase) {
      return savedBase;
    }

    if (window.TRIALSIGHT_CONFIG?.apiBase) {
      return window.TRIALSIGHT_CONFIG.apiBase;
    }

    if (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1") {
      return "http://localhost:8000";
    }

    return window.location.origin;
  },
  set apiBase(value) {
    localStorage.setItem("paperlens_api_base", value);
  },
  get token() {
    return localStorage.getItem("paperlens_token") || "";
  },
  set token(value) {
    if (!value) {
      localStorage.removeItem("paperlens_token");
      return;
    }
    localStorage.setItem("paperlens_token", value);
  },
};

function authHeaders(extra = {}) {
  const headers = { ...extra };
  if (storage.token) {
    headers.Authorization = `Bearer ${storage.token}`;
  }
  return headers;
}

async function request(path, options = {}) {
  const response = await fetch(`${storage.apiBase}${path}`, options);
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    throw new Error(typeof body === "string" ? body : JSON.stringify(body.detail || body));
  }
  return body;
}

function bindApiBaseInput(inputId, statusId) {
  const input = document.getElementById(inputId);
  const status = statusId ? document.getElementById(statusId) : null;

  if (!input) {
    if (status) {
      status.textContent = "Secure evidence workspace";
    }
    return;
  }

  input.value = storage.apiBase;
  input.addEventListener("change", async () => {
    storage.apiBase = input.value.trim();
    if (status) {
      status.textContent = "Secure evidence workspace";
    }
  });
}

async function validateSession() {
  if (!storage.token) {
    return null;
  }
  try {
    return await request("/auth/me", { headers: authHeaders() });
  } catch {
    storage.token = "";
    return null;
  }
}

function requireAuth(redirect = "./login.html") {
  if (!storage.token) {
    window.location.href = redirect;
    return false;
  }
  return true;
}
