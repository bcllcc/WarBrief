# WarBrief

> 军事资讯采集、事实约束写稿、AI 配音、合法素材检索与 FFmpeg 自动成片的端到端 Demo。

WarBrief V0.1 的目标不是做一个通用剪辑器，而是验证一条可执行的 **News-to-Video Compiler**：

```text
外网新闻采集
  → 去重与事件聚类
  → 事实包 FactPack
  → 有来源约束的中文口播稿
  → 逐句配音与时间轴
  → 镜头需求 ShotRequest
  → 白名单素材检索与版权门禁
  → Timeline 编译
  → FFmpeg 渲染
  → 自动质检与完整生产包
```

当前版本已能从 Web 控制台或 CLI 跑通完整流程，并同时提供：

- **零密钥 Mock 模式**：离线新闻样本、本地规则写稿、本地配音回退、本地生成素材卡，保证工程闭环可复现。
- **真实新闻模式**：GDELT DOC 2.0 与可配置 RSS 自动采集，按周期刷新。
- **真实模型模式**：兼容 OpenAI Chat Completions 协议的 LLM 接口。
- **真实配音模式**：Edge TTS，或兼容 `/audio/speech` 的 TTS 接口。
- **真实素材模式**：DVIDS 视频/图片、Wikimedia Commons 文件级许可筛选。
- **确定性渲染**：FFmpeg、ASS 字幕、SRT 字幕、配音、可选 BGM、竖屏 H.264 MP4。
- **可追溯产物**：事实、来源、文稿、配音时间轴、素材许可、时间线与质检均保存为 JSON。

## 1. 最快启动：Docker

### 1.1 准备配置

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

保持 `.env` 中以下配置即可运行零密钥 Demo：

```dotenv
DEMO_MODE=true
LLM_PROVIDER=mock
TTS_PROVIDER=mock
```

### 1.2 启动

```bash
docker compose up --build
```

打开：

- 控制台：`http://localhost:8000`
- OpenAPI：`http://localhost:8000/docs`
- 健康检查：`http://localhost:8000/api/health`

> 安全提示：V0.1 没有用户认证与访问控制，只应运行在本机或可信内网，不要直接暴露到公网。

点击“刷新外网新闻”后，Mock 模式会载入内置军事新闻样本；选择任意事件即可自动生成完整视频。

## 2. 本机 Python 启动

要求：

- Python 3.11+
- FFmpeg 与 ffprobe
- 推荐安装 `espeak-ng`，仅用于无密钥演示配音
- 推荐安装 Noto CJK 字体

```bash
python -m venv .venv
source .venv/bin/activate          # Linux / macOS
# .venv\Scripts\activate           # Windows PowerShell

python -m pip install -e ".[dev]"
cp .env.example .env
warbrief serve --reload
```

Windows 用户建议优先使用 Docker，避免 FFmpeg、字体和本地 TTS 的环境差异。

## 3. 一条命令跑完整离线 Demo

```bash
warbrief demo
```

或者指定一条手工事件：

```bash
warbrief demo --title "某型无人机完成公开飞行测试，官方公布训练画面"
```

`demo` 命令会强制使用 Mock 模式，不受本地 `.env` 中真实密钥配置影响。成功后终端会打印：

```text
Video:    .../render/video.mp4
Artifacts: ...-artifacts.zip
```

## 4. 切换真实新闻、模型、配音和素材

编辑 `.env`：

```dotenv
DEMO_MODE=false

# 外网新闻
NEWS_GDELT_ENABLED=true
NEWS_RSS_URLS=
NEWS_POLL_INTERVAL_MINUTES=30
NEWS_LOOKBACK_HOURS=24
NEWS_RETENTION_DAYS=14

# 任意 OpenAI-compatible Chat Completions 服务
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://your-llm-provider.example/v1
LLM_API_KEY=填入你的密钥（本地兼容服务可留空）
LLM_MODEL=填入模型名

# 中文配音：无需 API Key 的在线 Edge TTS
TTS_PROVIDER=edge
TTS_VOICE=zh-CN-YunxiNeural
TTS_RATE=+0%

# DVIDS 素材
DVIDS_API_KEY=填入你的 DVIDS API Key
DVIDS_ENABLED=true
DVIDS_PREFER_VIDEO=true

# Wikimedia 作为后备素材源
WIKIMEDIA_ENABLED=true
MEDIA_ALLOW_PLACEHOLDER=true
```

也可以使用兼容 OpenAI `/audio/speech` 的语音服务：

```dotenv
TTS_PROVIDER=openai_compatible
TTS_BASE_URL=https://your-tts-provider.example/v1
TTS_API_KEY=填入你的密钥
TTS_MODEL=填入模型名
TTS_VOICE=填入音色名
```

外网访问需要代理时：

```dotenv
HTTP_PROXY_URL=http://127.0.0.1:7890
```

### 当前已接入的新闻源

1. **GDELT DOC 2.0**：根据军事关键词检索最近时间窗内的全球报道。
2. **RSS**：支持任意 RSS/Atom 地址；默认示例为公开防务信息 Feed。
3. **手工事件**：直接输入标题、已确认事实摘要和来源链接。

系统会执行 URL 去重、标题近似去重、时间衰减评分和轻量事件聚类。V0.1 尚未加入向量模型，因此跨语言、跨表述聚类仍属于近似结果。

## 5. 版权与事实边界

WarBrief 将两类来源明确分离：

```text
发现源：用于发现、研究和形成事实证据
成片源：经过明确白名单与资产级许可检查后，才允许进入时间线
```

