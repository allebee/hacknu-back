/**
 * Canvas Meet Sync — Content Script (v2)
 *
 * Runs inside the Google Meet tab. Uses a robust approach to find captions:
 * 1. Observes document.body for new nodes
 * 2. Identifies captions via aria-live="polite" regions (Google's accessibility pattern)
 * 3. Falls back to structural heuristics if ARIA isn't found
 * 4. Deduplicates and batches every 20 seconds
 */

(() => {
  "use strict";

  const BATCH_INTERVAL_MS = 20_000;
  const MIN_TEXT_LENGTH = 5;

  let buffer = [];
  let config = { roomId: "", backendUrl: "http://localhost:8000" };
  let isCapturing = false;
  let observer = null;
  let batchTimer = null;
  let lastSentText = "";
  let captionContainer = null;

  // ── Load config ──────────────────────────────────────────────────────
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

  // ── Find caption container ───────────────────────────────────────────

  function findCaptionContainer() {
    // Strategy 1: ARIA live region — Google Meet renders captions in an
    // aria-live="polite" container at the bottom of the screen.
    const ariaLive = document.querySelectorAll('[aria-live="polite"]');
    for (const el of ariaLive) {
      // Caption containers are typically at the bottom of the viewport
      const rect = el.getBoundingClientRect();
      if (rect.bottom > window.innerHeight * 0.6 && rect.height > 20 && rect.height < 300) {
        console.log("[Canvas Meet Sync] Found caption container via aria-live:", el);
        return el;
      }
    }

    // Strategy 2: Look for elements with role="region" that contain caption-like text
    const regions = document.querySelectorAll('[role="region"]');
    for (const el of regions) {
      const rect = el.getBoundingClientRect();
      if (rect.bottom > window.innerHeight * 0.6 && el.textContent.length > 0) {
        console.log("[Canvas Meet Sync] Found caption container via role=region:", el);
        return el;
      }
    }

    // Strategy 3: Look for the caption bottom bar by position heuristic
    // Captions appear in a fixed/absolute-positioned container at the bottom
    const allDivs = document.querySelectorAll('div[style*="bottom"]');
    for (const el of allDivs) {
      const style = window.getComputedStyle(el);
      if (
        (style.position === "fixed" || style.position === "absolute") &&
        parseInt(style.bottom) < 100 &&
        el.textContent.length > 5 &&
        el.textContent.length < 500
      ) {
        console.log("[Canvas Meet Sync] Found caption container via position heuristic:", el);
        return el;
      }
    }

    return null;
  }

  // ── Caption text extraction ──────────────────────────────────────────

  function isCaptionNode(node) {
    // Reject common UI elements that aren't captions
    if (!node || !node.textContent) return false;
    const text = node.textContent.trim();
    if (text.length < MIN_TEXT_LENGTH) return false;

    // Reject known UI patterns (toolbar, menus, buttons)
    const tag = (node.tagName || "").toLowerCase();
    if (["button", "input", "select", "textarea", "nav", "header", "footer"].includes(tag)) return false;

    // Reject elements with button/menu/toolbar roles
    const role = node.getAttribute?.("role") || "";
    if (["button", "menu", "menuitem", "toolbar", "tab", "tablist", "dialog", "navigation"].includes(role)) return false;

    // Reject if parent is a button or has button role
    const parent = node.parentElement;
    if (parent) {
      const parentRole = parent.getAttribute?.("role") || "";
      const parentTag = (parent.tagName || "").toLowerCase();
      if (parentTag === "button" || parentRole === "button" || parentRole === "menu") return false;
    }

    // Reject elements with aria labels that indicate UI controls
    const ariaLabel = (node.getAttribute?.("aria-label") || "").toLowerCase();
    const uiLabels = ["menu", "toolbar", "size", "font", "color", "setting", "option", "button", "close", "more"];
    if (uiLabels.some((l) => ariaLabel.includes(l))) return false;

    // Reject if text matches known UI patterns
    const uiPatterns = [
      /^format_size/i,
      /^Размер шрифт/i,
      /^По умолчанию/i,
      /^(Мелкий|Маленький|Средний|Крупный|Огромный|Гигантский)/,
      /^(Default|Small|Medium|Large|Huge|Giant)/i,
      /^circle$/i,
      /^more_vert$/i,
      /^close$/i,
    ];
    if (uiPatterns.some((p) => p.test(text))) return false;

    return true;
  }

  function extractSpeakerFromCaption(captionEl) {
    // Google Meet shows speaker name in a separate child element
    // Usually the first child or a specifically styled span
    const children = captionEl.children;
    if (children.length >= 2) {
      const possibleSpeaker = children[0].textContent?.trim();
      const possibleText = Array.from(children)
        .slice(1)
        .map((c) => c.textContent?.trim())
        .join(" ");
      if (possibleSpeaker && possibleSpeaker.length < 50 && possibleText.length > 3) {
        return { speaker: possibleSpeaker, text: possibleText };
      }
    }
    return { speaker: "Unknown", text: captionEl.textContent.trim() };
  }

  // ── Capture logic ────────────────────────────────────────────────────

  function startCapture() {
    console.log("[Canvas Meet Sync] Starting caption capture v2...");
    console.log("[Canvas Meet Sync] Make sure captions are turned ON (CC button).");

    // Observe the entire document body and filter intelligently
    observer = new MutationObserver((mutations) => {
      if (!isCapturing || !config.roomId) return;

      for (const mutation of mutations) {
        // Watch for new child nodes
        if (mutation.type === "childList") {
          for (const node of mutation.addedNodes) {
            if (node.nodeType !== Node.ELEMENT_NODE) continue;
            processNode(node);
          }
        }
        // Watch for text content changes (captions update in-place)
        if (mutation.type === "characterData") {
          const el = mutation.target.parentElement;
          if (el) processNode(el);
        }
      }
    });

    // Periodically try to find and observe just the caption container
    // for better performance. Fall back to body observation.
    const tryNarrowObservation = () => {
      const container = findCaptionContainer();
      if (container && container !== captionContainer) {
        captionContainer = container;
        // Restart observer on the narrower target
        observer.disconnect();
        observer.observe(captionContainer, {
          childList: true,
          subtree: true,
          characterData: true,
        });
        console.log("[Canvas Meet Sync] Narrowed observation to caption container.");
      } else if (!captionContainer) {
        // Fall back to body
        observer.observe(document.body, {
          childList: true,
          subtree: true,
          characterData: true,
        });
        console.log("[Canvas Meet Sync] Observing document.body (caption container not found yet).");
      }
    };

    tryNarrowObservation();
    // Re-check every 10 seconds to narrow the observation
    const narrowInterval = setInterval(() => {
      if (!isCapturing) {
        clearInterval(narrowInterval);
        return;
      }
      if (!captionContainer) tryNarrowObservation();
    }, 10000);

    batchTimer = setInterval(flushBuffer, BATCH_INTERVAL_MS);
  }

  function processNode(node) {
    if (!isCaptionNode(node)) return;

    // Check if this node is inside the caption container (if we found one)
    if (captionContainer && !captionContainer.contains(node)) return;

    const { speaker, text } = extractSpeakerFromCaption(node);

    // Deduplicate: skip if same as last captured text
    if (text === lastSentText) return;
    lastSentText = text;

    // Skip very long texts (likely not captions)
    if (text.length > 500) return;

    buffer.push({ speaker, text, timestamp: Date.now() });
  }

  function stopCapture() {
    console.log("[Canvas Meet Sync] Stopping capture.");
    if (observer) {
      observer.disconnect();
      observer = null;
    }
    captionContainer = null;
    if (batchTimer) {
      clearInterval(batchTimer);
      batchTimer = null;
    }
    flushBuffer();
  }

  // ── Send to backend ──────────────────────────────────────────────────

  async function flushBuffer() {
    if (buffer.length === 0 || !config.roomId) return;

    // Deduplicate within buffer
    const seen = new Set();
    const unique = buffer.filter((b) => {
      const key = `${b.speaker}:${b.text}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    buffer = [];

    if (unique.length === 0) return;

    const url = `${config.backendUrl}/rooms/${encodeURIComponent(config.roomId)}/transcript`;
    const chunks = unique.map((b) => ({ speaker: b.speaker, text: b.text }));

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
