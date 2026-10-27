"use strict";
// Timestral card: while the worker computes a period, fetch the page again and swap in the card.
document.addEventListener("DOMContentLoaded", () => {
  const card = document.getElementById("timestral");
  if (!card || !card.dataset.pending) return;
  const started = Date.now();
  const clock = setInterval(() => {
    const elapsed = card.querySelector("[data-elapsed]");
    if (elapsed) elapsed.textContent = ` (${Math.round((Date.now() - started) / 1000)} s)`;
  }, 1000);

  async function poll() {
    try {
      const response = await fetch(window.location.href, { credentials: "same-origin" });
      const html = response.ok ? await response.text() : "";
      const fresh = new DOMParser().parseFromString(html, "text/html").getElementById("timestral");
      if (fresh && !fresh.dataset.pending) {
        clearInterval(clock);
        card.innerHTML = fresh.innerHTML;
        delete card.dataset.pending;
        return;
      }
    } catch (error) {
      // A network hiccup: the next poll tries again.
    }
    setTimeout(poll, 3000);
  }
  setTimeout(poll, 3000);
});
