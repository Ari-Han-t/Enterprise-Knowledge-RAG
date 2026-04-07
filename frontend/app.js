const storage = {
  get apiBase() {
    return localStorage.getItem("paperlens_api_base") || "http://localhost:8000";
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

const apiBaseInput = document.getElementById("apiBase");
const authState = document.getElementById("authState");
const uploadState = document.getElementById("uploadState");
const answerOutput = document.getElementById("answerOutput");
const citationOutput = document.getElementById("citationOutput");
const historyOutput = document.getElementById("historyOutput");

apiBaseInput.value = storage.apiBase;
apiBaseInput.addEventListener("change", () => {
  storage.apiBase = apiBaseInput.value.trim();
});

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

async function handleAuth(path, emailId, passwordId) {
  const email = document.getElementById(emailId).value.trim();
  const password = document.getElementById(passwordId).value.trim();
  try {
    const result = await request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    storage.token = result.access_token;
    authState.textContent = `Authenticated as ${result.user.email}`;
    await loadHistory();
  } catch (error) {
    authState.textContent = error.message;
  }
}

document.getElementById("signupBtn").addEventListener("click", () => handleAuth("/auth/signup", "signupEmail", "signupPassword"));
document.getElementById("loginBtn").addEventListener("click", () => handleAuth("/auth/login", "loginEmail", "loginPassword"));
document.getElementById("logoutBtn").addEventListener("click", () => {
  storage.token = "";
  authState.textContent = "Logged out";
  historyOutput.innerHTML = "";
});

document.getElementById("uploadBtn").addEventListener("click", async () => {
  const input = document.getElementById("pdfInput");
  if (!input.files.length) {
    uploadState.textContent = "Choose at least one PDF first.";
    return;
  }

  const formData = new FormData();
  Array.from(input.files).forEach((file) => formData.append("files", file));

  try {
    const result = await request("/upload", {
      method: "POST",
      headers: authHeaders(),
      body: formData,
    });
    uploadState.textContent = `Processed ${result.processed.length} files and added ${result.total_chunks} chunks.`;
  } catch (error) {
    uploadState.textContent = error.message;
  }
});

function renderCitations(citations = []) {
  citationOutput.innerHTML = "";
  citations.forEach((citation) => {
    const div = document.createElement("div");
    div.className = "citation";
    div.textContent = citation.label;
    citationOutput.appendChild(div);
  });
}

async function loadHistory() {
  if (!storage.token) {
    return;
  }
  try {
    const history = await request("/history", { headers: authHeaders() });
    historyOutput.innerHTML = "";
    history.forEach((item) => {
      const div = document.createElement("div");
      div.className = "history-item";
      div.innerHTML = `<strong>${item.question}</strong><p>${item.answer}</p>`;
      historyOutput.appendChild(div);
    });
  } catch (error) {
    historyOutput.textContent = error.message;
  }
}

document.getElementById("historyBtn").addEventListener("click", loadHistory);

document.getElementById("askBtn").addEventListener("click", async () => {
  const question = document.getElementById("questionInput").value.trim();
  answerOutput.textContent = "Thinking...";
  citationOutput.innerHTML = "";

  try {
    const response = await fetch(`${storage.apiBase}/ask/stream`, {
      method: "POST",
      headers: {
        ...authHeaders({ "Content-Type": "application/json" }),
      },
      body: JSON.stringify({ question }),
    });

    if (!response.ok || !response.body) {
      const errorText = await response.text();
      throw new Error(errorText);
    }

    answerOutput.textContent = "";
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop() || "";

      for (const event of events) {
        if (!event.startsWith("data: ")) continue;
        const payload = JSON.parse(event.slice(6));
        if (payload.type === "meta") {
          renderCitations(payload.citations || []);
        }
        if (payload.type === "chunk") {
          answerOutput.textContent += payload.text;
        }
      }
    }

    await loadHistory();
  } catch (error) {
    answerOutput.textContent = error.message;
  }
});

if (storage.token) {
  authState.textContent = "Session restored from local storage.";
  loadHistory();
}

