export function createDetailPanel(dialog, title, content, closeButton, {onOpen, onClose} = {}) {
  let previousFocus = null;
  let previousFocusKey = null;
  let closeRequested = false;

  function close() {
    if (dialog?.open) {
      closeRequested = true;
      dialog.close();
    }
  }

  function open({heading = "Détail", body = "", returnFocusTo = null} = {}) {
    if (!dialog || !title || !content) return;
    if (!dialog.open) {
      previousFocus = returnFocusTo || document.activeElement;
      previousFocusKey = previousFocus?.getAttribute?.("data-focus-key") || null;
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
      onOpen?.();
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
    if (!closeRequested) {
      dialog.showModal();
      closeButton?.focus();
      return;
    }
    closeRequested = false;
    let focusTarget = null;
    if (
      previousFocus
      && typeof previousFocus.focus === "function"
      && document.contains(previousFocus)
    ) {
      previousFocus.focus();
    } else if (previousFocusKey) {
      focusTarget = [...document.querySelectorAll("[data-focus-key]")]
        .find((control) => (
          control.getAttribute("data-focus-key") === previousFocusKey
          && document.contains(control)
          && typeof control.focus === "function"
        )) || null;
      focusTarget?.focus();
    }
    previousFocus = null;
    previousFocusKey = null;
    onClose?.();
  });

  return {open, close};
}
