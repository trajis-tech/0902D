(() => {
  const STAGE_DEFS = [
    { key: "result", label: "檢查結果" },
  ];

  const state = {
    templates: [],
    paramValues: {},
    imagePath: "",
    imageName: "",
    batchFiles: [],
    stageImages: null,
    activeStage: "result",
    lastResult: null,
    view: {
      scale: 1,
      tx: 0,
      ty: 0,
      resetOnLoad: true,
      dragging: false,
      lastX: 0,
      lastY: 0,
    },
  };

  const $ = (id) => document.getElementById(id);

  function setStatus(text, kind) {
    $("workspaceStatusMirror").textContent = text;
    const el = $("workspaceStatusIndicator");
    el.className = "status-indicator " + (kind === "busy" ? "is-busy" : kind === "ok" ? "is-ok" : kind === "err" ? "is-err" : "is-neutral");
  }

  function closeMenus() {
    document.querySelectorAll(".toolbar-menu").forEach((m) => { m.hidden = true; });
    document.querySelectorAll(".toolbar-btn").forEach((b) => b.setAttribute("aria-expanded", "false"));
  }

  function showDialog(title, html) {
    $("dialogTitle").textContent = title;
    $("dialogBody").innerHTML = html;
    const layer = $("dialogLayer");
    layer.hidden = false;
    layer.classList.add("is-open");
    layer.setAttribute("aria-hidden", "false");
  }

  function hideDialog() {
    const layer = $("dialogLayer");
    layer.classList.remove("is-open");
    layer.hidden = true;
    layer.setAttribute("aria-hidden", "true");
  }

  function defaultParams() {
    const values = {};
    for (const t of state.templates) {
      values[t.key] = t.default;
    }
    return values;
  }

  function renderParams() {
    const form = $("paramsForm");
    form.innerHTML = "";
    for (const t of state.templates) {
      const wrap = document.createElement("label");
      wrap.className = "field" + (t.type === "boolean" ? " field-bool" : "");
      wrap.title = t.description || "";
      const name = document.createElement("span");
      name.textContent = t.label;
      wrap.appendChild(name);
      const value = state.paramValues[t.key];
      if (t.type === "boolean") {
        const input = document.createElement("input");
        input.type = "checkbox";
        input.checked = Boolean(value);
        input.addEventListener("change", () => { state.paramValues[t.key] = input.checked; });
        wrap.appendChild(input);
      } else if (t.type === "color") {
        const input = document.createElement("input");
        input.type = "color";
        input.value = String(value || "#e23b3b");
        input.addEventListener("input", () => { state.paramValues[t.key] = input.value; });
        wrap.appendChild(input);
      } else {
        const input = document.createElement("input");
        input.type = t.type === "number" ? "number" : "text";
        if (t.type === "number") input.step = "any";
        input.value = value == null ? "" : value;
        input.addEventListener("input", () => {
          if (t.type === "number") {
            const raw = String(input.value).trim();
            const num = Number(raw);
            state.paramValues[t.key] = raw === "" || Number.isNaN(num) ? t.default : num;
          } else {
            state.paramValues[t.key] = input.value;
          }
        });
        wrap.appendChild(input);
      }
      form.appendChild(wrap);
    }
  }

  function renderStageTabs() {
    const tabs = $("stageTabs");
    tabs.innerHTML = "";
    for (const st of STAGE_DEFS) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = st.label;
      btn.className = st.key === state.activeStage ? "is-active" : "";
      btn.addEventListener("click", () => {
        state.activeStage = st.key;
        renderStageTabs();
        renderPreview();
      });
      tabs.appendChild(btn);
    }
  }

  function applyView() {
    const img = $("previewImage");
    const label = $("zoomLabel");
    if (label) label.textContent = Math.round(state.view.scale * 100) + "%";
    if (!img) return;
    img.style.transform = `translate(${state.view.tx}px, ${state.view.ty}px) scale(${state.view.scale})`;
  }

  function fitToView() {
    const vp = $("previewViewport");
    const img = $("previewImage");
    if (!vp || !img || !img.naturalWidth) return;
    const pad = 16;
    const vw = Math.max(1, vp.clientWidth - pad);
    const vh = Math.max(1, vp.clientHeight - pad);
    state.view.scale = Math.max(0.05, Math.min(vw / img.naturalWidth, vh / img.naturalHeight));
    state.view.tx = (vp.clientWidth - img.naturalWidth * state.view.scale) / 2;
    state.view.ty = (vp.clientHeight - img.naturalHeight * state.view.scale) / 2;
    applyView();
  }

  function zoomToActual() {
    const vp = $("previewViewport");
    const img = $("previewImage");
    if (!vp || !img || !img.naturalWidth) return;
    state.view.scale = 1;
    state.view.tx = (vp.clientWidth - img.naturalWidth) / 2;
    state.view.ty = (vp.clientHeight - img.naturalHeight) / 2;
    applyView();
  }

  function zoomAt(newScale, cx, cy) {
    const next = Math.min(16, Math.max(0.05, newScale));
    const ix = (cx - state.view.tx) / state.view.scale;
    const iy = (cy - state.view.ty) / state.view.scale;
    state.view.scale = next;
    state.view.tx = cx - ix * next;
    state.view.ty = cy - iy * next;
    applyView();
  }

  function zoomBy(factor) {
    const vp = $("previewViewport");
    if (!vp) return;
    zoomAt(state.view.scale * factor, vp.clientWidth / 2, vp.clientHeight / 2);
  }

  function renderPreview() {
    const box = $("previewBox");
    const label = $("zoomLabel");
    if (!state.stageImages) {
      box.innerHTML = '<p class="preview-placeholder">載入影像並執行後，檢查結果會顯示於此。滾輪放大、拖曳平移。</p>';
      if (label) label.textContent = "—";
      return;
    }
    const b64 = state.stageImages.result;
    if (!b64) {
      box.innerHTML = '<p class="preview-placeholder">沒有結果圖。</p>';
      if (label) label.textContent = "—";
      return;
    }
    let img = $("previewImage");
    if (!img) {
      box.innerHTML = "";
      img = document.createElement("img");
      img.id = "previewImage";
      img.alt = "階段預覽";
      img.draggable = false;
      img.addEventListener("load", () => {
        if (state.view.resetOnLoad) {
          fitToView();
          state.view.resetOnLoad = false;
        } else {
          applyView();
        }
      });
      box.appendChild(img);
    }
    img.alt = state.activeStage;
    img.src = "data:image/png;base64," + b64;
  }

  function wirePreviewZoom() {
    const vp = $("previewViewport");
    if (!vp) return;
    vp.addEventListener("wheel", (ev) => {
      if (!$("previewImage")) return;
      ev.preventDefault();
      const rect = vp.getBoundingClientRect();
      const factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
      zoomAt(state.view.scale * factor, ev.clientX - rect.left, ev.clientY - rect.top);
    }, { passive: false });
    vp.addEventListener("pointerdown", (ev) => {
      if (!$("previewImage") || ev.button !== 0) return;
      state.view.dragging = true;
      state.view.lastX = ev.clientX;
      state.view.lastY = ev.clientY;
      vp.classList.add("is-dragging");
      vp.setPointerCapture(ev.pointerId);
    });
    vp.addEventListener("pointermove", (ev) => {
      if (!state.view.dragging) return;
      state.view.tx += ev.clientX - state.view.lastX;
      state.view.ty += ev.clientY - state.view.lastY;
      state.view.lastX = ev.clientX;
      state.view.lastY = ev.clientY;
      applyView();
    });
    const stopDrag = (ev) => {
      if (!state.view.dragging) return;
      state.view.dragging = false;
      vp.classList.remove("is-dragging");
      if (vp.hasPointerCapture(ev.pointerId)) vp.releasePointerCapture(ev.pointerId);
    };
    vp.addEventListener("pointerup", stopDrag);
    vp.addEventListener("pointercancel", stopDrag);
    vp.addEventListener("dblclick", (ev) => {
      if (!$("previewImage")) return;
      ev.preventDefault();
      fitToView();
    });
    $("zoomFitBtn").addEventListener("click", fitToView);
    $("zoomOneBtn").addEventListener("click", zoomToActual);
    $("zoomInBtn").addEventListener("click", () => zoomBy(1.2));
    $("zoomOutBtn").addEventListener("click", () => zoomBy(1 / 1.2));
  }

  function fmt(n, digits) {
    if (typeof n !== "number" || Number.isNaN(n)) return "—";
    return n.toFixed(digits);
  }

  function renderMetrics(metrics) {
    const list = $("metricsList");
    if (!metrics) {
      list.innerHTML = "<div><dt>狀態</dt><dd>尚未處理</dd></div>";
      return;
    }
    const rect = metrics.rect || {};
    const warning = escapeHtml(String(metrics.warning || "無"));
    const solder = metrics.solderIndication || {};
    const variant = (solder.variants || {}).idealShifted || {};
    const contour = (metrics.contours || {}).ideal || {};
    const optimization = solder.positionOptimization || solder.horizontalOptimization || contour.positionOptimization || {};
    const optRegions = optimization.regions || [];
    const checks = metrics.qualityChecks || {};
    const pair = checks.pairDistance || {};
    const relative = checks.relativeShift || {};
    const solderOk = solder.status === "ok" && typeof solder.overallRate === "number";
    const rows = [
      ["總判定", escapeHtml(String(metrics.overallStatus || "—"))],
      ["同排 R 間距檢查", escapeHtml(String(pair.status || "—"))],
      ["R1-R2 最短距離", typeof pair.topDistancePx === "number" ? `${fmt(pair.topDistancePx, 1)} px（下限 ${fmt(pair.thresholdPx, 1)}）` : "—"],
      ["R3-R4 最短距離", typeof pair.bottomDistancePx === "number" ? `${fmt(pair.bottomDistancePx, 1)} px（下限 ${fmt(pair.thresholdPx, 1)}）` : "—"],
      ["四 R 相對位移檢查", escapeHtml(String(relative.status || "—"))],
      ["四 R 位移離散度", typeof relative.spreadPx === "number" ? `X ${fmt(relative.spreadXPx, 1)} / Y ${fmt(relative.spreadYPx, 1)} px（警告 > ${fmt(relative.thresholdPx, 1)}）` : "—"],
      ["G", fmt(metrics.G, 3)],
      ["上下帶高", String(metrics.stripH ?? "—")],
      ["μ_rect'", fmt(metrics.muRectPrime, 3)],
      ["輪廓基準", "解析式理想 stadium（X/Y 平移）"],
      ["X/Y 搜尋範圍", `${fmt(Number(metrics.idealShiftMaxPx), 1)} px`],
      ["空焊面積占比", solderOk ? `${(100 * Number(solder.emptyAreaRate)).toFixed(1)}%` : "—"],
      ["有效焊錫顯影占比", solderOk ? `${(100 * Number(solder.overallRate)).toFixed(1)}%` : "—"],
      ["灰階過渡上下界", solderOk && typeof solder.rateLower === "number" && typeof solder.rateUpper === "number" ? `${(100 * Number(solder.rateLower)).toFixed(1)}% ～ ${(100 * Number(solder.rateUpper)).toFixed(1)}%` : "—"],
    ];
    for (let id = 1; id <= 4; id += 1) {
      const r = (variant.regions || solder.regions || []).find((x) => Number(x.id) === id) || {};
      const o = optRegions.find((x) => Number(x.id) === id) || {};
      rows.push([`R${id} X/Y 位移`, typeof o.dxPx === "number" && typeof o.dyPx === "number" ? `X ${o.dxPx >= 0 ? "+" : ""}${Number(o.dxPx).toFixed(1)} / Y ${o.dyPx >= 0 ? "+" : ""}${Number(o.dyPx).toFixed(1)} px` : "—"]);
      rows.push([`R${id} 空焊占比`, typeof r.rate === "number" ? `${(100 * (1 - Number(r.rate))).toFixed(1)}%` : "—"]);
      if (typeof o.voidRateBefore === "number" && typeof o.voidRateAfter === "number") {
        rows.push([`R${id} 平移改善`, `${(100 * Number(o.voidRateBefore)).toFixed(1)}% → ${(100 * Number(o.voidRateAfter)).toFixed(1)}%`]);
      }
    }
    rows.push(
      ["焊錫灰階基準 S", solder.solderReferenceGray == null ? "—" : fmt(Number(solder.solderReferenceGray), 3)],
      ["S / G", solder.solderReferenceRatioG == null ? "—" : fmt(Number(solder.solderReferenceRatioG), 4)],
      ["焊錫基準候選上限", solder.referenceCeilingGray == null ? "—" : fmt(Number(solder.referenceCeilingGray), 3)],
      ["確定焊錫灰階上限", solder.sureSolderThresholdGray == null ? "—" : fmt(Number(solder.sureSolderThresholdGray), 3)],
      ["顯影中心灰階", solder.thresholdGray == null ? "—" : fmt(Number(solder.thresholdGray), 3)],
      ["確定空缺灰階下限", solder.sureVoidThresholdGray == null ? "—" : fmt(Number(solder.sureVoidThresholdGray), 3)],
      ["找框門檻'", fmt(metrics.rectBinThresh, 3)],
      ["A（框外，僅參考）", fmt(metrics.A, 3)],
      ["矩形", escapeHtml(`${fmt(rect.w, 1)} × ${fmt(rect.h, 1)}`)],
      ["方向", escapeHtml(String(metrics.orientation || "—"))],
      ["方向分差", fmt(metrics.orientationMargin, 4)],
      ["理想輪廓狀態", escapeHtml(String(contour.status || "—"))],
      ["理想產品配準分數", fmt(contour.registrationScore, 4)],
      ["輪廓演算法", escapeHtml(String(contour.algorithmVersion || "—"))],
      ["X/Y 最佳化狀態", escapeHtml(String(optimization.status || "—"))],
      ["警告", warning],
    );
    list.innerHTML = rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
  }

  async function apiGet(url) {
    const res = await fetch(url, { cache: "no-store" });
    return res.json();
  }

  async function apiPost(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return res.json();
  }

  function setImageMeta(name, extra) {
    state.imageName = name;
    $("workspaceImageName").textContent = name || "尚未載入影像";
    $("workspaceImageMeta").textContent = extra || "尚未處理";
    $("imageStatus").textContent = name ? "已載入" : "尚未載入";
  }

  function clearSingleResult() {
    state.stageImages = null;
    state.lastResult = null;
    state.activeStage = "result";
    renderStageTabs();
    renderPreview();
    renderMetrics(null);
  }

  async function uploadFile(file) {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/upload-image", { method: "POST", body: fd });
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || "上傳失敗");
    clearSingleResult();
    state.imagePath = data.path;
    setImageMeta(data.originalName, "已上傳，尚未處理");
    setStatus("已載入影像", "ok");
  }

  async function loadSample() {
    const samples = await apiGet("/api/samples");
    if (!samples.ok || !samples.files?.length) throw new Error("工作區沒有範例影像");
    const preferred = samples.files.find((f) => f.name === "1.jpg")
      || samples.files.find((f) => /^[123]\.(jpg|jpeg|png)$/i.test(f.name))
      || samples.files.find((f) => f.name.indexOf("原始") >= 0)
      || samples.files[0];
    const data = await apiPost("/api/use-sample", { name: preferred.name });
    if (!data.ok) throw new Error(data.error || "無法載入範例");
    clearSingleResult();
    state.imagePath = data.path;
    setImageMeta(data.originalName, "已載入範例，尚未處理");
    setStatus("已載入範例", "ok");
  }

  async function processImage() {
    if (!state.imagePath) {
      setStatus("請先載入影像", "err");
      return;
    }
    $("processBtn").disabled = true;
    $("imageStatus").textContent = "處理中";
    setStatus("處理中…", "busy");
    try {
      const data = await apiPost("/api/process", {
        imagePath: state.imagePath,
        paramValues: state.paramValues,
      });
      if (!data.ok) throw new Error(data.error || "處理失敗");
      state.stageImages = data.stageImages;
      state.lastResult = data;
      state.activeStage = "result";
      state.view.resetOnLoad = true;
      renderStageTabs();
      renderPreview();
      renderMetrics(data.metrics);
      $("workspaceImageMeta").textContent = data.savedPath || "處理完成";
      const overall = String(data.metrics?.overallStatus || "FAIL");
      $("imageStatus").textContent = `完成（${overall}）`;
      setStatus(`完成（${overall}）`, overall === "PASS" ? "ok" : "err");
    } catch (err) {
      $("imageStatus").textContent = "處理失敗";
      setStatus(err.message || "處理失敗", "err");
      showDialog("處理失敗", `<p>${escapeHtml(err.message || String(err))}</p>`);
    } finally {
      $("processBtn").disabled = false;
    }
  }

  async function processBatch() {
    const files = state.batchFiles || [];
    if (!files.length) {
      setStatus("請先選擇批量圖片", "err");
      return;
    }
    const button = $("batchProcessBtn");
    button.disabled = true;
    $("batchStatus").textContent = `處理中（${files.length} 張）`;
    setStatus(`批量處理中（${files.length} 張）…`, "busy");
    try {
      const form = new FormData();
      for (const file of files) form.append("files", file, file.name);
      form.append("paramValues", JSON.stringify(state.paramValues));
      const response = await fetch("/api/v1/batch", { method: "POST", body: form });
      if (!response.ok) {
        const text = await response.text();
        let message = text || "批量處理失敗";
        try { message = JSON.parse(text).error || message; } catch (_err) { /* plain text */ }
        throw new Error(message);
      }
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename="([^"]+)"/i);
      const filename = match ? match[1] : "batch_results.xlsx";
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
      $("batchStatus").textContent = `完成（${files.length} 張）`;
      setStatus(`批量完成（${files.length} 張，XLSX 已下載）`, "ok");
    } catch (err) {
      $("batchStatus").textContent = "處理失敗";
      setStatus(err.message || "批量處理失敗", "err");
      showDialog("批量處理失敗", `<p>${escapeHtml(err.message || String(err))}</p>`);
    } finally {
      button.disabled = false;
    }
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function downloadStage(key, fallbackName) {
    const b64 = state.stageImages?.[key];
    if (!b64) {
      setStatus("尚無影像可下載", "err");
      return;
    }
    const stem = String(state.imageName || fallbackName || "xray").replace(/\.[^.]+$/, "");
    const a = document.createElement("a");
    a.href = "data:image/png;base64," + b64;
    a.download = `${stem}_${key}.png`;
    a.click();
  }

  function downloadOverlay() {
    downloadStage("result", "inspection.png");
  }

  function exportParams() {
    const values = {};
    for (const t of state.templates) values[t.key] = state.paramValues[t.key];
    const blob = new Blob([JSON.stringify(values, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "xray_params.json";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function importParamsFile(file) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const data = JSON.parse(String(reader.result || "{}"));
        const values = defaultParams();
        for (const t of state.templates) {
          if (Object.prototype.hasOwnProperty.call(data, t.key)) values[t.key] = data[t.key];
        }
        state.paramValues = values;
        renderParams();
        setStatus("已匯入參數", "ok");
      } catch (err) {
        setStatus("參數 JSON 無效", "err");
      }
    };
    reader.readAsText(file, "utf-8");
  }

  function focusSection(which) {
    const map = { image: "imageBlock", params: "paramsBlock", preview: "previewPanel" };
    const el = $(map[which] || "imageBlock");
    if (!el) return;
    document.querySelectorAll(".ribbon-stages button").forEach((b) => {
      b.classList.toggle("is-active", b.getAttribute("data-workspace-focus") === which);
    });
    el.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function wireToolbar() {
    document.querySelectorAll(".toolbar-btn").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const item = btn.parentElement;
        const menu = item.querySelector(".toolbar-menu");
        const open = menu.hidden;
        closeMenus();
        menu.hidden = !open;
        btn.setAttribute("aria-expanded", open ? "true" : "false");
      });
    });
    document.addEventListener("click", closeMenus);
    document.querySelectorAll("[data-action]").forEach((el) => {
      el.addEventListener("click", () => {
        const action = el.getAttribute("data-action");
        closeMenus();
        if (action === "importParams") $("paramFileInput").click();
        if (action === "exportParams") exportParams();
        if (action === "resetParams") {
          state.paramValues = defaultParams();
          renderParams();
          setStatus("已重設參數", "ok");
        }
        if (action === "downloadOverlay") downloadOverlay();
        if (action === "openHelp") {
          showDialog("處理流程", `
            <h3>1. 找框、方向與配準</h3>
            <p>由上下灰階帶得到 G，找出封裝矩形並旋正。完整封裝中的非焊錫結構估計產品級 affine，將四個核准的解析式 stadium 一次映射到中央裁剪。</p>
            <h3>2. X/Y 空焊最小化</h3>
            <p>每個 R 的尺寸、角度與形狀鎖定，以 1 px 步進搜尋 X/Y 位移。焊錫灰階基準 S 在搜尋前固定，目標是讓理想區內的加權空焊占比最小。</p>
            <h3>3. 幾何品質檢查</h3>
            <p>R1/R2 與 R3/R4 的輪廓邊界最短距離小於設定下限時直接 FAIL。四個 R 的 dx 或 dy 離散度任一超過設定值時輸出 WARNING。FAIL 優先於 WARNING。</p>
            <h3>4. 輸出</h3>
            <p>單張只保留一張結果圖；左上角含 STATUS、VOID、四個 dx/dy、上下排間距、位移離散度與配準分數。批量輸入會逐張保存結果圖並由本地 API 回傳 XLSX。</p>
          `);
        }
        if (action === "openVersion") {
          showDialog("版本資訊", `<p>X-ray 四 R 幾何與焊錫檢查 v31</p><p>本機 HTTP：${escapeHtml(window.location.origin)}</p><p>EXE 內含打包時預設設定；首次啟動會建立 D:\\XrayRegistrationData\\algorithm_config.json，之後使用該檔且不覆寫。修改 JSON 後重啟即可生效。</p><p>單張與批量上傳完全分流，包含 X/Y 理想輪廓平移、幾何 QA、批量 XLSX API 與單檔 EXE 建置腳本。</p>`);
        }
      });
    });
    document.querySelectorAll("[data-workspace-focus]").forEach((el) => {
      el.addEventListener("click", () => focusSection(el.getAttribute("data-workspace-focus")));
    });
  }

  async function boot() {
    try {
      renderStageTabs();
      wireToolbar();
      wirePreviewZoom();
      $("dialogCloseBtn").addEventListener("click", hideDialog);
      $("dialogLayer").addEventListener("click", (ev) => {
        if (ev.target === $("dialogLayer")) hideDialog();
      });
      $("imageFileInput").addEventListener("change", async (ev) => {
        const file = ev.target.files && ev.target.files[0];
        if (!file) return;
        try {
          await uploadFile(file);
        } catch (err) {
          setStatus(err.message, "err");
        }
      });
      $("batchFileInput").addEventListener("change", (ev) => {
        state.batchFiles = Array.from(ev.target.files || []);
        $("batchStatus").textContent = state.batchFiles.length
          ? `已選擇 ${state.batchFiles.length} 張`
          : "尚未選擇";
        setStatus(
          state.batchFiles.length ? `已選擇 ${state.batchFiles.length} 張批量圖片` : "尚未選擇批量圖片",
          state.batchFiles.length ? "ok" : "neutral",
        );
      });
      $("loadSampleBtn").addEventListener("click", async () => {
        try { await loadSample(); } catch (err) { setStatus(err.message, "err"); }
      });
      $("processBtn").addEventListener("click", processImage);
      $("batchProcessBtn").addEventListener("click", processBatch);
      $("downloadBtn").addEventListener("click", downloadOverlay);
      $("paramFileInput").addEventListener("change", (ev) => {
        const file = ev.target.files && ev.target.files[0];
        if (file) importParamsFile(file);
        ev.target.value = "";
      });
      document.addEventListener("keydown", (ev) => {
        if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") {
          ev.preventDefault();
          processImage();
        }
      });
    } catch (err) {
      setStatus("介面初始化失敗", "err");
      console.error(err);
    }

    try {
      const health = await apiGet("/api/health");
      if (health.appVersion) $("appVersionBadge").textContent = "v" + health.appVersion;
      const missing = Object.entries(health.packages || {})
        .filter(([, v]) => !v.ok)
        .map(([k]) => k);
      if (missing.length) setStatus("缺少套件：" + missing.join(", "), "err");
      else setStatus("待機", "neutral");
    } catch (err) {
      setStatus("後端未連線", "err");
    }

    try {
      const tpl = await apiGet("/api/param-templates");
      if (!tpl.ok) throw new Error(tpl.error || "參數模板無效");
      state.templates = tpl.templates || [];
      state.paramValues = defaultParams();
      renderParams();
    } catch (err) {
      setStatus("無法載入參數模板", "err");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
