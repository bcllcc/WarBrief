# WarBrief Windows 原生部署

WarBrief 不强制使用 Docker。Windows 10/11 可以直接采用 Python 虚拟环境运行。

## 最简单的方式

解压完整源码后，双击：

```text
Start-WarBrief-Windows.bat
```

脚本会执行以下操作：

1. 检测 Python 3.11/3.12。
2. 检测 FFmpeg 和 ffprobe。
3. 缺少前置软件时，可选择通过 WinGet 安装。
4. 创建 `.venv` 虚拟环境。
5. 安装 WarBrief 依赖。
6. 第一次运行时复制 `.env.example` 为 `.env`。
7. 启动 Web 控制台并打开浏览器。

默认地址：

```text
http://127.0.0.1:8000
```

停止服务：在启动窗口按 `Ctrl+C`。

## 只验证完整离线流程

双击：

```text
Run-WarBrief-Demo-Windows.bat
```

它会生成一条零密钥测试视频，结果写入：

```text
data\runtime\jobs\<job_id>\render\video.mp4
```

## 手工安装前置软件

脚本自动安装失败时，在 PowerShell 执行：

```powershell
winget install --id Python.Python.3.12 -e --source winget
winget install --id Gyan.FFmpeg -e --source winget
```

安装后关闭当前终端，重新双击启动脚本。

验证：

```powershell
python --version
ffmpeg -version
ffprobe -version
```

## 配置真实模式

首次运行后，编辑项目根目录 `.env`：

```dotenv
DEMO_MODE=false

LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://你的服务地址/v1
LLM_API_KEY=你的密钥
LLM_MODEL=你的模型名称

TTS_PROVIDER=edge
TTS_VOICE=zh-CN-YunxiNeural

DVIDS_ENABLED=true
DVIDS_API_KEY=你的DVIDS密钥
WIKIMEDIA_ENABLED=true
MEDIA_ALLOW_PLACEHOLDER=true
```

重新启动 `Start-WarBrief-Windows.bat` 后生效。

## 常见问题

### `ffmpeg` 或 `ffprobe` 未找到

安装完成后需要重新打开终端。若仍未识别，确认 FFmpeg 的 `bin` 目录已经加入用户 `PATH`。

### 端口 8000 被占用

在 PowerShell 中运行：

```powershell
.\Start-WarBrief-Windows.bat -Port 8010
```

然后访问 `http://127.0.0.1:8010`。

### 依赖已经安装，不希望每次检查

```powershell
.\Start-WarBrief-Windows.bat -SkipDependencyInstall
```

### Edge TTS 或外网新闻无法访问

在 `.env` 中填写代理：

```dotenv
HTTP_PROXY_URL=http://127.0.0.1:7890
```

### 完全重装 Python 环境

删除项目目录中的 `.venv`，再次运行启动脚本即可。
