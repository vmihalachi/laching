// Tabs (install line, code sample), copy buttons, and the conformance timeline playback.
(() => {
  for (const group of document.querySelectorAll("[data-tabs]")) {
    const tabs = [...group.querySelectorAll('[role="tab"]')];
    const select = (tab) => {
      for (const t of tabs) {
        const on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
      }
    };
    tabs.forEach((tab, i) => {
      tab.addEventListener("click", () => select(tab));
      tab.addEventListener("keydown", (e) => {
        const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
        if (!step) return;
        const next = tabs[(i + step + tabs.length) % tabs.length];
        select(next);
        next.focus();
      });
    });
  }

  // The button's own text changes for sighted users; the live region says the same to a screen reader.
  const status = document.getElementById("copy-status");
  for (const button of document.querySelectorAll(".copy")) {
    button.addEventListener("click", async () => {
      const text = button.parentElement.querySelector("code").textContent;
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = "Copied";
        if (status) status.textContent = "Copied to the clipboard.";
      } catch {
        button.textContent = "Select";
        const range = document.createRange();
        range.selectNodeContents(button.parentElement.querySelector("code"));
        getSelection().removeAllRanges();
        getSelection().addRange(range);
        if (status) status.textContent = "Selected. Copy it with your keyboard.";
      }
      button.classList.add("done");
      setTimeout(() => {
        button.textContent = "Copy";
        button.classList.remove("done");
        if (status) status.textContent = "";
      }, 1600);
    });
  }

  const vector = document.getElementById("vector");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!vector || reduce || !("IntersectionObserver" in window)) return;

  const timeline = vector.querySelector(".timeline");
  const track = vector.querySelector(".lane-events .track");
  const playhead = vector.querySelector(".playhead");
  const marks = [...vector.querySelectorAll(".ev, .bar")].map((el) => ({ el, at: parseFloat(el.style.left) / 100 }));
  vector.classList.add("armed");

  const play = () => {
    vector.classList.add("playing");
    const start = performance.now();
    const duration = 2600;
    const frame = (now) => {
      const p = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      const t = track.getBoundingClientRect();
      const left = t.left - timeline.getBoundingClientRect().left + eased * t.width;
      playhead.style.left = `${left - 1}px`;
      for (const m of marks) if (eased >= m.at) m.el.classList.add("hit");
      if (p < 1) requestAnimationFrame(frame);
      else setTimeout(() => vector.classList.remove("playing"), 500);
    };
    requestAnimationFrame(frame);
  };

  const io = new IntersectionObserver(
    (entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        io.disconnect();
        play();
      }
    },
    { threshold: 0.6 }
  );
  io.observe(timeline);
})();
