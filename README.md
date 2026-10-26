# course-fetch

Download a university course's lecture recordings and slides, name them so they pair up
(`mp3/l05.mp3` ↔ `slides/s05.pdf`), and transcribe the audio locally with Whisper.

Built for courses that publish a [TP](https://tp.educloud.no) schedule with links to
[Panopto](https://www.panopto.com) recordings and slide files (e.g. University of Oslo).

## 1. Download recordings and slides: `course_fetch.py`

Requirements: Python 3, `yt-dlp`, `ffmpeg` (`brew install yt-dlp ffmpeg`).

```bash
./course_fetch.py --schedule-json schedule.json --cookies-from-browser chrome \
    --panopto-url 'https://<host>/Panopto/Pages/Sessions/List.aspx#folderID=...'
```

**Schedule.** The TP API only answers a logged-in browser. Open the course schedule page,
run the one-line snippet the script prints (run it once with `--schedule-url` to get it) in
the browser console, and save the clipboard as `schedule.json`.

**Panopto login** (pick one):
- `--cookies-from-browser chrome` (also `firefox`, `safari`, ...). Stay logged in to Panopto there.
- `--token-file FILE` containing your `.ASPXAUTH` cookie value (browser dev tools → Application → Cookies).
- `PANOPTO_TOKEN` environment variable with the same value.

Useful flags: `--dry-run` (show the plan only), `--no-audio`, `--no-slides`, `--out DIR`,
`--extra l01=<panopto url>` (add a recording the schedule doesn't link).

Re-run it any time; existing files are skipped. It also writes `INDEX.md`, a table mapping
each lecture to its files. Sessions in the Panopto folder that the schedule doesn't link to
are listed so you can add them with `--extra`.

## 2. Transcribe: `transcribe.py`

Uses [mlx-whisper](https://github.com/ml-explore/mlx-examples) (Apple Silicon Macs only).
mlx-whisper needs `torch`, which may lack wheels for the newest Python, so use 3.12/3.13:

```bash
uv venv --python 3.12 ~/whisper-env && uv pip install --python ~/whisper-env/bin/python -r requirements.txt
~/whisper-env/bin/python transcribe.py
```

Writes `transcripts/lNN.txt` and `transcripts/lNN.srt` for every `mp3/lNN.mp3`. The language is
auto-detected; force one with `--language en` or `--lang l08=no`.

## Privacy

Your login token is never printed or saved; it is written to a temporary cookie file that is
deleted on exit. Downloaded recordings and slides are course material, so don't redistribute
them. `.gitignore` keeps them, transcripts and token files out of git.
