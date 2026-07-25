export function createNavigation(root, views, { windowObject = window } = {}) {
  const buttons = Array.from(root.querySelectorAll("[data-view-target]"));

  function show(name, { focus = true, historyMode = "replace" } = {}) {
    if (!Object.hasOwn(views, name) || !views[name]) {
      return;
    }

    Object.entries(views).forEach(([viewName, view]) => {
      view.hidden = viewName !== name;
    });
    buttons.forEach((button) => {
      if (button.dataset.viewTarget === name) {
        button.setAttribute("aria-current", "page");
      } else {
        button.removeAttribute("aria-current");
      }
    });
    if (historyMode === "push") {
      windowObject.history.pushState(null, "", `#${name}`);
    } else if (historyMode === "replace") {
      windowObject.history.replaceState(null, "", `#${name}`);
    }

    if (focus) {
      views[name].querySelector("h1")?.focus({ preventScroll: true });
    }
  }

  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      show(button.dataset.viewTarget, {historyMode: "push"});
    });
  });

  function restoreFromHash() {
    const hashView = windowObject.location.hash.slice(1);
    const viewName = Object.hasOwn(views, hashView) ? hashView : "pilotage";
    show(viewName, {focus: false, historyMode: "none"});
  }

  windowObject.addEventListener("popstate", restoreFromHash);
  windowObject.addEventListener("hashchange", restoreFromHash);

  const hashView = windowObject.location.hash.slice(1);
  const initialView = Object.hasOwn(views, hashView) ? hashView : "pilotage";
  show(initialView, {focus: false, historyMode: "replace"});

  return { show };
}
