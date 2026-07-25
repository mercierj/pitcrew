export function createDetailPanel(dialog, title, content, closeButton) {
  let previousFocus = null;

  function close() {
    if (dialog?.open) {
      dialog.close();
    }
  }

  function open({heading = "Détail", body = "", returnFocusTo = null} = {}) {
    if (!dialog || !title || !content) return;
    if (!dialog.open) {
      previousFocus = returnFocusTo || document.activeElement;
    }
    title.textContent = String(heading);
    content.replaceChildren();
    if (body?.nodeType) {
      content.append(body);
    } else {
      const paragraph = document.createElement("p");
      paragraph.textContent = body == null ? "" : String(body);
      content.append(paragraph);
    }
    if (!dialog.open) {
      dialog.showModal();
    }
    closeButton?.focus();
  }

  closeButton?.addEventListener("click", close);
  dialog?.addEventListener("click", (event) => {
    if (event.target === dialog) {
      close();
    }
  });
  dialog?.addEventListener("close", () => {
    if (
      previousFocus
      && typeof previousFocus.focus === "function"
      && document.contains(previousFocus)
    ) {
      previousFocus.focus();
    }
    previousFocus = null;
  });

  return {open, close};
}
