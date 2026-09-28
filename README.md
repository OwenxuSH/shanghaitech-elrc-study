# ShanghaiTech ELRC Study

本地视频优先的课堂学习界面：左侧是带时间戳的课件目录，中间播放屏幕录播，右侧可分别展开逐页讲解和整节课转写。视频与转写可以双向跳转，也支持调速和可选的逐页暂停。

当前预生成笔记涵盖 2026 秋季 CS181、CS182、CS277、CS280 各四节课堂。页面索引是从录屏筛选的课堂画面，并不保证覆盖原始 PPT 的每一页。笔记和讲解由录屏与语音识别辅助整理，应以课堂原视频和课件为准。

## 资料边界

此公开仓库**不提供** ELRC 录播、原始转写、课件截图、Cookie 或可直接播放的媒体文件，也没有视频 Release。每位使用者须有自己的上海科技大学 ELRC 访问权限，并在本地获取其有权访问的材料。克隆仓库后，未下载媒体时课程目录为空；GitHub Pages 无法运行这里的 Python 服务。

## 本地运行

需要 Python 3.9+、`ffmpeg`/`ffprobe`、Google Chrome 和至少 20 GB 的空闲磁盘空间（分段录播与合并文件会并存）。以下命令在仓库根目录执行。

```bash
python3 -m pip install -r requirements.txt
```

在 macOS 上启动一个**可见的**、单独的 Chrome 调试资料夹，并在该窗口登录自己的 ELRC 账号，打开任意 `https://elrc.shanghaitech.edu.cn/learn/` 课程页面：

```bash
open -na "Google Chrome" --args --remote-debugging-port=9224 --remote-allow-origins=http://localhost --user-data-dir="$PWD/.chrome-elrc"
```

随后运行：

```bash
python3 download_courses.py CS181 CS182 CS277 CS280
python3 merge_class_sessions.py CS181 CS182 CS277 CS280
python3 prepare_study.py CS181 CS182 CS277 CS280
python3 study_service.py
```

打开 <http://127.0.0.1:8790/>。若端口占用，运行 `python3 study_service.py --port 8791` 并打开对应地址。下载脚本只使用当前 Chrome 中的登录会话，不要求在命令行输入或保存账号密码；下载中断后可重跑。录播和转写只保存在本机，关闭调试 Chrome 后该会话不再供脚本读取。

当前笔记的截图链接在执行 `prepare_study.py` 后才会出现。需要 PDF 时，可自行安装 PyMuPDF 并从本地截图导出；公开仓库不含教师原始 PPT 或其截图文件。
