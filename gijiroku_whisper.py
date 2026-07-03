#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
議事録文字起こしスクリプト (M1 Mac用)

Google Drive for desktop の同期フォルダ「マイドライブ/議事録/録音」を監視し、
音声ファイルを mlx-whisper でローカル文字起こしする。

出力(会議ごとに「議事録/日付_時刻_会議名/」フォルダを自動作成して集約):
  文字起こし.txt
  議事録プロンプト.txt          ← Claudeチャットに貼る用
  議事録.md / 議事録.docx       ← Gemini APIで自動生成(キー設定時のみ)
  音声ファイル                  ← 処理後、同フォルダへ移動

音声ファイル名が「YYYY-MM-DD_HHMM_会議名_録音.m4a」形式(ブラウザアプリが付与)なら
その日時・会議名でフォルダを作り、リアルタイムモードの保存先と同じフォルダに合流する。
それ以外はファイルの更新日時とファイル名からフォルダ名を決める。

使い方:
  python3 gijiroku_whisper.py           # 1回だけ処理して終了
  python3 gijiroku_whisper.py --watch   # 常駐監視(60秒間隔)

議事録の自動生成(任意):
  Gemini APIキー(https://aistudio.google.com/apikey で無料発行)を
  環境変数 GEMINI_API_KEY か、このスクリプトと同じフォルダの
  gemini_api_key.txt(gitignore済み)に設定すると、文字起こし後に
  議事録まで自動生成される。未設定なら従来どおり文字起こしのみ。

依存:
  brew install ffmpeg
  pip3 install mlx-whisper
"""

import argparse
import datetime
import glob
import os
import re
import shutil
import subprocess
import sys
import time

# ---- 設定 -------------------------------------------------------------

MODEL = "mlx-community/whisper-large-v3-turbo"

AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".webm", ".mp4"}
WATCH_INTERVAL = 60  # 秒


def find_drive_root() -> str:
    """Google Drive for desktop のマイドライブを自動検出する。

    環境変数 GIJIROKU_DRIVE で明示指定も可能。
    複数アカウントがある場合は「議事録」フォルダを持つドライブを優先する。
    """
    override = os.environ.get("GIJIROKU_DRIVE")
    if override:
        if os.path.isdir(override):
            return override
        sys.exit(f"エラー: GIJIROKU_DRIVE のパスが存在しません: {override}")

    candidates = sorted(
        glob.glob(os.path.expanduser("~/Library/CloudStorage/GoogleDrive-*/マイドライブ"))
    )
    for path in candidates:
        if os.path.isdir(os.path.join(path, "議事録")):
            return path
    if candidates:
        return candidates[0]
    sys.exit(
        "エラー: Google Drive の同期フォルダが見つかりません。"
        "Google Drive for desktop が起動しているか確認してください。"
    )


# ---- 議事録プロンプト(index.html の buildPrompt と同じ形式) ----------

def build_prompt(title: str, date: str, text: str) -> str:
    return f"""以下は会議の文字起こしです。これをもとに議事録を作成してください。

# 会議情報
- 会議名: {title}
- 日付: {date}

# 議事録の形式
1. 会議の概要(3行以内)
2. 主な議題と議論の内容
3. 決定事項(箇条書き)
4. TODO・アクションアイテム(担当者と期限が読み取れれば併記)
5. 次回への持ち越し・未解決事項

音声認識の誤変換と思われる箇所は文脈から補正してください。

# 文字起こし全文
{text}"""


# ---- 議事録の自動生成(Gemini API・無料枠あり) --------------------------

GEMINI_MODEL = "gemini-2.5-flash"


def load_gemini_key():
    """環境変数 GEMINI_API_KEY またはスクリプト隣の gemini_api_key.txt からキーを読む。"""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    key_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gemini_api_key.txt")
    if os.path.isfile(key_file):
        with open(key_file, encoding="utf-8") as f:
            return f.read().strip() or None
    return None


def generate_minutes(prompt: str, api_key: str) -> str:
    import json
    import ssl
    import urllib.request

    # python.org版Pythonは証明書が未設定のことがあるため、certifiがあればそれを使う
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    req = urllib.request.Request(
        url,
        data=json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    with urllib.request.urlopen(req, timeout=300, context=ctx) as res:
        data = json.load(res)
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


# ---- Word(.docx)変換(macOS標準のtextutilを使用) ----------------------

def md_to_html(md: str) -> str:
    """議事録Markdownを簡易HTMLに変換する(textutilでdocx化するための中間形式)。"""
    import html as html_mod

    out = []
    in_list = False

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in md.splitlines():
        line = raw.strip()
        esc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html_mod.escape(line))
        if not line:
            close_list()
        elif line.startswith("### "):
            close_list(); out.append(f"<h3>{esc[4:]}</h3>")
        elif line.startswith("## "):
            close_list(); out.append(f"<h2>{esc[3:]}</h2>")
        elif line.startswith("# "):
            close_list(); out.append(f"<h1>{esc[2:]}</h1>")
        elif line in ("---", "***"):
            close_list(); out.append("<hr>")
        elif line.startswith(("* ", "- ")):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{esc[2:]}</li>")
        else:
            close_list(); out.append(f"<p>{esc}</p>")
    close_list()
    return (
        '<html><head><meta charset="utf-8"></head>'
        "<body style=\"font-family: -apple-system, 'Hiragino Sans', sans-serif;\">"
        + "\n".join(out)
        + "</body></html>"
    )


def write_docx(md_text: str, out_path: str) -> bool:
    """MarkdownをWord(.docx)に変換して保存する。成功でTrue。"""
    if shutil.which("textutil") is None:
        return False
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(md_to_html(md_text))
        tmp = f.name
    try:
        subprocess.run(
            ["textutil", "-convert", "docx", tmp, "-output", out_path],
            check=True, capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False
    finally:
        os.unlink(tmp)


# ---- 文字起こし処理 -----------------------------------------------------

def session_dir_for(audio_path: str, drive: str) -> str:
    """会議フォルダ「議事録/日付_時刻_会議名」のパスを決める。

    ブラウザアプリが付けた「YYYY-MM-DD_HHMM_会議名_録音N」形式のファイル名なら
    その日時・会議名を使い、リアルタイムモードの保存先フォルダに合流する。
    """
    stem = os.path.splitext(os.path.basename(audio_path))[0]
    m = re.match(r"^(\d{4}-\d{2}-\d{2}_\d{4})_(.+?)(?:_録音\d*)?$", stem)
    if m:
        folder = f"{m.group(1)}_{m.group(2)}"
    else:
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(audio_path))
        folder = f"{mtime:%Y-%m-%d_%H%M}_{stem}"
    return os.path.join(drive, "議事録", folder)

def is_stable(path: str, wait: float = 2.0) -> bool:
    """Driveの同期途中でないか、ファイルサイズが安定しているかで判定する。"""
    size1 = os.path.getsize(path)
    time.sleep(wait)
    return os.path.getsize(path) == size1


def transcribe_file(audio_path: str, drive: str) -> None:
    import mlx_whisper  # 起動を速くするため遅延import

    session_dir = session_dir_for(audio_path, drive)
    # フォルダ名「日付_時刻_会議名」から会議名部分を取り出す
    title = os.path.basename(session_dir).split("_", 2)[-1]
    print(f"文字起こし開始: {os.path.basename(audio_path)}")
    start = time.time()

    result = mlx_whisper.transcribe(audio_path, path_or_hf_repo=MODEL)
    text = result["text"].strip()

    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(audio_path))
    date = mtime.strftime("%Y/%m/%d")

    os.makedirs(session_dir, exist_ok=True)
    txt_path = os.path.join(session_dir, "文字起こし.txt")
    prompt_path = os.path.join(session_dir, "議事録プロンプト.txt")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"会議名: {title}\n日付: {date}\n\n{text}\n")
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(build_prompt(title, date, text))

    # 音声も会議フォルダへ移動(全データを1フォルダに集約)
    shutil.move(audio_path, os.path.join(session_dir, os.path.basename(audio_path)))

    elapsed = time.time() - start
    print(f"完了 ({elapsed:.0f}秒): {session_dir}/")

    # 議事録の自動生成(Geminiキーがある場合のみ。失敗しても文字起こしは残る)
    api_key = load_gemini_key()
    if not api_key:
        print("       (Gemini APIキー未設定のため議事録の自動生成はスキップ)")
        return
    try:
        print("議事録を生成中(Gemini)…")
        minutes = generate_minutes(build_prompt(title, date, text), api_key)
        md_path = os.path.join(session_dir, "議事録.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(minutes)
        docx_path = os.path.join(session_dir, "議事録.docx")
        if write_docx(minutes, docx_path):
            print("       議事録 → 議事録.md / 議事録.docx")
        else:
            print("       議事録 → 議事録.md(docx変換はスキップ)")
    except Exception as e:
        print(f"議事録生成に失敗(文字起こしは保存済み): {e}", file=sys.stderr)


def process_once(rec_dir: str, drive: str) -> int:
    """録音フォルダ(受信箱)内の音声を全部処理する。処理した件数を返す。"""
    count = 0
    for entry in sorted(os.listdir(rec_dir)):
        path = os.path.join(rec_dir, entry)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(entry)[1].lower() not in AUDIO_EXTS:
            continue
        if entry.startswith("."):
            continue
        if not is_stable(path):
            print(f"同期中のためスキップ: {entry}")
            continue
        try:
            transcribe_file(path, drive)
            count += 1
        except Exception as e:  # 1件失敗しても他のファイルは処理を続ける
            print(f"エラー: {entry}: {e}", file=sys.stderr)
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="議事録の文字起こし(ローカルWhisper)")
    parser.add_argument("--watch", action="store_true", help="常駐監視モード(60秒間隔)")
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None:
        sys.exit("エラー: ffmpeg が見つかりません。`brew install ffmpeg` を実行してください。")

    drive = find_drive_root()
    rec_dir = os.path.join(drive, "議事録", "録音")
    os.makedirs(rec_dir, exist_ok=True)

    print(f"監視フォルダ: {rec_dir}")

    if args.watch:
        print(f"常駐監視を開始します({WATCH_INTERVAL}秒間隔)。Ctrl+C で終了。")
        while True:
            process_once(rec_dir, drive)
            time.sleep(WATCH_INTERVAL)
    else:
        n = process_once(rec_dir, drive)
        print(f"処理完了: {n}件")


if __name__ == "__main__":
    main()
