#!/usr/bin/env python3
"""Download a course's lecture recordings (mp3) and slides, with matching names.

Lectures come from a TP (tp.educloud.no) schedule. Each lecture N gets
  mp3/lNN.mp3   (lNNa, lNNb, ... if it has several recordings)
  slides/sNN.pdf (sNNa, sNNb, ... if it has several slide files)
so l05 and s05 are always the same lecture. Numbering is by date over all
lectures in the schedule, so numbers stay stable as new material appears.
Existing files are skipped, so the script can be re-run during the semester.

Requirements: python3, yt-dlp, ffmpeg (brew install yt-dlp ffmpeg).

Panopto login (pick one)
  --cookies-from-browser chrome   read your logged-in browser session (easiest)
  --token-file FILE               file with your .ASPXAUTH cookie value
                                  (raw value or NAME=value)
  PANOPTO_TOKEN                   environment variable with the same value
The token is only written to a temporary cookie file that is deleted on exit.

Schedule
  --schedule-url   the TP schedule page URL for the course
  --schedule-json  schedule data saved from your browser (see below)
  --panopto-url    optional: the Panopto course folder URL; used to list
                   sessions the schedule does not link to

The TP schedule API needs a logged-in browser session, so a plain request is
usually refused. If so, the script prints a one-line snippet: run it in the
browser console on the schedule page and save the clipboard to a .json file,
then pass that file with --schedule-json.

Example
  ./course_fetch.py --schedule-json schedule.json --cookies-from-browser chrome \\
      --panopto-url 'https://<host>/Panopto/Pages/Sessions/List.aspx#folderID=...'
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from html.parser import HTMLParser

SLIDE_EXTS = (".pdf", ".pptx", ".ppt", ".key")
PANOPTO_VIEWER = "/Panopto/Pages/Viewer.aspx"


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href, "".join(self._text).strip()))
            self._href = None


def api_path(schedule_url):
    u = urllib.parse.urlparse(schedule_url)
    q = urllib.parse.parse_qs(u.query)
    prefix = u.path.split("/app/")[0]
    query = urllib.parse.urlencode(
        {
            "type": q.get("scheduleType", ["course"])[0],
            "id[]": q["course"][0],
            "sem": q["semester"][0],
        }
    )
    return f"{u.scheme}://{u.netloc}", f"{prefix}/ws/timeplan/?{query}"


def load_events(args):
    if args.schedule_json:
        with open(args.schedule_json, encoding="utf-8") as f:
            data = json.load(f)
        return data["events"], "https://tp.educloud.no"
    origin, path = api_path(args.schedule_url)
    try:
        with urllib.request.urlopen(origin + path) as r:
            data = json.load(r)
    except urllib.error.HTTPError:
        data = {"error": "refused"}
    if "events" not in data:
        sys.exit(
            "The schedule API requires a logged-in session.\n"
            "Open the schedule page in your browser, run this in the console:\n\n"
            f"  copy(await (await fetch('{path}')).text())\n\n"
            "then paste the clipboard into a file (e.g. schedule.json) and rerun with\n"
            "  --schedule-json schedule.json"
        )
    return data["events"], origin


def read_token(path):
    raw = open(path, encoding="utf-8").read().strip()
    return re.sub(r"^[\w.]+=", "", raw.splitlines()[0].strip())


def date_hint(text):
    for m in re.finditer(r"(\d{4})-(\d{2})-(\d{2})", text):
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3])).date()
        except ValueError:
            pass
    for m in re.finditer(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", text):
        y = int(m[3]) + (2000 if int(m[3]) < 100 else 0)
        try:
            return datetime(y, int(m[2]), int(m[1])).date()
        except ValueError:
            pass
    return None


def build_lectures(events, base, methods):
    lectures = []
    for e in sorted((e for e in events if e.get("teachingMethod") in methods), key=lambda e: e["dtstart"]):
        parser = LinkParser()
        parser.feed(e.get("curr") or "")
        slides, recs = [], {}
        for href, text in parser.links:
            url = urllib.parse.urljoin(base, href)
            path = urllib.parse.urlparse(url).path.lower()
            if path.endswith(SLIDE_EXTS) and url not in slides:
                slides.append(url)
            elif PANOPTO_VIEWER in url:
                recs.setdefault(url, text)
        for r in e.get("resources") or []:
            if r.get("type") == "recording" and PANOPTO_VIEWER in r["url"]:
                recs[r["url"]] = r.get("name") or ""
        lectures.append(
            {
                "date": datetime.fromisoformat(e["dtstart"][:10]).date(),
                "title": e["title"].strip(),
                "slides": slides,
                "recs": recs,
            }
        )
    for i, lec in enumerate(lectures, 1):
        lec["n"] = i
    return lectures


def resolve_duplicate_recordings(lectures):
    """A recording linked from several lectures is kept on one of them."""
    owners = {}
    for lec in lectures:
        for url in lec["recs"]:
            owners.setdefault(url, []).append(lec)
    for url, lecs in owners.items():
        if len(lecs) < 2:
            continue
        name = lecs[0]["recs"][url]
        hinted = date_hint(name)
        keep = next((l for l in lecs if l["date"] == hinted), None)
        if keep is None:
            keep = lecs[0]
            print(f"warning: recording '{name}' is linked from lectures "
                  f"{', '.join(str(l['n']) for l in lecs)}; keeping it on {keep['n']}. "
                  f"Use --extra to fix.")
        for l in lecs:
            if l is not keep:
                del l["recs"][url]


def labels(prefix, n, count):
    if count == 1:
        return [f"{prefix}{n:02d}"]
    return [f"{prefix}{n:02d}{chr(97 + i)}" for i in range(count)]


def need_tool(name):
    if not shutil.which(name):
        sys.exit(f"{name} not found. Install with: brew install {name}")


def download_slide(url, dest_base, out_dir):
    ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    dest = os.path.join(out_dir, "slides", dest_base + ext)
    if os.path.exists(dest):
        return "exists"
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        urllib.request.urlretrieve(urllib.parse.quote(url, safe=":/?&=%"), dest)
        return "ok"
    except (urllib.error.URLError, OSError) as err:
        return f"FAILED ({err})"


def write_cookie_file(token, host, directory):
    path = os.path.join(directory, "cookies.txt")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")
        f.write(f"#HttpOnly_{host}\tFALSE\t/\tTRUE\t2000000000\t.ASPXAUTH\t{token}\n")
    return path


def download_audio(url, label, out_dir, cookie_args):
    dest = os.path.join(out_dir, "mp3", label + ".mp3")
    if os.path.exists(dest):
        return "exists"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    cmd = ["yt-dlp", *cookie_args, "-x", "--audio-format", "mp3",
           "-o", os.path.join(out_dir, "mp3", label + ".%(ext)s"), url]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not os.path.exists(dest):
        last = (res.stderr.strip().splitlines() or ["unknown error"])[-1]
        return f"FAILED ({last})"
    return "ok"


def unlinked_sessions(panopto_url, cookie_args, used_urls):
    res = subprocess.run(
        ["yt-dlp", *cookie_args, "--flat-playlist", "--print", "%(url)s\t%(title)s", panopto_url],
        capture_output=True, text=True)
    used_ids = {urllib.parse.parse_qs(urllib.parse.urlparse(u).query).get("id", [""])[0] for u in used_urls}
    out = []
    for line in res.stdout.splitlines():
        url, _, title = line.partition("\t")
        sid = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("id", [""])[0]
        if PANOPTO_VIEWER in url and sid not in used_ids:
            out.append((title, url))
    return out


def write_index(lectures, assigned, out_dir):
    lines = ["# Lecture audio (mp3/lNN) and slides (slides/sNN)", "",
             "| # | Date | Topic | Audio | Slides |", "|---|------|-------|-------|--------|"]
    def on_disk(sub, prefix, n):
        folder = os.path.join(out_dir, sub)
        names = os.listdir(folder) if os.path.isdir(folder) else []
        return sorted(os.path.splitext(x)[0] for x in names if re.fullmatch(rf"{prefix}{n:02d}[a-z]?\..+", x))

    for lec in lectures:
        a = ", ".join(on_disk("mp3", "l", lec["n"])) or "none"
        s = ", ".join(on_disk("slides", "s", lec["n"])) or "none"
        lines.append(f"| {lec['n']} | {lec['date']:%d.%m} | {lec['title']} | {a} | {s} |")
    with open(os.path.join(out_dir, "INDEX.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--schedule-url")
    src.add_argument("--schedule-json")
    ap.add_argument("--token-file", help="file with the Panopto .ASPXAUTH value")
    ap.add_argument("--cookies-from-browser", metavar="BROWSER",
                    help="read the Panopto login from a browser (chrome, firefox, safari, ...)")
    ap.add_argument("--panopto-url", help="Panopto course folder URL (to report unlinked sessions)")
    ap.add_argument("--out", default=".", help="output directory (default: current)")
    ap.add_argument("--methods", default="FOR", help="TP teaching methods counted as lectures, comma separated (default FOR)")
    ap.add_argument("--extra", action="append", default=[], metavar="LABEL=URL",
                    help="also download a Panopto recording as LABEL, e.g. l01=https://...Viewer.aspx?id=...")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--no-slides", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, download nothing")
    args = ap.parse_args()

    events, base = load_events(args)
    lectures = build_lectures(events, base, set(args.methods.split(",")))
    resolve_duplicate_recordings(lectures)

    plan_audio, plan_slides = [], []
    assigned = {}
    for lec in lectures:
        recs = list(lec["recs"])
        a_labels = labels("l", lec["n"], len(recs))
        s_labels = labels("s", lec["n"], len(lec["slides"]))
        plan_audio += list(zip(a_labels, recs))
        plan_slides += list(zip(s_labels, lec["slides"]))
        assigned[lec["n"]] = {"audio": a_labels if recs else [], "slides": s_labels if lec["slides"] else []}
    for item in args.extra:
        label, _, url = item.partition("=")
        plan_audio.append((label, url))
        n = re.sub(r"\D", "", label)
        if n and int(n) in assigned:
            assigned[int(n)]["audio"].append(label)

    for lec in lectures:
        a, s = assigned[lec["n"]]["audio"], assigned[lec["n"]]["slides"]
        print(f"{lec['n']:>2} {lec['date']:%d.%m} {lec['title'][:55]:<55} audio: {','.join(a) or '-':<10} slides: {','.join(s) or '-'}")
    if args.dry_run:
        return

    os.makedirs(args.out, exist_ok=True)
    if not args.no_slides:
        print("\nSlides")
        for label, url in plan_slides:
            print(f"  {label}: {download_slide(url, label, args.out)}")

    if not args.no_audio:
        need_tool("yt-dlp")
        need_tool("ffmpeg")
        token = os.environ.get("PANOPTO_TOKEN")
        if args.token_file:
            token = read_token(args.token_file)
        if not token and not args.cookies_from_browser:
            sys.exit("No Panopto login: use --cookies-from-browser, --token-file or PANOPTO_TOKEN (or --no-audio)")
        with tempfile.TemporaryDirectory() as tmp:
            if args.cookies_from_browser and not token:
                cookie_args = ["--cookies-from-browser", args.cookies_from_browser]
            else:
                host = urllib.parse.urlparse(args.panopto_url or (plan_audio[0][1] if plan_audio else "")).netloc
                if not host:
                    sys.exit("No Panopto host found; pass --panopto-url")
                cookie_args = ["--cookies", write_cookie_file(token, host, tmp)]
            print("\nRecordings")
            for label, url in plan_audio:
                print(f"  {label}: {download_audio(url, label, args.out, cookie_args)}")
            if args.panopto_url:
                missing = unlinked_sessions(args.panopto_url, cookie_args, [u for _, u in plan_audio])
                if missing:
                    print("\nPanopto sessions not linked from the schedule (add with --extra lNN=URL):")
                    for title, url in missing:
                        print(f"  {title}\n    {url}")

    write_index(lectures, assigned, args.out)
    print("\nWrote INDEX.md")


if __name__ == "__main__":
    main()
