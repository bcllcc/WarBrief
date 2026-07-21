const state = { currentJobId: null, pollTimer: null, events: [] };
const $ = (id) => document.getElementById(id);

const stageLabels = {
  queued: "等待调度",
  load_event: "读取事件与来源",
  fact_pack: "构建事实包",
  script: "生成口播文稿",
  voice: "生成配音与时间轴",
  storyboard: "拆解镜头需求",
  materials: "检索合法素材",
  timeline: "编译视频时间线",
  render: "FFmpeg 自动渲染",
  quality_control: "执行自动质检",
  completed: "生产完成",
  completed_with_warnings: "成片完成，存在质检提醒",
  failed: "任务失败"
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
}

function setNotice(message, type = "info") {
  const notice = $("refreshNotice");
  notice.textContent = message;
  notice.classList.remove("hidden");
  notice.style.borderColor = type === "error" ? "rgba(242,122,113,.45)" : "#315269";
  setTimeout(() => notice.classList.add("hidden"), 6500);
}

async function loadConfig() {
  const config = await api("/api/config");
  const chips = [
    ["模式", config.demo_mode ? "MOCK" : "REAL", !config.demo_mode],
    ["LLM", config.llm_configured ? config.llm_provider : "MOCK", config.llm_configured],
    ["TTS", config.tts_provider, config.tts_provider !== "mock"],
    ["DVIDS", config.dvids_configured ? "READY" : "OFF", config.dvids_configured],
    ["视频", config.video_resolution, true]
  ];
  const root = $("statusChips");
  root.innerHTML = "";
  for (const [label, value, live] of chips) {
    const chip = document.createElement("div");
    chip.className = `status-chip ${live ? "live" : ""}`;
    chip.append(document.createTextNode(label));
    const strong = document.createElement("strong");
    strong.textContent = String(value).toUpperCase();
    chip.append(strong);
    root.append(chip);
  }
}

async function loadEvents() {
  const events = await api("/api/events?limit=30");
  state.events = events;
  const root = $("events");
  root.innerHTML = "";
  if (!events.length) {
    root.innerHTML = '<div class="empty-state">暂时没有事件。点击“刷新外网新闻”开始采集。</div>';
    return;
  }
  for (const event of events) {
    const card = document.createElement("article");
    card.className = "event-card";
    const content = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = event.title;
    const summary = document.createElement("p");
    summary.textContent = event.summary;
    const meta = document.createElement("div");
    meta.className = "event-meta";
    const sources = document.createElement("span");
    sources.textContent = `${event.source_names.length || 1} 个来源`;
    const time = document.createElement("span");
    time.textContent = formatTime(event.published_at);
    const score = document.createElement("span");
    score.className = "event-score";
    score.textContent = `SCORE ${Number(event.score).toFixed(1)}`;
    meta.append(sources, time, score);
    content.append(title, summary, meta);
    const action = document.createElement("div");
    action.className = "event-action";
    const button = document.createElement("button");
    button.className = "button button-primary";
    button.textContent = "生成视频";
    button.addEventListener("click", () => startJob({ event_id: event.id }, event.title));
    action.append(button);
    card.append(content, action);
    root.append(card);
  }
}

async function refreshNews() {
  const button = $("refreshButton");
  button.disabled = true;
  button.textContent = "正在采集…";
  try {
    const result = await api("/api/news/refresh", { method: "POST", body: "{}" });
    setNotice(`采集完成：${result.articles_collected} 条新闻，形成 ${result.events_created} 个候选事件。`);
    await loadEvents();
  } catch (error) {
    setNotice(`采集失败：${error.message}`, "error");
  } finally {
    button.disabled = false;
    button.textContent = "刷新外网新闻";
  }
}

async function startJob(payload, title = "新生产任务") {
  clearInterval(state.pollTimer);
  const job = await api("/api/jobs", { method: "POST", body: JSON.stringify(payload) });
  state.currentJobId = job.id;
  $("jobEmpty").classList.add("hidden");
  $("jobView").classList.remove("hidden");
  $("jobTitle").textContent = title;
  $("videoWrap").classList.add("hidden");
  $("jobError").classList.add("hidden");
  renderJob(job);
  state.pollTimer = setInterval(() => pollJob(job.id), 1500);
  await pollJob(job.id);
  await loadJobs();
}

function renderJob(job) {
  $("jobId").textContent = job.id;
  $("jobStatus").textContent = job.status.toUpperCase();
  $("jobStage").textContent = stageLabels[job.stage] || job.stage;
  $("jobProgress").textContent = `${job.progress}%`;
  $("progressBar").style.width = `${job.progress}%`;
  if (job.error) {
    $("jobError").textContent = job.error;
    $("jobError").classList.remove("hidden");
  } else {
    $("jobError").classList.add("hidden");
  }
  if (job.video_url) {
    $("videoPlayer").src = `${job.video_url}?t=${Date.now()}`;
    $("downloadVideo").href = job.video_url;
    $("downloadArtifacts").href = job.download_url || "#";
    $("videoWrap").classList.remove("hidden");
  }
}

async function pollJob(jobId) {
  try {
    const job = await api(`/api/jobs/${jobId}`);
    renderJob(job);
    if (["completed", "failed"].includes(job.status)) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
      await loadJobs();
    }
  } catch (error) {
    clearInterval(state.pollTimer);
    $("jobError").textContent = error.message;
    $("jobError").classList.remove("hidden");
  }
}

async function loadJobs() {
  const jobs = await api("/api/jobs?limit=12");
  const root = $("jobs");
  root.innerHTML = "";
  if (!jobs.length) {
    root.innerHTML = '<div class="empty-state">还没有生产记录。</div>';
    return;
  }
  for (const job of jobs) {
    const item = document.createElement("div");
    item.className = "job-item";
    const left = document.createElement("div");
    const id = document.createElement("strong");
    id.textContent = job.id;
    const stage = document.createElement("div");
    stage.textContent = `${stageLabels[job.stage] || job.stage} · ${formatTime(job.created_at)}`;
    left.append(id, stage);
    const status = document.createElement("span");
    status.className = job.status;
    status.textContent = `${job.status.toUpperCase()} ${job.progress}%`;
    item.append(left, status);
    item.addEventListener("click", async () => {
      state.currentJobId = job.id;
      $("jobEmpty").classList.add("hidden");
      $("jobView").classList.remove("hidden");
      $("jobTitle").textContent = `历史任务 · ${job.event_id}`;
      await pollJob(job.id);
    });
    root.append(item);
  }
}

$("refreshButton").addEventListener("click", refreshNews);
$("reloadJobs").addEventListener("click", loadJobs);
$("manualForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const title = $("manualTitle").value.trim();
  const payload = {
    manual_title: title,
    manual_summary: $("manualSummary").value.trim() || title,
    manual_source_url: $("manualUrl").value.trim() || null
  };
  try {
    await startJob(payload, title);
  } catch (error) {
    setNotice(`任务启动失败：${error.message}`, "error");
  }
});

Promise.all([loadConfig(), loadEvents(), loadJobs()]).catch((error) => {
  setNotice(`初始化失败：${error.message}`, "error");
});
setTimeout(loadEvents, 2500);