默认成片源：

- DVIDS
- Wikimedia Commons
- WarBrief 本地生成的占位素材

每个素材都写入 `rights_manifest.json`：

```json
{
  "asset_id": "asset_xxx",
  "provider": "dvids",
  "source_url": "...",
  "page_url": "...",
  "license": "...",
  "attribution": "...",
  "render_allowed": true,
  "rights_review_required": true
}
```

需要明确：

- 下载成功不等于拥有使用权。
- DVIDS 与 Wikimedia 均可能存在资产级限制或第三方内容。
- V0.1 的关键词筛查是保守门禁，不构成法律意见，也不能替代发布前人工复核。
- 新闻网页、YouTube、X、Telegram 等默认只作为发现源，不自动进入成片。
- Mock 模式生成的素材卡只用于工程测试，不能代表真实新闻现场。

## 6. 每条任务的产物

任务目录位于：

```text
data/runtime/jobs/<job_id>/
```

主要产物：

```text
event.json                 原始事件
sources.json               新闻来源与正文证据
fact_pack.json             可引用事实与证据映射
script_manifest.json       逐句口播稿与 claim_id
voice_manifest.json        逐句音频、起止时间、停顿
shot_requests.json         每句话需要的画面
media_assets.json          实际选中的素材
rights_manifest.json       素材许可与署名信息
subtitles.ass              烧录字幕
subtitles.srt              通用字幕
timeline.json              确定性视频时间线
qc_report.json             分辨率、音轨、时长、版权门禁等质检
render/video.mp4           最终成片
```

任务完成后，控制台可下载完整 ZIP 生产包。

## 7. API

核心接口：

```text
GET  /api/health
GET  /api/config
POST /api/news/refresh
GET  /api/events
GET  /api/events/{event_id}
POST /api/jobs
GET  /api/jobs
GET  /api/jobs/{job_id}
POST /api/jobs/{job_id}/retry
GET  /api/jobs/{job_id}/video
GET  /api/jobs/{job_id}/download
```

创建现有事件任务：

```bash
curl -X POST http://localhost:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{"event_id":"event_xxx"}'
```

创建手工事件任务：

```bash
curl -X POST http://localhost:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "manual_title":"某型无人机完成公开测试",
    "manual_summary":"官方公开材料确认了测试时间、地点和任务性质。",
    "manual_source_url":"https://example.com/source"
  }'
```

完整接口定义见 [docs/API.md](docs/API.md)。

## 8. 工程结构

```text
WarBrief/
├── warbrief/
│   ├── api.py                 FastAPI 与 Web 控制台
│   ├── config.py              环境变量与运行配置
│   ├── models.py              核心 Pydantic 数据契约
│   ├── storage.py             SQLite 存储
│   ├── providers/
│   │   ├── news.py            GDELT / RSS / Mock 新闻
│   │   ├── llm.py             Mock / OpenAI-compatible LLM
│   │   ├── tts.py             Mock / Edge / API TTS
│   │   └── media.py           DVIDS / Wikimedia / Placeholder
│   ├── services/
│   │   ├── news.py            去重、聚类、评分、正文补全
│   │   ├── pipeline.py        端到端状态机
│   │   └── render.py          字幕、时间线、FFmpeg、QC
│   └── web/                   零构建前端
├── tests/
├── docs/
├── scripts/
├── Dockerfile
└── docker-compose.yml
```

架构与中间数据契约见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 9. 测试

```bash
pytest -q
```

端到端测试会使用小分辨率、Mock 新闻、Mock LLM、Mock TTS 和本地素材，真实执行 FFmpeg 并验证：

- MP4 存在且包含视频和音频流
- 分辨率正确
- 时长与 VoiceManifest 接近
- ASS/SRT、FactPack、RightsManifest、Timeline、QC 均生成
- 最终 ZIP 可解压

代码检查：

```bash
ruff check .
```

## 10. V0.1 已完成与未完成

### 已完成

- 外网新闻定时采集与手动刷新
- RSS、GDELT、Mock 三种新闻输入
- 新闻去重、聚类、评分
- 证据约束 FactPack
- OpenAI-compatible LLM 适配层
- 逐句中文配音与 VoiceManifest
- DVIDS 视频短片截取、DVIDS 图片、Wikimedia 图片
- 资产级版权字段和硬门禁
- ASS/SRT 字幕
- 确定性 Timeline
- FFmpeg 竖屏自动成片
- QC 与完整产物 ZIP
- Web 控制台、CLI、Docker、CI 测试

### 尚未完成

- 新闻事件的向量聚类与多语言归并
- 对“单一来源、冲突来源、官方来源”的更严格可信度模型
- 真正的镜头级媒体湖、视觉向量检索与素材复用
- 地图动画、战线图、装备参数卡等动态图形
- 文稿人工修改、逐镜头替换和时间线可视化编辑器
- 专属声音克隆
- 全自动发布与账号数据分析

这些未完成项不会阻塞 V0.1 的完整 Demo，但属于后续产品化阶段。

## 11. 开发原则

WarBrief 不让大模型直接生成 FFmpeg 命令。模型只输出结构化中间结果：

```text
FactPack
→ ScriptManifest
→ VoiceManifest
→ ShotRequest
→ MediaAsset / RightsManifest
→ Timeline
→ MP4 / QCReport
```

每一步都可以被保存、检查、重试和替换。这是系统后续扩展为生产级内容工厂的基础。

## License

MIT。第三方模型、API、素材和数据源继续受各自条款约束。
