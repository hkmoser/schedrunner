// 4-digit passcode gate shown before the app boots. The entered code selects an
// access mode:
//   • 1937            → full experience (all pages)
//   • all-even digits → decoy __APP_NAME_LOWER__ only (dummy content, no navigation)
// Anything else is rejected (the keypad shakes and clears).

export type AccessMode = "full" | "decoy";

export function classifyPasscode(code: string): AccessMode | null {
  if (!/^\d{4}$/.test(code)) return null;
  if (code === "1937") return "full";
  if ([...code].every((d) => Number(d) % 2 === 0)) return "decoy";
  return null;
}

// Render a passcode keypad into `host`; calls onUnlock(mode) once a valid code is entered.
export function renderLockScreen(host: HTMLElement, onUnlock: (mode: AccessMode) => void): void {
  let code = "";

  const wrap = document.createElement("div");
  wrap.className = "lock";

  const icon = document.createElement("div");
  icon.className = "lock-icon";
  icon.textContent = "🔒";

  const title = document.createElement("div");
  title.className = "lock-title";
  title.textContent = "Enter Passcode";

  const dots = document.createElement("div");
  dots.className = "lock-dots";
  const dotEls: HTMLElement[] = [];
  for (let i = 0; i < 4; i++) {
    const d = document.createElement("span");
    d.className = "lock-dot";
    dots.appendChild(d);
    dotEls.push(d);
  }

  function renderDots() {
    dotEls.forEach((d, i) => d.classList.toggle("filled", i < code.length));
  }

  const onKey = (e: KeyboardEvent) => {
    if (/^[0-9]$/.test(e.key)) press(e.key);
    else if (e.key === "Backspace") del();
  };

  function fire(mode: AccessMode) {
    document.removeEventListener("keydown", onKey);
    onUnlock(mode);
  }

  function reject() {
    wrap.classList.add("shake");
    setTimeout(() => {
      wrap.classList.remove("shake");
      code = "";
      renderDots();
    }, 450);
  }

  function press(digit: string) {
    if (code.length >= 4 || wrap.classList.contains("shake")) return;
    code += digit;
    renderDots();
    if (code.length === 4) {
      const mode = classifyPasscode(code);
      if (mode) setTimeout(() => fire(mode), 140); // let the 4th dot paint
      else reject();
    }
  }

  function del() {
    if (code.length === 0) return;
    code = code.slice(0, -1);
    renderDots();
  }

  const pad = document.createElement("div");
  pad.className = "keypad";
  const keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "⌫"];
  for (const k of keys) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "key";
    if (k === "") {
      btn.classList.add("key-blank");
      btn.disabled = true;
    } else if (k === "⌫") {
      btn.classList.add("key-action");
      btn.textContent = k;
      btn.addEventListener("click", del);
    } else {
      btn.textContent = k;
      btn.addEventListener("click", () => press(k));
    }
    pad.appendChild(btn);
  }

  document.addEventListener("keydown", onKey);
  wrap.append(icon, title, dots, pad);
  host.replaceChildren(wrap);
}
