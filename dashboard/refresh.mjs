export async function refreshAfterPending(pendingRefresh, refresh, options = {}) {
  if (pendingRefresh) {
    await pendingRefresh;
  }
  return refresh(options);
}
