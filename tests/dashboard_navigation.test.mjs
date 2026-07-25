import assert from "node:assert/strict";
import test from "node:test";

import {createNavigation} from "../dashboard/navigation.mjs";

class FakeHeading {
  constructor() {
    this.focusCalls = [];
  }

  focus(options) {
    this.focusCalls.push(options);
  }
}

class FakeElement {
  constructor({viewTarget = null, heading = null} = {}) {
    this.attributes = new Map();
    this.dataset = viewTarget ? {viewTarget} : {};
    this.heading = heading;
    this.hidden = false;
    this.listeners = new Map();
  }

  addEventListener(name, listener) {
    this.listeners.set(name, listener);
  }

  click() {
    this.listeners.get("click")?.();
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  querySelector(selector) {
    return selector === "h1" ? this.heading : null;
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  setAttribute(name, value) {
    this.attributes.set(name, value);
  }
}

function fixture(hash = "") {
  const buttons = Object.fromEntries(
    ["pilotage", "agents", "history"].map((name) => [
      name,
      new FakeElement({viewTarget: name}),
    ]),
  );
  const headings = Object.fromEntries(
    ["pilotage", "agents", "history"].map((name) => [
      name,
      new FakeHeading(),
    ]),
  );
  const views = Object.fromEntries(
    ["pilotage", "agents", "history"].map((name) => [
      name,
      new FakeElement({heading: headings[name]}),
    ]),
  );
  const root = {
    querySelectorAll(selector) {
      assert.equal(selector, "[data-view-target]");
      return Object.values(buttons);
    },
  };
  const listeners = new Map();
  const historyCalls = {
    push: [],
    replace: [],
  };
  const windowObject = {
    location: {hash},
    history: {
      pushState(...args) {
        historyCalls.push.push(args);
        windowObject.location.hash = args[2];
      },
      replaceState(...args) {
        historyCalls.replace.push(args);
        windowObject.location.hash = args[2];
      },
    },
    addEventListener(name, listener) {
      const eventListeners = listeners.get(name) ?? [];
      eventListeners.push(listener);
      listeners.set(name, eventListeners);
    },
    dispatch(name) {
      for (const listener of listeners.get(name) ?? []) {
        listener();
      }
    },
  };

  return {buttons, headings, historyCalls, root, views, windowObject};
}

function assertActive(fakes, activeName) {
  for (const name of ["pilotage", "agents", "history"]) {
    assert.equal(fakes.views[name].hidden, name !== activeName);
    assert.equal(
      fakes.buttons[name].getAttribute("aria-current"),
      name === activeName ? "page" : null,
    );
  }
}

test("uses a valid initial hash without focusing the view", () => {
  const fakes = fixture("#agents");

  createNavigation(fakes.root, fakes.views, {windowObject: fakes.windowObject});

  assertActive(fakes, "agents");
  assert.deepEqual(fakes.historyCalls.replace, [[null, "", "#agents"]]);
  assert.deepEqual(fakes.historyCalls.push, []);
  for (const heading of Object.values(fakes.headings)) {
    assert.deepEqual(heading.focusCalls, []);
  }
});

test("falls back to pilotage for an invalid initial hash", () => {
  const fakes = fixture("#unknown");

  createNavigation(fakes.root, fakes.views, {windowObject: fakes.windowObject});

  assertActive(fakes, "pilotage");
  assert.deepEqual(fakes.historyCalls.replace, [[null, "", "#pilotage"]]);
  assert.deepEqual(fakes.historyCalls.push, []);
});

test("pushes and focuses the History view after a click", () => {
  const fakes = fixture();
  createNavigation(fakes.root, fakes.views, {windowObject: fakes.windowObject});

  fakes.buttons.history.click();

  assertActive(fakes, "history");
  assert.deepEqual(fakes.historyCalls.push, [[null, "", "#history"]]);
  assert.deepEqual(
    fakes.headings.history.focusCalls,
    [{preventScroll: true}],
  );
});

test("restores the hash view on popstate without history writes or focus", () => {
  const fakes = fixture();
  createNavigation(fakes.root, fakes.views, {windowObject: fakes.windowObject});
  const writesBefore = structuredClone(fakes.historyCalls);

  fakes.windowObject.location.hash = "#agents";
  fakes.windowObject.dispatch("popstate");

  assertActive(fakes, "agents");
  assert.deepEqual(fakes.historyCalls, writesBefore);
  assert.deepEqual(fakes.headings.agents.focusCalls, []);
});

test("restores pilotage for an invalid hashchange without a history write", () => {
  const fakes = fixture("#history");
  createNavigation(fakes.root, fakes.views, {windowObject: fakes.windowObject});
  const writesBefore = structuredClone(fakes.historyCalls);

  fakes.windowObject.location.hash = "#missing";
  fakes.windowObject.dispatch("hashchange");

  assertActive(fakes, "pilotage");
  assert.deepEqual(fakes.historyCalls, writesBefore);
  assert.deepEqual(fakes.headings.pilotage.focusCalls, []);
});
