// Lumida landing page: small, dependency-free interactions.
(() => {
  "use strict";

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const canObserve = "IntersectionObserver" in window;
  document.documentElement.classList.remove("no-js");

  // Navigation: background on scroll, mobile menu ---------------------------------------
  const nav = document.querySelector(".nav");
  const toggle = document.querySelector(".nav__toggle");
  const links = document.getElementById("nav-links");
  const onScroll = () => nav.classList.toggle("is-scrolled", window.scrollY > 8);
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();
  toggle.addEventListener("click", () => {
    const open = links.classList.toggle("is-open");
    toggle.setAttribute("aria-expanded", String(open));
  });
  links.addEventListener("click", (event) => {
    if (event.target.closest("a")) {
      links.classList.remove("is-open");
      toggle.setAttribute("aria-expanded", "false");
    }
  });

  // Hero console: the evidence appears line by line, like a run printing it --------------
  const code = document.querySelector("#console-code code");
  if (code) {
    const rows = code.innerHTML.split("\n").map((row) => `<span class="line">${row || " "}</span>`);
    code.innerHTML = rows.join("");
    const lines = [...code.querySelectorAll(".line")];
    if (reduceMotion) {
      lines.forEach((line) => line.classList.add("is-shown"));
    } else {
      lines.forEach((line, i) => setTimeout(() => line.classList.add("is-shown"), 500 + i * 320));
    }
  }

  // Findings inbox: expand rows, confirm or dismiss -------------------------------------
  const inbox = document.getElementById("inbox");
  inbox.addEventListener("click", (event) => {
    const row = event.target.closest(".finding__row");
    if (row) {
      const body = row.nextElementSibling;
      const open = row.getAttribute("aria-expanded") === "true";
      row.setAttribute("aria-expanded", String(!open));
      body.hidden = open;
      return;
    }
    const action = event.target.closest("[data-act]");
    if (action) {
      const finding = action.closest(".finding");
      finding.classList.add("is-done");
      action.closest(".finding__actions").textContent =
        action.dataset.act === "confirm"
          ? "Confirmed. It stays open until it's fixed."
          : "Marked as normal. It won't be raised again.";
    }
  });

  // "It asks. You answer." --------------------------------------------------------------
  const ask = document.getElementById("ask");
  const result = ask.querySelector(".ask__result");
  const outcomes = {
    yes: "→ rule added: shop.addresses.postal_code is always filled. Checked from tonight.",
    no: "→ noted: empty postal codes are fine here. Nothing will alert on them.",
    skip: "→ skipped. Lumida will ask again in 30 days.",
  };
  ask.addEventListener("click", (event) => {
    const button = event.target.closest("[data-answer]");
    if (button) result.textContent = outcomes[button.dataset.answer];
  });

  // Terminal: type the run out once it scrolls into view --------------------------------
  const terminal = document.getElementById("terminal");
  const termLines = terminal.textContent.split("\n");
  const renderTerminal = (count, caret) => {
    const html = termLines
      .slice(0, count)
      .map((line) =>
        line.startsWith("$")
          ? `<span class="prompt">$</span>${escapeHtml(line.slice(2))}`
          : escapeHtml(line).replace(/\b(ok|healthy)\b/, '<span class="ok">$1</span>'),
      )
      .join("\n");
    terminal.innerHTML = `<code>${html}${caret ? '<span class="caret"></span>' : ""}</code>`;
  };
  if (reduceMotion || !canObserve) {
    renderTerminal(termLines.length, false);
  } else {
    renderTerminal(0, true);
    const typeOut = new IntersectionObserver((entries) => {
      if (!entries.some((e) => e.isIntersecting)) return;
      typeOut.disconnect();
      let shown = 0;
      const next = () => {
        shown += 1;
        renderTerminal(shown, shown < termLines.length);
        if (shown < termLines.length) {
          const isCommand = termLines[shown] && termLines[shown].startsWith("$");
          setTimeout(next, isCommand ? 900 : 300);
        }
      };
      next();
    }, { threshold: 0.5 });
    typeOut.observe(terminal);
  }

  // Reveal sections as they scroll in ---------------------------------------------------
  // Anything that has come into view, or was scrolled past quickly, is shown.
  let hidden = [...document.querySelectorAll(".reveal")];
  if (reduceMotion) {
    hidden.forEach((el) => el.classList.add("is-visible"));
  } else {
    let queued = false;
    const revealSeen = () => {
      queued = false;
      const fold = window.innerHeight * 0.92;
      hidden = hidden.filter((el) => {
        if (el.getBoundingClientRect().top > fold) return true;
        el.classList.add("is-visible");
        return false;
      });
      if (!hidden.length) window.removeEventListener("scroll", onReveal);
    };
    const onReveal = () => {
      if (!queued) {
        queued = true;
        requestAnimationFrame(revealSeen);
      }
    };
    window.addEventListener("scroll", onReveal, { passive: true });
    window.addEventListener("resize", onReveal, { passive: true });
    revealSeen();
  }

  // Early access: no sign-up backend yet, so say so honestly ----------------------------
  // Set this to a form endpoint (e.g. Netlify Forms, Formspree, or your own API) when ready.
  const EARLY_ACCESS_ENDPOINT = "";
  const form = document.getElementById("signup");
  const message = document.getElementById("signup-msg");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = form.email.value.trim();
    message.classList.remove("is-error");
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      message.textContent = "Please enter a valid work email.";
      message.classList.add("is-error");
      return;
    }
    if (!EARLY_ACCESS_ENDPOINT) {
      message.textContent = "Sign-ups open soon. This page is a preview and doesn't store emails yet.";
      return;
    }
    try {
      const response = await fetch(EARLY_ACCESS_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ email }),
      });
      if (!response.ok) throw new Error(String(response.status));
      form.reset();
      message.textContent = "Thanks! We'll be in touch.";
    } catch {
      message.textContent = "That didn't go through. Please try again in a moment.";
      message.classList.add("is-error");
    }
  });

  document.getElementById("year").textContent = String(new Date().getFullYear());

  function escapeHtml(text) {
    return text.replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  }
})();
