bindApiBaseInput("apiBase", "backendState");

const authState = document.getElementById("authState");
const loginForm = document.getElementById("loginForm");
const signupForm = document.getElementById("signupForm");
const showLoginBtn = document.getElementById("showLoginBtn");
const showSignupBtn = document.getElementById("showSignupBtn");

function setTab(mode) {
  if (!loginForm || !signupForm || !showLoginBtn || !showSignupBtn) {
    return;
  }
  const loginActive = mode === "login";
  loginForm.classList.toggle("hidden", !loginActive);
  signupForm.classList.toggle("hidden", loginActive);
  showLoginBtn.classList.toggle("active", loginActive);
  showSignupBtn.classList.toggle("active", !loginActive);
  showLoginBtn.classList.toggle("ghost-tab", !loginActive);
  showSignupBtn.classList.toggle("ghost-tab", loginActive);
  if (authState) {
    authState.textContent = "";
  }
}

if (showLoginBtn) {
  showLoginBtn.addEventListener("click", () => setTab("login"));
}
if (showSignupBtn) {
  showSignupBtn.addEventListener("click", () => setTab("signup"));
}

setTab("login");

async function handleAuth(path, emailId, passwordId) {
  const emailInput = document.getElementById(emailId);
  const passwordInput = document.getElementById(passwordId);
  if (!emailInput || !passwordInput) {
    return;
  }

  const email = emailInput.value.trim();
  const password = passwordInput.value.trim();
  try {
    const result = await request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    storage.token = result.access_token;
    if (authState) {
      authState.textContent = `Authenticated as ${result.user.email}. Redirecting...`;
    }
    window.location.href = "./chat.html";
  } catch (error) {
    if (authState) {
      authState.textContent = error.message;
    }
  }
}

const loginBtn = document.getElementById("loginBtn");
const signupBtn = document.getElementById("signupBtn");
if (loginBtn) {
  loginBtn.addEventListener("click", () => handleAuth("/auth/login", "loginEmail", "loginPassword"));
}
if (signupBtn) {
  signupBtn.addEventListener("click", () => handleAuth("/auth/signup", "signupEmail", "signupPassword"));
}

validateSession().then((user) => {
  if (user) {
    window.location.href = "./chat.html";
  }
});
