(function () {
  const standalone = window.matchMedia("(display-mode: standalone)").matches
    || window.matchMedia("(display-mode: fullscreen)").matches
    || window.navigator.standalone === true;
  if (!standalone) return;

  const style = document.createElement("style");
  style.textContent = [
    ".ptr{display:flex;align-items:center;justify-content:center;gap:10px;height:0;overflow:hidden;",
    "color:#e3b41a;background:#151b24;font-family:\"Barlow Condensed\",sans-serif;",
    "letter-spacing:.12em;text-transform:uppercase;font-size:.85rem;}",
    ".ptr-mark{width:14px;height:14px;border:2px solid #e3b41a;border-right-color:transparent;border-radius:50%;",
    "flex:0 0 14px;opacity:.85;}",
    ".ptr.busy .ptr-mark{animation:ptr-spin .7s linear infinite;}",
    "@keyframes ptr-spin{to{transform:rotate(360deg);}}"
  ].join("");
  document.head.appendChild(style);

  const bar = document.createElement("div");
  bar.className = "ptr";
  bar.setAttribute("aria-hidden", "true");
  bar.innerHTML = "<span class=\"ptr-mark\"></span><span class=\"ptr-label\">Pull to refresh</span>";
  const label = bar.querySelector(".ptr-label");

  const scroller = document.querySelector(".tape-scroll") || document.scrollingElement || document.documentElement;
  const host = document.querySelector(".tape-scroll") || document.body;
  host.insertBefore(bar, host.firstChild);

  const THRESH = 70;
  let startY = 0;
  let armed = false;
  let pulling = false;
  let busy = false;
  let pull = 0;

  function setPull(px) {
    pull = px;
    bar.style.height = Math.round(px) + "px";
    if (busy) return;
    label.textContent = px >= THRESH ? "Release to refresh" : "Pull to refresh";
  }

  function top() {
    return (scroller.scrollTop || document.documentElement.scrollTop || 0) <= 0;
  }

  async function refresh() {
    busy = true;
    bar.classList.add("busy");
    label.textContent = "Updating…";
    setPull(56);
    try {
      if (navigator.serviceWorker) {
        const reg = await navigator.serviceWorker.getRegistration();
        if (reg) await reg.update();
      }
      if (window.caches) {
        const keys = await caches.keys();
        await Promise.all(keys.map((key) => caches.delete(key)));
      }
    } catch (err) {}
    location.reload();
  }

  scroller.addEventListener("touchstart", (e) => {
    if (busy) return;
    armed = top();
    startY = e.touches[0].clientY;
    pulling = false;
  }, { passive: true });

  scroller.addEventListener("touchmove", (e) => {
    if (!armed || busy) return;
    const dy = e.touches[0].clientY - startY;
    if (!top() || dy < 10) {
      if (pulling) setPull(0);
      pulling = false;
      return;
    }
    pulling = true;
    if (e.cancelable) e.preventDefault();
    setPull(Math.min(96, dy * 0.5));
  }, { passive: false });

  scroller.addEventListener("touchend", () => {
    if (busy) return;
    if (pulling && pull >= THRESH) refresh();
    else setPull(0);
    armed = false;
    pulling = false;
  });
})();
