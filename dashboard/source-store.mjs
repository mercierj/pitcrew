const emptyState = () => ({
  data: null,
  stale: false,
  lastSuccess: null,
  error: null,
});

export function createStableRenderGuard(signature = JSON.stringify) {
  let previousSignature;
  return (value, render) => {
    const nextSignature = signature(value);
    if (nextSignature === previousSignature) return false;
    previousSignature = nextSignature;
    render(value);
    return true;
  };
}

export function createSourceStore(now = () => Date.now()) {
  const states = new Map();
  const requestVersions = new Map();

  async function load(name, loader, options = {}) {
    const version = (requestVersions.get(name) ?? 0) + 1;
    requestVersions.set(name, version);
    const previous = states.get(name);

    try {
      const data = await loader();
      options.validate?.(data);
      if (requestVersions.get(name) !== version) {
        return states.get(name) ?? previous ?? emptyState();
      }
      const state = {
        data,
        stale: false,
        lastSuccess: now(),
        error: null,
      };
      states.set(name, state);
      return state;
    } catch (error) {
      if (requestVersions.get(name) !== version) {
        return states.get(name) ?? previous ?? emptyState();
      }
      const state = {
        data: previous?.data ?? null,
        stale: previous?.data != null,
        lastSuccess: previous?.lastSuccess ?? null,
        error: error instanceof Error ? error.message : "service unavailable",
      };
      states.set(name, state);
      return state;
    }
  }

  function get(name) {
    return states.get(name) ?? emptyState();
  }

  return {load, get};
}
