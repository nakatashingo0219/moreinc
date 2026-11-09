// ひらがな・カタカナ・全角半角・大文字小文字を区別せずに検索する
function normalizeText(s) {
  return (s || "")
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[ァ-ヶ]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0x60))
    .replace(/\s+/g, "");
}

function setupFilter(root) {
  const input = root.querySelector("[data-search]");
  const chips = root.querySelectorAll("[data-chip]");
  const items = root.querySelectorAll("[data-item]");
  const empty = root.querySelector("[data-empty]");
  let category = "";
  function apply() {
    const q = normalizeText(input ? input.value : "");
    let shown = 0;
    items.forEach((el) => {
      const hit =
        (!q || normalizeText(el.dataset.text).includes(q)) &&
        (!category || el.dataset.category === category);
      el.hidden = !hit;
      if (hit) shown++;
    });
    if (empty) empty.hidden = shown > 0;
  }
  if (input) input.addEventListener("input", apply);
  chips.forEach((chip) =>
    chip.addEventListener("click", () => {
      category = chip.dataset.chip;
      chips.forEach((c) => c.classList.toggle("on", c === chip));
      apply();
    })
  );
}

function setupStepper(root) {
  const input = root.querySelector("input[type=number]");
  root.querySelectorAll("[data-step]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const min = input.min === "" ? -Infinity : Number(input.min);
      const v = Math.max(min, (Number(input.value) || 0) + Number(btn.dataset.step));
      input.value = v;
      input.dispatchEvent(new Event("input"));
    })
  );
}

function setupConfirm(form) {
  form.addEventListener("submit", (e) => {
    const msg = form.dataset.confirm.replace(/\{(\w+)\}/g, (_, name) => {
      const el = form.querySelector(`[name=${name}]:checked`) || form.querySelector(`[name=${name}]`);
      if (!el) return "";
      if (el.type === "radio") return el.dataset.label || el.value;
      return el.value;
    });
    if (!window.confirm(msg)) e.preventDefault();
  });
}

function setupDrop(label) {
  const input = label.querySelector("input[type=file]");
  const text = label.querySelector("[data-filename]");
  const show = () => { if (input.files.length && text) text.textContent = input.files[0].name; };
  input.addEventListener("change", show);
  ["dragenter", "dragover"].forEach((t) => label.addEventListener(t, (e) => { e.preventDefault(); label.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => label.addEventListener(t, () => label.classList.remove("over")));
  label.addEventListener("drop", (e) => {
    e.preventDefault();
    if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; show(); }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-filter]").forEach(setupFilter);
  document.querySelectorAll("[data-stepper]").forEach(setupStepper);
  document.querySelectorAll("form[data-confirm]").forEach(setupConfirm);
  document.querySelectorAll("label.drop").forEach(setupDrop);
  document.querySelectorAll(".count-input").forEach((el) => {
    const mark = () => el.classList.toggle("filled", el.value !== "");
    el.addEventListener("input", mark);
    mark();
  });
  const toggle = document.querySelector("[data-toggle-others]");
  if (toggle) toggle.addEventListener("click", () => {
    document.body.classList.toggle("show-others");
    toggle.textContent = document.body.classList.contains("show-others") ? "この場所の商品だけ表示" : "ほかの商品も表示";
  });
});
