# WarBrief API V0.1

服务启动后，交互式文档位于 `/docs`。

## GET `/api/health`

返回版本、时间和不含密钥的公开配置摘要。

## GET `/api/config`

返回前端状态条使用的公开配置。

## POST `/api/news/refresh`

立即执行一次新闻采集、去重、事件聚类和评分。

响应示例：

```json
{
  "articles_collected": 20,
  "events_created": 15,
  "providers_ok": ["gdelt", "rss"],
  "providers_failed": {},
  "refreshed_at": "2026-07-21T10:00:00Z"
}
```

## GET `/api/events?limit=30`

列出候选事件，按评分和时间倒序。

## GET `/api/events/{event_id}`

返回事件及其关联来源文章。

## POST `/api/jobs`

### 使用现有事件

```json
{
  "event_id": "event_xxx"
}
```

### 创建手工事件

```json
{
  "manual_title": "事件标题",
  "manual_summary": "已经确认的事实摘要",
  "manual_source_url": "https://example.com/source"
}
```

响应为初始 `JobRecord`。任务在服务端异步执行。

## GET `/api/jobs?limit=30`

列出最近任务。

## GET `/api/jobs/{job_id}`

返回任务状态。任务完成后额外返回：

```json
{
  "video_url": "/api/jobs/job_xxx/video",
  "download_url": "/api/jobs/job_xxx/download"
}
```

## POST `/api/jobs/{job_id}/retry`

基于原事件创建一个新任务，不覆盖历史任务。

## GET `/api/jobs/{job_id}/video`

下载或在线播放最终 MP4。

## GET `/api/jobs/{job_id}/download`

下载完整 ZIP 生产包。

## GET `/api/jobs/{job_id}/artifacts/{artifact_path}`

读取任务目录内的单个产物。接口包含目录穿越防护。

示例：

```text
/api/jobs/job_xxx/artifacts/fact_pack.json
/api/jobs/job_xxx/artifacts/subtitles.srt
/api/jobs/job_xxx/artifacts/qc_report.json
```
