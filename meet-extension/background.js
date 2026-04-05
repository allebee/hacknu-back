/**
 * Canvas Meet Sync — Background Service Worker
 *
 * Handles all network requests on behalf of the content script.
 * This bypasses cross-origin restrictions that affect content scripts.
 */

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type !== "POST_TRANSCRIPT") return false;

  const { url, chunks } = msg;

  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chunks }),
  })
    .then(async (resp) => {
      if (resp.ok) {
        const data = await resp.json();
        sendResponse({ ok: true, stored_count: data.stored_count });
      } else {
        sendResponse({ ok: false, status: resp.status });
      }
    })
    .catch((err) => {
      sendResponse({ ok: false, error: err.message });
    });

  return true; // keep message channel open for async response
});
