export function createNavigation(root, views, { windowObject = window } = {}) {
  const buttons = Array.from(root.querySelectorAll("[data-view-target]"));

  function show(name, { focus = true } = {}) {
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
    windowObject.history.replaceState(null, "", `#${name}`);

    if (focus) {
      views[name].querySelector("h1")?.focus({ preventScroll: true });
    }
  }

  buttons.forEach((button) => {
    button.addEventListener("click", () => {
      show(button.dataset.viewTarget);
    });
  });

  const hashView = windowObject.location.hash.slice(1);
  const initialView = Object.hasOwn(views, hashView) ? hashView : "pilotage";
  show(initialView, { focus: false });

  return { show };
}
