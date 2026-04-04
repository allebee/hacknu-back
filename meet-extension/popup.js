/**
 * Popup script — manages config and toggle for caption capture.
 * Stores roomId, backendUrl, and isCapturing in chrome.storage.local.
 * The content script reads these values.
 */

document.addEventListener("DOMContentLoaded", () => {
  const roomIdInput = document.getElementById("roomId");
  const backendUrlInput = document.getElementById("backendUrl");
  const startBtn = document.getElementById("startBtn");
  const statusEl = document.getElementById("status");

  let isCapturing = false;

  // Load saved config
  chrome.storage.local.get(["roomId", "backendUrl", "isCapturing"], (data) => {
    if (data.roomId) roomIdInput.value = data.roomId;
    if (data.backendUrl) backendUrlInput.value = data.backendUrl;
    if (data.isCapturing) {
      isCapturing = true;
      updateUI();
    }
  });

  // Toggle capture
  startBtn.addEventListener("click", () => {
    const roomId = roomIdInput.value.trim();
    const backendUrl = backendUrlInput.value.trim() || "http://localhost:8000";

    if (!roomId) {
      statusEl.innerHTML = '<span class="dot red"></span>Enter a Room ID first';
      return;
    }

    isCapturing = !isCapturing;

    // Save to storage (content.js listens for changes)
    chrome.storage.local.set({
      roomId,
      backendUrl,
      isCapturing,
    });

    updateUI();
  });

  // Save config on input change (without toggling capture)
  roomIdInput.addEventListener("change", () => {
    chrome.storage.local.set({ roomId: roomIdInput.value.trim() });
  });
  backendUrlInput.addEventListener("change", () => {
    chrome.storage.local.set({
      backendUrl: backendUrlInput.value.trim() || "http://localhost:8000",
    });
  });

  function updateUI() {
    if (isCapturing) {
      startBtn.textContent = "Stop Capturing";
      startBtn.classList.add("active");
      statusEl.innerHTML =
        '<span class="dot green"></span>Capturing captions → ' +
        roomIdInput.value.trim().substring(0, 12) + "...";
    } else {
      startBtn.textContent = "Start Capturing";
      startBtn.classList.remove("active");
      statusEl.innerHTML = '<span class="dot grey"></span>Not connected';
    }
  }
});
