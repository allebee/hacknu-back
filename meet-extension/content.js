/**
 * Canvas Meet Sync — Content Script (v4)
 *
 * Simple polling approach: every 2 seconds, check for caption elements
 * using the known Google Meet caption class. Only captures NEW finalized text.
 */

(() => {
  "use strict";

  const POLL_INTERVAL_MS = 2_000;
  const BATCH_INTERVAL_MS = 10_000;
  const MIN_TEXT_LENGTH = 5;

  // Known Google Meet caption selectors (update if Google changes them)
  const CAPTION_SELECTORS = [
    ".ygicle",           // caption text container (from user inspection)
    ".VbkSUe",           // alternative caption class
  ];

  let buffer = [];
  let config = { roomId: "", backendUrl: "http://localhost:8000" };
  let isCapturing = false;
  let pollTimer = null;
  let batchTimer = null;

  // Track what we've already captured to avoid duplicates
  let lastCapturedText = "";
  const capturedSet = new Set();

  // ── Config ───────────────────────────────────────────────────────────
  chrome.storage.local.get(["roomId", "backendUrl", "isCapturing"], (data) => {
    if (data.roomId) config.roomId = data.roomId;
    if (data.backendUrl) config.backendUrl = data.backendUrl;
    if (data.isCapturing) {
      isCapturing = true;
      startCapture();
    }
  });

  chrome.storage.onChanged.addListener((changes) => {
    if (changes.roomId) config.roomId = changes.roomId.newValue || "";
    if (changes.backendUrl) config.backendUrl = changes.backendUrl.newValue || config.backendUrl;
    if (changes.isCapturing) {
      if (changes.isCapturing.newValue && !isCapturing) {
        isCapturing = true;
        startCapture();
      } else if (!changes.isCapturing.newValue && isCapturing) {
        isCapturing = false;
        stopCapture();
      }
    }
  });

  // ── Polling: read caption elements every 2s ──────────────────────────

  function pollCaptions() {
    if (!isCapturing || !config.roomId) return;

    // Find all caption elements
    let captionEls = [];
    for (const sel of CAPTION_SELECTORS) {
      const found = document.querySelectorAll(sel);
      if (found.length > 0) {
        captionEls = found;
        break;
      }
    }

    if (captionEls.length === 0) return;

    for (const el of captionEls) {
      const text = el.textContent?.trim();
      if (!text || text.length < MIN_TEXT_LENGTH || text.length > 500) continue;

      // Skip if identical to last captured
      if (text === lastCapturedText) continue;

      // Skip if we already captured this exact text
      if (capturedSet.has(text)) continue;

      // Find speaker name — it's usually in a sibling/parent element
      let speaker = "Unknown";
      const parent = el.parentElement;
      if (parent) {
        // Speaker name is typically in the element before the caption text
        const prev = el.previousElementSibling;
        if (prev && prev.textContent?.trim().length < 50) {
          speaker = prev.textContent.trim();
        } else {
          // Check parent's children
          for (const child of parent.children) {
            if (child === el) break;
            const name = child.textContent?.trim();
            if (name && name.length < 50 && name.length > 0) {
              speaker = name;
            }
          }
        }
      }

      lastCapturedText = text;
      capturedSet.add(text);

      // Keep set from growing forever
      if (capturedSet.size > 300) {
        const iter = capturedSet.values();
        capturedSet.delete(iter.next().value);
      }

      buffer.push({ speaker, text, timestamp: Date.now() });
      console.log(`[Canvas Meet Sync] ✅ [${speaker}]: ${text.substring(0, 80)}...`);
    }
  }

  // ── Start/Stop ───────────────────────────────────────────────────────

  function startCapture() {
    console.log("[Canvas Meet Sync v4] Started — polling .ygicle every 2s");
    console.log("[Canvas Meet Sync v4] Turn ON captions (CC button) in Google Meet!");
    pollTimer = setInterval(pollCaptions, POLL_INTERVAL_MS);
    batchTimer = setInterval(flushBuffer, BATCH_INTERVAL_MS);
  }

  function stopCapture() {
    console.log("[Canvas Meet Sync v4] Stopped.");
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    if (batchTimer) { clearInterval(batchTimer); batchTimer = null; }
    flushBuffer();
    capturedSet.clear();
    lastCapturedText = "";
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
        console.log(`[Canvas Meet Sync v4] Sent ${data.stored_count} chunks ✅`);
      } else {
        console.warn(`[Canvas Meet Sync v4] POST failed: ${resp.status}`);
      }
    } catch (err) {
      console.warn("[Canvas Meet Sync v4] Network error:", err.message);
    }
  }
})();
