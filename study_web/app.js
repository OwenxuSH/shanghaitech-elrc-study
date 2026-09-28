(() => {
  "use strict";
  const $ = (selector) => document.querySelector(selector);
  const ui = {
    course: $("#courseSelect"), lesson: $("#lessonSelect"), status: $("#status"),
    video: $("#video"), videoMessage: $("#videoMessage"), slideList: $("#slideList"),
    slideCount: $("#slideCount"), slideTitle: $("#slideTitle"), lessonLabel: $("#lessonLabel"),
    pageTime: $("#pageTime"), transcript: $("#transcriptList"), cueCount: $("#cueCount"),
    explain: $("#explainContent"), noteState: $("#noteState"), play: $("#playToggle"),
    back: $("#back10"), forward: $("#forward10"), speed: $("#speedSelect"),
    pagePause: $("#pagePause"), clock: $("#clock"),
    workspace: $(".workspace"),
    leftDivider: $("#leftDivider"), rightDivider: $("#rightDivider"),
    explainSection: $("#explainSection"), transcriptSection: $("#transcriptSection"),
    explainToggle: $("#explainToggle"), transcriptToggle: $("#transcriptToggle"),
  };
  let catalog = [];
  let lesson = null;
  let cues = [];
  let sentences = [];
  let pageIndex = -2;
  let activeSentence = -1;
  let lastBoundaryPause = -1;
  let previousPlaybackTime = 0;
  let loadSerial = 0;

  function togglePlayback() {
    if (ui.video.paused) ui.video.play().catch(() => {});
    else ui.video.pause();
  }

  const layoutKey = "elrc-study-layout-v2";
  const desktop = () => window.innerWidth > 1050;
  const mobile = () => window.innerWidth <= 700;
  const dimensions = () => ({
    left: ui.slideList.closest(".slide-panel").getBoundingClientRect().width,
    right: ui.explain.closest(".study-panel").getBoundingClientRect().width,
  });
  function setWidths(left, right) {
    if (mobile()) return;
    const available = ui.workspace.clientWidth - 72;
    const minCenter = 320;
    if (desktop()) {
      const maxSideTotal = Math.max(420, available - minCenter);
      const nextRight = Math.max(240, Math.min(right, maxSideTotal - 180));
      const nextLeft = Math.max(180, Math.min(left, maxSideTotal - nextRight));
      ui.workspace.style.setProperty("--left-width", `${nextLeft}px`);
      ui.workspace.style.setProperty("--right-width", `${nextRight}px`);
    } else {
      ui.workspace.style.setProperty("--left-width", `${Math.max(180, Math.min(left, available - minCenter))}px`);
    }
  }
  function saveLayout() {
    const sizes = dimensions();
    try { localStorage.setItem(layoutKey, JSON.stringify(sizes)); } catch (_) { /* private mode */ }
  }
  function installDivider(divider, move) {
    divider.addEventListener("pointerdown", event => {
      if (event.button !== 0) return;
      event.preventDefault();
      const start = event.clientX;
      const sizes = dimensions();
      divider.setPointerCapture(event.pointerId);
      divider.classList.add("dragging");
      document.body.classList.add("resizing", "resizing-columns");
      const onMove = next => move(sizes, next.clientX - start);
      const onStop = () => {
        divider.removeEventListener("pointermove", onMove);
        divider.removeEventListener("pointerup", onStop);
        divider.removeEventListener("pointercancel", onStop);
        divider.classList.remove("dragging");
        document.body.classList.remove("resizing", "resizing-columns", "resizing-rows");
        saveLayout();
      };
      divider.addEventListener("pointermove", onMove);
      divider.addEventListener("pointerup", onStop);
      divider.addEventListener("pointercancel", onStop);
    });
  }
  installDivider(ui.leftDivider, (sizes, delta) => setWidths(sizes.left + delta, sizes.right));
  installDivider(ui.rightDivider, (sizes, delta) => setWidths(sizes.left, sizes.right - delta));
  for (const divider of [ui.leftDivider, ui.rightDivider]) {
    divider.addEventListener("keydown", event => {
      const step = event.shiftKey ? 60 : 24;
      const sizes = dimensions();
      if (["ArrowLeft", "ArrowRight"].includes(event.code)) {
        event.preventDefault();
        const delta = event.code === "ArrowRight" ? step : -step;
        if (divider === ui.leftDivider) setWidths(sizes.left + delta, sizes.right);
        else setWidths(sizes.left, sizes.right - delta);
      } else return;
      saveLayout();
    });
  }
  try {
    const saved = JSON.parse(localStorage.getItem(layoutKey) || "null");
    if (saved && Number.isFinite(saved.left) && Number.isFinite(saved.right)) setWidths(saved.left, saved.right);
  } catch (_) { /* no usable saved layout */ }
  window.addEventListener("resize", () => {
    const sizes = dimensions();
    if (ui.workspace.style.getPropertyValue("--left-width")) setWidths(sizes.left, sizes.right);
  });

  function setSectionOpen(section, toggle, open) {
    section.classList.toggle("expanded", open);
    toggle.setAttribute("aria-expanded", String(open));
    if (open && section === ui.transcriptSection) requestAnimationFrame(() => scrollTranscriptTo(ui.video.currentTime));
  }
  ui.explainToggle.addEventListener("click", () => setSectionOpen(ui.explainSection, ui.explainToggle, !ui.explainSection.classList.contains("expanded")));
  ui.transcriptToggle.addEventListener("click", () => setSectionOpen(ui.transcriptSection, ui.transcriptToggle, !ui.transcriptSection.classList.contains("expanded")));

  const timeText = (seconds) => {
    const n = Math.max(0, Math.floor(Number(seconds) || 0));
    const hours = Math.floor(n / 3600);
    const minutes = Math.floor((n % 3600) / 60);
    const secs = n % 60;
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  };
  const seconds = (stamp) => {
    const parts = stamp.replace(",", ".").split(":").map(Number);
    return parts.reduce((value, part) => value * 60 + part, 0);
  };
  const clean = (raw) => raw.replace(/<br\s*\/?\s*>/gi, " ").replace(/<[^>]*>/g, "")
    .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
    .replace(/\s+/g, " ").trim();
  function parseVtt(raw) {
    const blocks = raw.replace(/^\uFEFF/, "").replace(/\r\n/g, "\n").split(/\n\s*\n/);
    const out = [];
    for (const block of blocks) {
      const lines = block.split("\n");
      const pos = lines.findIndex(line => line.includes("-->"));
      if (pos < 0) continue;
      const match = lines[pos].match(/(\d{2}:\d{2}:\d{2}[.,]\d+)\s+-->\s+(\d{2}:\d{2}:\d{2}[.,]\d+)/);
      if (!match) continue;
      const text = clean(lines.slice(pos + 1).join(" "));
      if (text) out.push({start: seconds(match[1]), end: seconds(match[2]), text});
    }
    return out;
  }
  function pageAt(t) {
    if (!lesson || !lesson.pages.length) return -1;
    const pages = lesson.pages;
    let low = 0, high = pages.length - 1, candidate = -1;
    while (low <= high) {
      const mid = (low + high) >> 1;
      if (pages[mid].start <= t) { candidate = mid; low = mid + 1; }
      else high = mid - 1;
    }
    return candidate >= 0 && t < pages[candidate].end ? candidate : -1;
  }
  function sentenceAt(t) {
    let low = 0, high = sentences.length - 1, candidate = -1;
    while (low <= high) {
      const mid = (low + high) >> 1;
      if (sentences[mid].start <= t) { candidate = mid; low = mid + 1; }
      else high = mid - 1;
    }
    return candidate >= 0 && t < sentences[candidate].end + 0.6 ? candidate : -1;
  }
  function make(tag, cls, value) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (value !== undefined) el.textContent = value;
    return el;
  }
  function seek(t, shouldPlay = false) {
    if (!lesson) return;
    ui.video.currentTime = Math.max(0, Math.min(t, Number.isFinite(ui.video.duration) ? ui.video.duration : t));
    sync();
    if (shouldPlay) ui.video.play().catch(() => {});
  }
  function renderSlides() {
    ui.slideList.replaceChildren();
    const fragment = document.createDocumentFragment();
    for (const [i, page] of lesson.pages.entries()) {
      const button = make("button", "slide-item");
      button.type = "button";
      button.dataset.index = String(i);
      button.setAttribute("aria-label", `跳转到 ${page.heading}，${timeText(page.start)}`);
      const thumb = make("img", "slide-thumb");
      thumb.src = page.image_url;
      thumb.loading = "lazy";
      thumb.alt = "";
      const meta = make("span", "slide-meta");
      meta.append(make("strong", "", page.heading.replace(/[，,]\s*(?:约\s*)?\d+:\d+$/, "")),
                  make("small", "", `${String(i + 1).padStart(2, "0")} · ${timeText(page.start)}–${timeText(page.end)}`));
      button.append(thumb, meta);
      button.addEventListener("click", () => seek(page.start, !ui.video.paused));
      fragment.append(button);
    }
    ui.slideList.append(fragment);
    ui.slideCount.textContent = `${lesson.pages.length} 页`;
  }
  function buildSentences() {
    const result = [];
    let current = null;
    for (const cue of cues) {
      if (!current || cue.start - current.end > 2.4 || current.text.length > 70) {
        current = {start: cue.start, end: cue.end, text: cue.text};
        result.push(current);
      } else {
        current.text += cue.text;
        current.end = cue.end;
      }
      if (/[。！？!?；;]$/.test(cue.text) && current.text.length >= 12) current = null;
    }
    return result;
  }
  function renderTranscript() {
    sentences = buildSentences();
    activeSentence = -1;
    ui.transcript.replaceChildren();
    ui.cueCount.textContent = `${sentences.length} 句`;
    if (!sentences.length) {
      ui.transcript.append(make("p", "empty", "这节课没有可用的转写。"));
      return;
    }
    const fragment = document.createDocumentFragment();
    let paragraph = null, chars = 0, count = 0, previous = null;
    sentences.forEach((sentence, i) => {
      if (!paragraph || chars >= 135 || count >= 5 || (previous && sentence.start - previous.end > 6)) {
        paragraph = make("p", "transcript-paragraph");
        paragraph.append(make("span", "paragraph-time", timeText(sentence.start)));
        fragment.append(paragraph);
        chars = 0; count = 0;
      }
      const button = make("button", "sentence", sentence.text);
      button.type = "button";
      button.dataset.index = String(i);
      button.dataset.start = String(sentence.start);
      button.title = `跳转到 ${timeText(sentence.start)}`;
      button.setAttribute("aria-label", `${timeText(sentence.start)} ${sentence.text}`);
      button.addEventListener("click", () => seek(sentence.start, !ui.video.paused));
      paragraph.append(button, document.createTextNode(" "));
      chars += sentence.text.length;
      count += 1;
      previous = sentence;
    });
    ui.transcript.append(fragment);
  }
  function renderExplanation(page) {
    ui.explain.replaceChildren();
    if (!page) {
      ui.noteState.textContent = "未索引片段";
      ui.explain.append(make("p", "empty", "当前视频位置尚未对应到筛选后的课件页。视频和转写仍可正常学习；选择左侧页面可以跳转到课件内容。"));
      return;
    }
    ui.explain.append(make("h3", "", page.heading.replace(/[，,]\s*(?:约\s*)?\d+:\d+$/, "")));
    const image = make("img");
    image.src = page.image_url;
    image.alt = `课件页面 ${page.number}`;
    ui.explain.append(image, make("span", "time-badge", `${timeText(page.start)}–${timeText(page.end)}`));
    if (page.detail && page.detail.sections && page.detail.sections.length) {
      ui.noteState.textContent = "本地详细讲解";
      if (page.detail.lead) ui.explain.append(make("p", "lead", page.detail.lead));
      for (const section of page.detail.sections) {
        ui.explain.append(make("h4", "", section.heading), make("p", "", section.body));
      }
      if (page.detail.caution) ui.explain.append(make("p", "notice", page.detail.caution));
    } else {
      ui.noteState.textContent = "讲解待补全";
      ui.explain.append(make("p", "lead", page.summary));
      ui.explain.append(make("p", "notice", "这一页目前只有原有提要，详细讲解尚未生成。可先结合下方老师原话和页面画面学习。"));
    }
    ui.explain.append(make("p", "source-note", "讲解依据本地录屏页面和 ELRC 转写整理；识别错误或未讲清的内容应以原视频为准。"));
  }
  function syncSentence(t) {
    const i = sentenceAt(t);
    if (i === activeSentence) return;
    const old = ui.transcript.querySelector(".sentence.active");
    if (old) old.classList.remove("active");
    activeSentence = i;
    if (i >= 0) {
      const current = ui.transcript.querySelector(`.sentence[data-index="${i}"]`);
      if (current) {
        current.classList.add("active");
        if (ui.transcriptSection.classList.contains("expanded")) current.scrollIntoView({block: "nearest"});
      }
    }
  }
  function scrollTranscriptTo(t) {
    if (!sentences.length || !ui.transcriptSection.classList.contains("expanded")) return;
    let low = 0, high = sentences.length;
    while (low < high) {
      const mid = (low + high) >> 1;
      if (sentences[mid].start < t) low = mid + 1;
      else high = mid;
    }
    const index = activeSentence >= 0 ? activeSentence : Math.min(low, sentences.length - 1);
    const target = ui.transcript.querySelector(`.sentence[data-index="${index}"]`);
    if (target) target.scrollIntoView({block: "center"});
  }
  function sync() {
    if (!lesson) return;
    const t = ui.video.currentTime;
    const previousPage = pageAt(previousPlaybackTime);
    if (ui.pagePause.checked && !ui.video.paused && previousPage >= 0 && previousPage !== lastBoundaryPause) {
      const end = lesson.pages[previousPage].end;
      if (previousPlaybackTime < end - 0.05 && t >= end - 0.05 && t - previousPlaybackTime < 5) {
        lastBoundaryPause = previousPage;
        ui.video.pause();
        ui.video.currentTime = Math.max(lesson.pages[previousPage].start, end - 0.05);
        previousPlaybackTime = ui.video.currentTime;
        return;
      }
    }
    previousPlaybackTime = t;
    ui.clock.textContent = `${timeText(t)} / ${timeText(ui.video.duration || lesson.duration)}`;
    const i = pageAt(t);
    const pageChanged = i !== pageIndex;
    if (pageChanged) {
      pageIndex = i;
      for (const button of ui.slideList.querySelectorAll(".slide-item.active")) button.classList.remove("active");
      const button = i >= 0 ? ui.slideList.querySelector(`.slide-item[data-index="${i}"]`) : null;
      if (button) { button.classList.add("active"); button.scrollIntoView({block: "nearest"}); }
      const page = i >= 0 ? lesson.pages[i] : null;
      ui.slideTitle.textContent = page ? page.heading.replace(/[，,]\s*(?:约\s*)?\d+:\d+$/, "") : "未索引的课堂画面";
      ui.pageTime.textContent = page ? `${timeText(page.start)}–${timeText(page.end)}` : "—";
      renderExplanation(page);
    }
    syncSentence(t);
    if (pageChanged) scrollTranscriptTo(t);
  }
  async function loadLesson(code, slug) {
    const serial = ++loadSerial;
    ui.status.textContent = "加载课堂中…";
    ui.video.pause();
    ui.video.removeAttribute("src");
    ui.video.load();
    const response = await fetch(`/api/lesson?course=${encodeURIComponent(code)}&lesson=${encodeURIComponent(slug)}`);
    if (!response.ok) throw new Error(`课堂数据请求失败：${response.status}`);
    const loaded = await response.json();
    const vttResponse = await fetch(loaded.transcript_url);
    if (!vttResponse.ok) throw new Error(`转写请求失败：${vttResponse.status}`);
    const loadedCues = parseVtt(await vttResponse.text());
    if (serial !== loadSerial) return;
    lesson = loaded;
    cues = loadedCues;
    pageIndex = -2; activeSentence = -1; lastBoundaryPause = -1; previousPlaybackTime = 0;
    ui.lessonLabel.textContent = loaded.title;
    ui.video.src = loaded.video_url;
    ui.video.playbackRate = Number(ui.speed.value);
    ui.video.load();
    renderSlides();
    renderTranscript();
    sync();
    ui.status.textContent = `${loaded.pages.length} 页 · ${loadedCues.length} 条转写`;
    const url = new URL(location.href);
    url.searchParams.set("course", code);
    url.searchParams.set("lesson", slug);
    history.replaceState(null, "", url);
  }
  function selectCourse(code, wantedLesson = null) {
    const course = catalog.find(item => item.code === code);
    if (!course) return;
    ui.course.value = code;
    ui.lesson.replaceChildren();
    for (const item of course.lessons) {
      const option = make("option", "", item.title);
      option.value = item.id;
      ui.lesson.append(option);
    }
    const target = course.lessons.find(item => item.id === wantedLesson) || course.lessons[0];
    if (!target) return;
    ui.lesson.value = target.id;
    loadLesson(code, target.id).catch(error => { ui.status.textContent = error.message; console.error(error); });
  }
  async function init() {
    const response = await fetch("/api/catalog");
    if (!response.ok) throw new Error(`目录请求失败：${response.status}`);
    catalog = (await response.json()).courses;
    for (const course of catalog) {
      const option = make("option", "", `${course.code} · ${course.name}`);
      option.value = course.code;
      ui.course.append(option);
    }
    const query = new URLSearchParams(location.search);
    selectCourse(query.get("course") || catalog[0].code, query.get("lesson"));
  }
  ui.course.addEventListener("change", () => selectCourse(ui.course.value));
  ui.lesson.addEventListener("change", () => loadLesson(ui.course.value, ui.lesson.value).catch(error => { ui.status.textContent = error.message; }));
  ui.play.addEventListener("click", togglePlayback);
  ui.back.addEventListener("click", () => seek(ui.video.currentTime - 10));
  ui.forward.addEventListener("click", () => seek(ui.video.currentTime + 10));
  ui.speed.addEventListener("change", () => { ui.video.playbackRate = Number(ui.speed.value); });
  ui.video.addEventListener("timeupdate", sync);
  ui.video.addEventListener("seeking", () => { previousPlaybackTime = ui.video.currentTime; });
  ui.video.addEventListener("seeked", () => { sync(); scrollTranscriptTo(ui.video.currentTime); });
  ui.video.addEventListener("loadedmetadata", sync);
  ui.video.addEventListener("play", () => { ui.play.textContent = "暂停"; });
  ui.video.addEventListener("pause", () => { ui.play.textContent = "播放"; });
  ui.video.addEventListener("error", () => { ui.videoMessage.hidden = false; ui.videoMessage.textContent = "视频无法播放，请检查本地文件或浏览器支持的编码。"; });
  ui.video.addEventListener("loadeddata", () => { ui.videoMessage.hidden = true; });
  document.addEventListener("keydown", (event) => {
    const active = document.activeElement;
    if (["INPUT", "SELECT", "TEXTAREA"].includes(active.tagName) || active.isContentEditable) return;
    if (event.code === "Space") {
      event.preventDefault();
      event.stopPropagation();
      if (!event.repeat) togglePlayback();
      return;
    }
    if (active.tagName === "BUTTON" || active.classList.contains("divider")) return;
    if (event.code === "ArrowLeft") { event.preventDefault(); event.stopPropagation(); seek(ui.video.currentTime - 5); }
    if (event.code === "ArrowRight") { event.preventDefault(); event.stopPropagation(); seek(ui.video.currentTime + 5); }
  }, true);
  document.addEventListener("keyup", event => {
    if (event.code !== "Space") return;
    const active = document.activeElement;
    if (["INPUT", "SELECT", "TEXTAREA"].includes(active.tagName) || active.isContentEditable) return;
    event.preventDefault();
    event.stopPropagation();
  }, true);
  init().catch(error => { ui.status.textContent = error.message; console.error(error); });
})();
