export function createApi(sessionToken, fetchImpl = globalThis.fetch) {
  async function request(path, options = {}) {
    const response = await fetchImpl(path, {
      cache: "no-store",
      credentials: "same-origin",
      ...options,
    });
    if (!response.ok) {
      throw new Error(`Requête refusée (${response.status})`);
    }
    return response.json();
  }

  function get(path, options = {}) {
    return request(path, options);
  }

  function post(path, body) {
    return request(path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Pitcrew-Session": sessionToken,
      },
      body: JSON.stringify(body),
    });
  }

  function action(body) {
    return post("/api/actions", body);
  }

  return {get, post, action};
}
