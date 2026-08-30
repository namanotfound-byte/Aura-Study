/*
 * AuraStudy auth pages -- form handling for login / register / forgot / reset.
 * Driven by `window.AURA_AUTH_MODE`, set inline by each template.
 * Plain ES2018 browser JS, no build step.
 */
(function () {
  "use strict";

  var mode = window.AURA_AUTH_MODE;
  var form = document.getElementById("auth-form");
  var alertBox = document.getElementById("form-alert");
  var submitBtn = document.getElementById("submit-btn");

  function showAlert(msg, kind) {
    if (!alertBox) return;
    alertBox.textContent = msg;
    alertBox.hidden = false;
    alertBox.className = "auth-alert" + (kind ? " auth-alert-" + kind : "");
  }

  function hideAlert() {
    if (alertBox) alertBox.hidden = true;
  }

  function fieldError(name, msg) {
    if (!form) return;
    var el = form.querySelector('[data-for="' + name + '"]');
    if (el) el.textContent = msg || "";
    var input = form.querySelector("#" + name);
    if (input) input.classList.toggle("is-invalid", !!msg);
  }

  function clearFieldErrors() {
    if (!form) return;
    var errs = form.querySelectorAll(".auth-field-error");
    for (var i = 0; i < errs.length; i++) errs[i].textContent = "";
    var inputs = form.querySelectorAll("input");
    for (var j = 0; j < inputs.length; j++) inputs[j].classList.remove("is-invalid");
  }

  function setLoading(loading) {
    if (!submitBtn) return;
    submitBtn.disabled = loading;
    var spinner = submitBtn.querySelector(".auth-spinner");
    var label = submitBtn.querySelector(".auth-submit-label");
    if (spinner) spinner.hidden = !loading;
    if (label) label.style.opacity = loading ? "0.6" : "1";
  }

  function apiFetch(path, body) {
    return fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify(body || {}),
    }).then(function (res) {
      return res
        .json()
        .catch(function () {
          return {};
        })
        .then(function (data) {
          return { ok: res.ok, status: res.status, data: data };
        });
    });
  }

  // The `next` query param on /login is attacker-controlled (it's just part
  // of the URL a phishing link can set) and was previously handed straight
  // to `window.location.href`, which happily navigates to an absolute URL on
  // another origin -- or, worse, a `javascript:` URI, which would execute in
  // this page's own origin right after a real, successful login. Only a
  // same-origin, root-relative path (single leading slash, not `//host/...`
  // or `/\host/...`, both of which browsers treat as protocol-relative) is
  // ever honoured; anything else falls back to "/".
  function safeNextPath(raw) {
    if (typeof raw !== "string" || !raw) return "/";
    if (!/^\/(?!\/|\\)/.test(raw)) return "/";
    return raw;
  }

  function emailClientError(email) {
    if (!email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
      return "Enter a valid email address.";
    }
    return null;
  }

  function passwordClientError(pw) {
    if (!pw || pw.length < 8) return "Password must be at least 8 characters.";
    if (!/[a-zA-Z]/.test(pw)) return "Password must contain at least one letter.";
    if (!/[0-9]/.test(pw)) return "Password must contain at least one digit.";
    return null;
  }

  // Show/hide password toggles.
  var toggles = document.querySelectorAll("[data-toggle-for]");
  for (var t = 0; t < toggles.length; t++) {
    toggles[t].addEventListener("click", function (e) {
      var btn = e.currentTarget;
      var input = document.getElementById(btn.getAttribute("data-toggle-for"));
      if (!input) return;
      var showing = input.type === "text";
      input.type = showing ? "password" : "text";
      var icon = btn.querySelector("i");
      if (icon) icon.setAttribute("data-lucide", showing ? "eye" : "eye-off");
      if (window.lucide) lucide.createIcons();
    });
  }

  var resendBtn = document.getElementById("resend-btn");
  if (resendBtn) {
    resendBtn.addEventListener("click", function () {
      var resendBox = document.getElementById("resend-box");
      var emailInput = document.getElementById("email");
      var email = (resendBox && resendBox.dataset.email) || (emailInput ? emailInput.value : "");
      if (!email) return;
      resendBtn.disabled = true;
      apiFetch("/api/auth/resend-verification", { email: email })
        .then(function () {
          showAlert("Verification email sent! Check your inbox.", "success");
        })
        .finally(function () {
          resendBtn.disabled = false;
        });
    });
  }

  if (!form) return;

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    hideAlert();
    clearFieldErrors();
    var resendBox = document.getElementById("resend-box");
    if (resendBox) resendBox.hidden = true;

    var data = new FormData(form);

    if (mode === "login") {
      var email = (data.get("email") || "").trim();
      var password = data.get("password") || "";
      var emailErr = emailClientError(email);
      if (emailErr) return fieldError("email", emailErr);
      if (!password) return fieldError("password", "Password is required.");

      setLoading(true);
      apiFetch("/api/auth/login", { email: email, password: password })
        .then(function (r) {
          setLoading(false);
          if (r.ok) {
            var params = new URLSearchParams(window.location.search);
            window.location.href = safeNextPath(params.get("next"));
            return;
          }
          var code = r.data.error;
          if (code === "email_unverified") {
            showAlert(r.data.message || "Please verify your email first.", null);
            if (resendBox) {
              resendBox.hidden = false;
              resendBox.dataset.email = email;
            }
          } else {
            showAlert(r.data.message || "Incorrect email or password.", null);
          }
        })
        .catch(function () {
          setLoading(false);
          showAlert("Something went wrong. Please try again.", null);
        });
    } else if (mode === "register") {
      var rEmail = (data.get("email") || "").trim();
      var rPassword = data.get("password") || "";
      var displayName = (data.get("display_name") || "").trim();
      var rEmailErr = emailClientError(rEmail);
      var rPwErr = passwordClientError(rPassword);
      if (rEmailErr) fieldError("email", rEmailErr);
      if (rPwErr) fieldError("password", rPwErr);
      if (rEmailErr || rPwErr) return;

      setLoading(true);
      apiFetch("/api/auth/register", {
        email: rEmail,
        password: rPassword,
        display_name: displayName || undefined,
      })
        .then(function (r) {
          setLoading(false);
          if (r.ok) {
            showAlert(r.data.message || "Check your email to confirm your account!", "success");
            form.reset();
          } else {
            showAlert(r.data.message || "Something went wrong.", null);
          }
        })
        .catch(function () {
          setLoading(false);
          showAlert("Something went wrong. Please try again.", null);
        });
    } else if (mode === "forgot") {
      var fEmail = (data.get("email") || "").trim();
      var fEmailErr = emailClientError(fEmail);
      if (fEmailErr) return fieldError("email", fEmailErr);

      setLoading(true);
      apiFetch("/api/auth/forgot-password", { email: fEmail })
        .then(function () {
          setLoading(false);
          var successBox = document.getElementById("success-box");
          if (successBox) successBox.hidden = false;
          form.reset();
        })
        .catch(function () {
          setLoading(false);
          showAlert("Something went wrong. Please try again.", null);
        });
    } else if (mode === "reset") {
      var token = data.get("token") || "";
      var newPassword = data.get("password") || "";
      var nPwErr = passwordClientError(newPassword);
      if (nPwErr) return fieldError("password", nPwErr);

      setLoading(true);
      apiFetch("/api/auth/reset-password", { token: token, password: newPassword })
        .then(function (r) {
          setLoading(false);
          if (r.ok) {
            showAlert("Password updated! Redirecting you to log in...", "success");
            setTimeout(function () {
              window.location.href = "/login";
            }, 1200);
          } else {
            showAlert(r.data.message || "This link is invalid or expired.", null);
          }
        })
        .catch(function () {
          setLoading(false);
          showAlert("Something went wrong. Please try again.", null);
        });
    }
  });
})();
