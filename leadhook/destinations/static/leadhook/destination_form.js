"use strict";
// Destination form: show only what the chosen kind and authentication use.
document.addEventListener("DOMContentLoaded", () => {
  const kind = document.getElementById("id_kind");
  const auth = document.getElementById("id_auth_type");
  if (!kind || !auth) return;
  const show = (element, visible) => { if (element) element.hidden = !visible; };
  const secretLabel = document.querySelector("label[for=id_secret]");
  const secretText = secretLabel ? secretLabel.textContent : "";

  function sync() {
    const telegram = kind.value === "telegram";
    document.querySelectorAll(".lh-kind-webhook").forEach((el) => show(el, !telegram));
    document.querySelectorAll(".lh-kind-telegram").forEach((el) => show(el, telegram));
    show(document.getElementById("field_mappings-group"), !telegram);
    show(document.querySelector(".field-auth_header_name"), auth.value === "header");
    // The secret is the bot token for Telegram, otherwise only needed with authentication.
    show(document.querySelector(".field-secret")?.closest("fieldset"), telegram || auth.value !== "none");
    if (secretLabel) secretLabel.textContent = telegram ? "Token del bot:" : secretText;
  }

  kind.addEventListener("change", sync);
  auth.addEventListener("change", sync);
  sync();
});

// Telegram message: clicking a field chip inserts its {placeholder} at the cursor.
document.addEventListener("click", (event) => {
  const chip = event.target.closest(".lh-chip[data-insert]");
  const message = document.getElementById("id_message_template");
  if (!chip || !message) return;
  const start = message.selectionStart ?? message.value.length;
  const end = message.selectionEnd ?? start;
  message.setRangeText(chip.dataset.insert, start, end, "end");
  message.focus();
});

// Show the question chips of the form picked in the Form menu.
document.addEventListener("change", (event) => {
  if (!event.target.matches(".lh-insert-form")) return;
  document.querySelectorAll(".lh-chips[data-form]").forEach((chips) => {
    chips.hidden = chips.dataset.form !== event.target.value;
  });
});

// Telegram message: highlight {placeholders} behind the textarea (indigo known, red unknown).
document.addEventListener("DOMContentLoaded", () => {
  const message = document.getElementById("id_message_template");
  if (!message) return;
  const wrap = document.createElement("div");
  wrap.className = "lh-highlight";
  const backdrop = document.createElement("div");
  backdrop.className = "lh-highlight-backdrop";
  backdrop.setAttribute("aria-hidden", "true");
  message.parentNode.insertBefore(wrap, message);
  wrap.append(backdrop, message);
  const style = getComputedStyle(message);
  for (const property of ["font", "letterSpacing", "lineHeight", "padding", "borderWidth", "borderStyle", "boxSizing", "width", "height"]) {
    backdrop.style[property] = style[property];
  }
  const known = new Set(
    [...document.querySelectorAll(".lh-chip[data-insert]")].map((chip) => chip.dataset.insert.slice(1, -1)),
  );
  const escape = (text) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  function paint() {
    // Same pattern as the server: {name} or {{ name }}.
    backdrop.innerHTML = escape(message.value).replace(/\{\{?\s*([^{}\n]+?)\s*\}?\}/g, (match, name) =>
      `<mark class="${known.has(name) ? "" : "lh-unknown"}">${match}</mark>`,
    ) + "\n";
    backdrop.scrollTop = message.scrollTop;
  }
  message.addEventListener("input", paint);
  message.addEventListener("scroll", () => { backdrop.scrollTop = message.scrollTop; });
  new ResizeObserver(() => { backdrop.style.width = getComputedStyle(message).width; backdrop.style.height = getComputedStyle(message).height; }).observe(message);
  document.addEventListener("click", (event) => { if (event.target.closest(".lh-chip")) paint(); });
  paint();
});
