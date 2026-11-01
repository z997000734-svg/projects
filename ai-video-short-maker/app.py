from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_NAME = "AI 视频短片助手"


@dataclass
class Segment:
    start: float
    end: float
    text: str


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")


def media_duration(path: Path) -> float:
    result = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                  "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "ffprobe 无法读取视频")
    return float(result.stdout.strip())


def escape_subtitle_path(path: Path) -> str:
    # FFmpeg subtitles filter uses its own quoting rules.
    value = str(path.resolve()).replace("\\", "/")
    return value.replace(":", r"\\:").replace("'", r"\\'")


def recognize(path: Path, model_name: str, language: str, device: str,
              log) -> list[Segment]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("缺少 faster-whisper，请先运行 pip install -r requirements.txt") from exc
    # PyAV 19 removed the metadata_errors keyword that some faster-whisper
    # releases still pass to av.open(). Keep compatibility with both APIs.
    import av
    original_av_open = av.open

    def compatible_av_open(*args, **kwargs):
        try:
            return original_av_open(*args, **kwargs)
        except TypeError as exc:
            if "metadata_errors" not in kwargs or "metadata_errors" not in str(exc):
                raise
            kwargs.pop("metadata_errors", None)
            return original_av_open(*args, **kwargs)

    av.open = compatible_av_open
    log(f"正在加载 Whisper {model_name} 模型（首次运行可能需要下载）…")
    compute = "float16" if device == "cuda" else "int8"
    model = WhisperModel(model_name, device=device, compute_type=compute)
    lang = None if language == "自动检测" else {"中文": "zh", "英文": "en"}.get(language, language)
    try:
        items, info = model.transcribe(str(path), language=lang, vad_filter=True,
                                       word_timestamps=False, beam_size=5)
        log(f"识别语言：{info.language}，开始整理字幕…")
        output = []
        for item in items:
            text = item.text.strip()
            if text and item.end > item.start:
                output.append(Segment(float(item.start), float(item.end), text))
        return output
    finally:
        av.open = original_av_open


def make_chunks(segments: list[Segment], duration: float, target: int) -> list[tuple[float, float, str]]:
    if not segments:
        return [(start, min(start + target, duration), "") for start in range(0, int(duration), target)]
    chunks = []
    begin = 0
    while begin < len(segments):
        end_idx = begin
        start_time = segments[begin].start
        limit = start_time + target
        while end_idx + 1 < len(segments) and segments[end_idx + 1].end <= limit:
            end_idx += 1
        # If one utterance is long, include it to avoid dropping spoken content.
        end_time = min(duration, max(segments[end_idx].end, start_time + 1))
        text = " ".join(s.text for s in segments[begin:end_idx + 1])
        chunks.append((max(0, start_time), end_time, text))
        begin = end_idx + 1
    return chunks


