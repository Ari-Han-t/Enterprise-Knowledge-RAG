bindApiBaseInput("apiBase", "backendState");

if (!requireAuth()) {
  throw new Error("Authentication required");
}

const uploadState = document.getElementById("uploadState");
const answerOutput = document.getElementById("answerOutput");
const citationOutput = document.getElementById("citationOutput");
const historyOutput = document.getElementById("historyOutput");
const sessionLabel = document.getElementById("sessionLabel");

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

document.getElementById("logoutBtn").addEventListener("click", () => {
  storage.token = "";
  window.location.href = "./login.html";
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
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop() || "";

      for (const event of events) {
        if (!event.startsWith("data: ")) {
          continue;
        }
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

validateSession().then((user) => {
  if (!user) {
    window.location.href = "./login.html";
    return;
  }
  if (sessionLabel) {
    sessionLabel.textContent = user.email;
  }
  loadHistory();
});
