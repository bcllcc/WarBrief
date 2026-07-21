# WarBrief V0.1 架构说明

## 1. 目标

WarBrief 把军事资讯短视频制作建模为一个可恢复、可审计的数据编译流程，而不是一个自由对话 Agent。

```text
News Inputs
   ↓
Article Normalization
   ↓
Dedupe / Event Clustering
   ↓
FactPack
   ↓
ScriptManifest
   ↓
VoiceManifest
   ↓
ShotRequest[]
   ↓
MediaAsset[] + RightsManifest
   ↓
Timeline
   ↓
FFmpeg Renderer
   ↓
QCReport + MP4 + Artifact ZIP
```

## 2. 为什么首版使用模块化单体

V0.1 的主要风险是内容质量、外部 API 稳定性和视频渲染，而不是水平扩展。模块化单体具有以下优势：

- 一条命令即可本地启动。
- SQLite 足以保存 Demo 事件与任务。
- 外部 Provider 通过稳定接口隔离，后续可以替换。
- 所有阶段仍有结构化边界，未来可迁移到 PostgreSQL、Temporal 和独立 Worker。

## 3. 核心数据契约

### 3.1 NewsArticle

单条外部信息。保存标准化 URL、标题、摘要、正文、发布时间、来源和原始字段。

### 3.2 NewsEvent

多个近似报道形成的事件簇。当前使用标题 Token Jaccard 进行轻量聚类，并结合新鲜度、来源数量和军事关键词评分。

### 3.3 FactPack

只包含来源材料直接支持的事实：

```json
{
  "event_id": "event_xxx",
  "sources": [
    {
      "source_id": "source_01",
      "url": "...",
      "quote": "原始证据片段"
    }
  ],
  "claims": [
    {
      "claim_id": "claim_01",
      "statement": "可进入口播的事实",
      "evidence_source_ids": ["source_01"],
      "confidence": 0.86,
      "disputed": false
    }
  ]
}
```

### 3.4 ScriptManifest

每一句口播必须引用至少一个有效 `claim_id`，同时给出英文素材检索词和画面类型。

### 3.5 VoiceManifest

TTS 按句生成音频。真实音频时长决定镜头起止时间，因此配音是时间线的主时钟。

### 3.6 ShotRequest

将文稿句子转换为素材请求：

```json
{
  "sentence_id": "sentence_02",
  "start_ms": 5410,
  "end_ms": 10220,
  "duration_ms": 4810,
  "query": "fighter aircraft military exercise official footage",
  "visual_type": "event"
}
```

### 3.7 MediaAsset 与 RightsManifest

`MediaAsset` 描述真实进入渲染的本地文件；`RightsManifest` 保存来源、许可、署名、是否允许渲染和是否需要人工复核。

`render_allowed=false` 会硬阻断任务。

### 3.8 Timeline

Timeline 是唯一允许进入渲染器的输入。LLM 不直接操作 FFmpeg。

### 3.9 QCReport

当前自动检查：

- MP4 是否存在
- 目标分辨率
- 音频流
- 时长偏差
- 版权门禁
- 字幕文件

## 4. Provider 边界

### NewsProvider

```python
async def fetch() -> list[NewsArticle]
```

实现：

- `GDELTNewsProvider`
- `RSSNewsProvider`
- `MockNewsProvider`

### LLMProvider

```python
async def build_fact_pack(event, articles) -> FactPack
async def build_script(event, fact_pack, max_sentences) -> ScriptManifest
```

实现：

- `MockLLMProvider`
- `OpenAICompatibleLLMProvider`

真实 LLM 失败时，会退回规则引擎，保证 Demo 可继续运行。

### TTSProvider

```python
async def synthesize(text, output_wav) -> None
```

实现：

- `MockTTSProvider`
- `EdgeTTSProvider`
- `OpenAICompatibleTTSProvider`

单句失败时退回本地 Mock TTS。

### MediaProvider

```python
async def find(shot, output_dir, used_urls) -> MediaAsset | None
```

实现：

- `DVIDSMediaProvider`
- `WikimediaMediaProvider`
- `PlaceholderMediaProvider`

素材按 Provider 顺序选择，并阻止同一来源 URL 在一条视频中重复使用。

## 5. 新闻发现与成片素材分离

### Discovery Sources

用于发现和事实证据：GDELT、RSS、手工 URL。

### Renderable Sources

用于最终视频：DVIDS、Wikimedia、项目生成素材。

新闻网页中的图片和视频不会自动下载进入成片。这一边界是版权治理的最低要求。

## 6. 外部失败策略

| 环节 | 失败策略 |
|---|---|
| GDELT/RSS | 记录 Provider 错误；允许其他源继续；可回退 Mock 新闻 |
| 网页正文抽取 | 保留 RSS/GDELT 摘要作为证据 |
| LLM | 回退规则 FactPack 与文稿 |
| 单句 TTS | 回退 Mock TTS |
| DVIDS | 继续 Wikimedia |
| Wikimedia | 继续本地占位素材 |
| Rights Gate | 不降级，直接阻断渲染 |
| FFmpeg | 任务失败并保存 `error.txt` 与产物 ZIP |

## 7. 任务状态机

```text
queued
→ load_event
→ fact_pack
→ script
→ voice
→ storyboard
→ materials
→ timeline
→ render
→ quality_control
→ completed / failed
```

每个任务使用独立不可变目录，失败后仍可下载中间产物定位问题。

## 8. 向生产架构迁移

V0.1 接口保持不变的前提下，可逐步替换：

```text
SQLite              → PostgreSQL
asyncio task         → Temporal Workflow + Worker
本地 jobs 目录        → S3 / MinIO / SeaweedFS
标题 Jaccard          → BGE-M3 + 在线事件聚类
临时素材检索          → 镜头级媒体湖 + Qdrant
规则 QC               → 黑帧/静帧/字幕溢出/内容一致性 QC
零构建 Web            → Next.js 审核与时间线界面
```

优先级应当是：先验证视频质量和素材匹配，再引入分布式基础设施。
