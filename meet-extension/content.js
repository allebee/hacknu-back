/**
 * Canvas Meet Sync — Content Script
 *
 * Runs inside the Google Meet tab. Watches for live captions via MutationObserver
 * and POSTs batched transcript chunks to the backend every 15-30 seconds.
 *
 * The room_id and backend URL are stored in chrome.storage.local by the popup.
 */

(() => {
  "use strict";

  // ── Config ───────────────────────────────────────────────────────────
  const BATCH_INTERVAL_MS = 20_000; // 20 seconds
  const MIN_TEXT_LENGTH = 3;

  let buffer = [];
  let config = { roomId: "", backendUrl: "http://localhost:8000" };
  let isCapturing = false;
  let observer = null;
  let batchTimer = null;

  // ── Load config from storage ─────────────────────────────────────────
  chrome.storage.local.get(["roomId", "backendUrl", "isCapturing"], (data) => {
    if (data.roomId) config.roomId = data.roomId;
    if (data.backendUrl) config.backendUrl = data.backendUrl;
    if (data.isCapturing) {
      isCapturing = true;
      startCapture();
    }
  });

  // Listen for config changes from popup
  chrome.storage.onChanged.addListener((changes) => {
    if (changes.roomId) config.roomId = changes.roomId.newValue || "";
    if (changes.backendUrl) config.backendUrl = changes.backendUrl.newValue || config.backendUrl;
    if (changes.isCapturing) {
      const nowCapturing = changes.isCapturing.newValue;
      if (nowCapturing && !isCapturing) {
        isCapturing = true;
        startCapture();
      } else if (!nowCapturing && isCapturing) {
        isCapturing = false;
        stopCapture();
      }
    }
  });

  // ── Caption scraping ─────────────────────────────────────────────────

  function findCaptionContainer() {
    // Google Meet caption selectors (may change over time)
    // Try multiple known selectors for resilience
    const selectors = [
      '[jscontroller="TEjod"]',
      ".a4cQT",
      '[data-is-persistent-caption="true"]',
      ".iOzk7",
    ];
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  function extractSpeakerAndText(node) {
    // Try to find speaker name element
    const speakerEl =
      node.querySelector(".zs7s8d") ||
      node.querySelector('[data-sender-name]') ||
      node.querySelector(".KcIKyf");

    let speaker = "Unknown";
    if (speakerEl) {
      speaker = speakerEl.getAttribute("data-sender-name") || speakerEl.textContent || "Unknown";
    }

    // Get the caption text (full node text minus the speaker name)
    let text = node.textContent || "";
    if (speaker !== "Unknown") {
      text = text.replace(speaker, "").trim();
    }
    return { speaker: speaker.trim(), text: text.trim() };
  }

  function startCapture() {
    console.log("[Canvas Meet Sync] Starting caption capture...");

    // Wait for captions container to appear
    const tryAttach = () => {
      const container = findCaptionContainer();
      if (!container) {
        console.log("[Canvas Meet Sync] Caption container not found, retrying in 3s...");
        console.log("[Canvas Meet Sync] Make sure captions are turned ON in Google Meet (CC button).");
        setTimeout(tryAttach, 3000);
        return;
      }

      console.log("[Canvas Meet Sync] Found caption container, observing...");

      observer = new MutationObserver((mutations) => {
        if (!isCapturing || !config.roomId) return;

        for (const mutation of mutations) {
          // Track both added nodes and text changes
          for (const node of mutation.addedNodes) {
            if (node.nodeType !== Node.ELEMENT_NODE) continue;
            const { speaker, text } = extractSpeakerAndText(node);
            if (text.length >= MIN_TEXT_LENGTH) {
              buffer.push({ speaker, text, timestamp: Date.now() });
            }
          }
          // Also catch text content changes in existing nodes
          if (mutation.type === "characterData") {
            const parentEl = mutation.target.parentElement;
            if (parentEl) {
              const { speaker, text } = extractSpeakerAndText(parentEl.closest("[class]") || parentEl);
              if (text.length >= MIN_TEXT_LENGTH) {
                // Update the last buffer entry if same speaker within 2s
                const last = buffer[buffer.length - 1];
                if (last && last.speaker === speaker && Date.now() - last.timestamp < 2000) {
                  last.text = text;
                } else {
                  buffer.push({ speaker, text, timestamp: Date.now() });
                }
              }
            }
          }
        }
      });

      observer.observe(container, {
        childList: true,
        subtree: true,
        characterData: true,
      });

      // Start batch send timer
      batchTimer = setInterval(flushBuffer, BATCH_INTERVAL_MS);
    };

    tryAttach();
  }

  function stopCapture() {
    console.log("[Canvas Meet Sync] Stopping capture.");
    if (observer) {
      observer.disconnect();
      observer = null;
    }
    if (batchTimer) {
      clearInterval(batchTimer);
      batchTimer = null;
    }
    // Flush remaining
    flushBuffer();
  }

  // ── Send to backend ──────────────────────────────────────────────────

  async function flushBuffer() {
    if (buffer.length === 0 || !config.roomId) return;

    const chunks = buffer.map((b) => ({ speaker: b.speaker, text: b.text }));
    buffer = [];

    const url = `${config.backendUrl}/rooms/${encodeURIComponent(config.roomId)}/transcript`;

    try {
      const resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chunks }),
      });
      if (resp.ok) {
        const data = await resp.json();
        console.log(`[Canvas Meet Sync] Sent ${data.stored_count} chunks to room=${config.roomId}`);
      } else {
        console.warn(`[Canvas Meet Sync] POST failed: ${resp.status}`);
      }
    } catch (err) {
      console.warn("[Canvas Meet Sync] Network error:", err.message);
    }
  }
})();
