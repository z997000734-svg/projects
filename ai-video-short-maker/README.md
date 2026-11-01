# AI 视频短片助手

一个独立编写的 Windows 本地视频处理小工具。输入视频后，可自动提取音频并用 faster-whisper 生成字幕，再按静音区间切分为短片，烧录中文字幕并导出竖屏 MP4。工具不上传视频。

## 功能
- 图形界面选择视频、输出目录、字幕语言与单段时长
- Whisper 本地语音识别，支持 CPU / CUDA
- 根据语音时间戳选择内容片段；无语音时按时长切分
- FFmpeg 自动裁切、缩放、字幕烧录、导出 MP4
- 任务日志与错误提示

## Windows 快速开始
1. 安装 Python 3.10–3.12，并安装 FFmpeg，确保 `ffmpeg`、`ffprobe` 可从 PowerShell 调用。
2. 在本目录运行 `python -m venv .venv`，然后 `.venv\\Scripts\\Activate.ps1`。
3. 安装依赖：`pip install -r requirements.txt`
4. 启动：`python app.py`
5. 首次运行会下载 Whisper 模型；建议先选 `tiny` 做小视频试跑。模型下载需要网络。

## 说明
- 首次处理会下载所选 Whisper 模型；可在界面选择 tiny/base/small/medium。
- CPU 可以运行但速度较慢；GPU 推理需要匹配的 PyTorch/CUDA 环境。
- 默认每段目标约 30 秒、竖屏 1080×1920。原视频主体居中裁切；可选模糊背景填充模式。
- 输出目录会创建 `clips/`、`subtitles/` 和 `manifest.json`。
- 此为本地 MVP；自动挑选高光只是基于语音密度的启发式排序，不代表语义判断。

## 隐私
视频、音频和字幕均在本机处理。Whisper 模型首次下载自模型托管服务；程序本身不上传媒体。

## 许可
本项目代码独立编写。依赖项及模型各自遵循其上游许可，请在分发前核对。