def write_srt(path: Path, segments: list[Segment], offset: float, end: float) -> None:
    def stamp(sec: float) -> str:
        ms = max(0, round(sec * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"
    lines = []
    index = 1
    for item in segments:
        left, right = max(item.start, offset), min(item.end, end)
        if right <= left:
            continue
        lines.extend([str(index), f"{stamp(left-offset)} --> {stamp(right-offset)}", item.text, ""])
        index += 1
    path.write_text("\n".join(lines), encoding="utf-8-sig")


def render_clip(source: Path, out: Path, srt: Path | None,
                start: float, end: float, fill: bool, log) -> None:
    duration = max(0.2, end - start)
    if fill:
        vf = ("split=2[bg][fg];[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
              "crop=1080:1920,boxblur=25:10[blur];[fg]scale=1080:1920:"
              "force_original_aspect_ratio=decrease[front];[blur][front]overlay=(W-w)/2:(H-h)/2")
    else:
        vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
    if srt:
        vf += f",subtitles='{escape_subtitle_path(srt)}':force_style='FontName=Microsoft YaHei,FontSize=20,Outline=2,Shadow=1,MarginV=100'"
    args = ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
            "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
    result = run(args)
    if result.returncode:
        raise RuntimeError(result.stderr[-2500:] or "FFmpeg 导出失败")
    log(f"已导出：{out.name}")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(APP_NAME)
        root.geometry("760x650")
        root.minsize(680, 560)
        self.messages: queue.Queue = queue.Queue()
        self.input = tk.StringVar()
        self.output = tk.StringVar(value=str(Path.home() / "Videos" / "AI短片输出"))
        self.model = tk.StringVar(value="tiny")
        self.language = tk.StringVar(value="自动检测")
        self.device = tk.StringVar(value="cpu")
        self.target = tk.IntVar(value=30)
        self.fill = tk.BooleanVar(value=True)
        self.subtitles = tk.BooleanVar(value=True)
        self._build()
        self.root.after(150, self._drain)

    def _build(self):
        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="AI 视频短片助手", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        ttk.Label(frame, text="本地识别语音、切分短片并添加字幕，不会上传你的视频。").pack(anchor="w", pady=(2, 16))
        self._path_row(frame, "输入视频", self.input, self._pick_video)
        self._path_row(frame, "输出目录", self.output, self._pick_folder)
        opts = ttk.LabelFrame(frame, text="处理设置", padding=12)
        opts.pack(fill="x", pady=10)
        ttk.Label(opts, text="目标片段秒数").grid(row=0, column=0, sticky="w", padx=4, pady=6)
        ttk.Spinbox(opts, from_=10, to=180, increment=5, textvariable=self.target, width=8).grid(row=0,column=1,sticky="w")
        ttk.Label(opts, text="语音模型").grid(row=0,column=2,sticky="w",padx=(18,4))
        ttk.Combobox(opts, textvariable=self.model, values=("tiny","base","small","medium"), state="readonly", width=10).grid(row=0,column=3)
        ttk.Label(opts, text="语言").grid(row=1,column=0,sticky="w",padx=4,pady=6)
        ttk.Combobox(opts,textvariable=self.language,values=("自动检测","中文","英文"),state="readonly",width=12).grid(row=1,column=1,sticky="w")
        ttk.Label(opts,text="设备").grid(row=1,column=2,sticky="w",padx=(18,4))
        ttk.Combobox(opts,textvariable=self.device,values=("cpu","cuda"),state="readonly",width=10).grid(row=1,column=3)
        ttk.Checkbutton(opts,text="生成并烧录字幕",variable=self.subtitles).grid(row=2,column=0,columnspan=2,sticky="w",pady=5)
        ttk.Checkbutton(opts,text="竖屏模糊背景填充（关闭则居中裁切）",variable=self.fill).grid(row=2,column=2,columnspan=3,sticky="w")
        self.button=ttk.Button(frame,text="开始生成短片",command=self._start)
        self.button.pack(anchor="w",pady=5)
        self.progress=ttk.Progressbar(frame,mode="indeterminate")
        self.progress.pack(fill="x",pady=5)
        ttk.Label(frame,text="运行日志").pack(anchor="w",pady=(8,2))
        self.logbox=tk.Text(frame,height=15,wrap="word",state="disabled",font=("Consolas",9))
        self.logbox.pack(fill="both",expand=True)
        self._log("请选择一个视频开始。建议先用 1–3 分钟的小视频试跑。")

    def _path_row(self,parent,label,var,command):
        row=ttk.Frame(parent); row.pack(fill="x",pady=4)
        ttk.Label(row,text=label,width=10).pack(side="left")
        ttk.Entry(row,textvariable=var).pack(side="left",fill="x",expand=True,padx=5)
        ttk.Button(row,text="浏览…",command=command).pack(side="right")

    def _pick_video(self):
        p=filedialog.askopenfilename(filetypes=[("视频","*.mp4 *.mov *.mkv *.avi *.webm"),("所有文件","*.*")])
        if p:self.input.set(p)

    def _pick_folder(self):
        p=filedialog.askdirectory()
        if p:self.output.set(p)

    def _log(self,text):
        self.messages.put(text)

    def _drain(self):
        try:
            while True:
                text=self.messages.get_nowait()
                self.logbox.configure(state="normal"); self.logbox.insert("end",text+"\n")
                self.logbox.see("end"); self.logbox.configure(state="disabled")
        except queue.Empty: pass
        self.root.after(150,self._drain)

    def _start(self):
        src=Path(self.input.get())
        if not src.is_file():
            messagebox.showerror(APP_NAME,"请先选择有效的视频文件。"); return
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            messagebox.showerror(APP_NAME,"找不到 FFmpeg。请安装 FFmpeg 并加入 PATH，然后重启程序。"); return
        if self.subtitles.get() and not self.language.get():
            messagebox.showerror(APP_NAME,"请选择字幕识别语言。"); return
        out=Path(self.output.get())
        if src.resolve()==out.resolve():
            messagebox.showerror(APP_NAME,"输出目录不能是视频文件本身。"); return
        self.button.configure(state="disabled"); self.progress.start(12)
        settings=(src,out,self.model.get(),self.language.get(),self.device.get(),
                  int(self.target.get()),self.fill.get(),self.subtitles.get())
        threading.Thread(target=self._work,args=settings,daemon=True).start()

    def _work(self,src,out,model,language,device,target,fill,subtitles):
        try:
            out.mkdir(parents=True,exist_ok=True)
            clips_dir=out/"clips"; subs_dir=out/"subtitles"
            clips_dir.mkdir(exist_ok=True); subs_dir.mkdir(exist_ok=True)
            duration=media_duration(src)
            self._log(f"视频时长：{duration:.1f} 秒")
            segs=recognize(src,model,language,device,self._log) if subtitles else []
            chunks=make_chunks(segs,duration,target)
            if not chunks: raise RuntimeError("没有可导出的视频片段。")
            self._log(f"准备导出 {len(chunks)} 段…")
            manifest=[]
            for i,(start,end,text) in enumerate(chunks,1):
                srt=None
                if subtitles:
                    srt=subs_dir/f"clip_{i:03}.srt"
                    write_srt(srt,segs,start,end)
                dest=clips_dir/f"clip_{i:03}.mp4"
                render_clip(src,dest,srt,start,end,fill,self._log)
                manifest.append({"file":str(dest.relative_to(out)),"start":start,"end":end,"transcript":text})
            (out/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
            self._log(f"完成！输出目录：{out}")
            self.messages.put(("done",None))
        except Exception as exc:
            self._log(f"处理失败：{exc}")
            self.messages.put(("done",str(exc)))

    def _drain(self):
        try:
            while True:
                item=self.messages.get_nowait()
                if isinstance(item,tuple) and item[0]=="done":
                    self.progress.stop(); self.button.configure(state="normal")
                    if item[1]: self.root.after(0,lambda e=item[1]:messagebox.showerror(APP_NAME,e))
                    else: self.root.after(0,lambda:messagebox.showinfo(APP_NAME,"短片已生成。"))
                else:
                    self.logbox.configure(state="normal"); self.logbox.insert("end",str(item)+"\n")
                    self.logbox.see("end"); self.logbox.configure(state="disabled")
        except queue.Empty: pass
        self.root.after(150,self._drain)


if __name__ == "__main__":
    root=tk.Tk()
    App(root)
    root.mainloop()

