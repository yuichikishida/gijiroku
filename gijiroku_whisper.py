#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
議事録文字起こしスクリプト (M1 Mac用)

Google Drive for desktop の同期フォルダ「マイドライブ/議事録/録音」を監視し、
音声ファイルを mlx-whisper でローカル文字起こしする。

出力:
  議事録/文字起こし/〈元ファイル名〉_文字起こし.txt
  議事録/文字起こし/〈元ファイル名〉_議事録プロンプト.txt  ← Claudeチャットに貼る用
処理済みの音声は 録音/処理済み/ へ移動する。

使い方:
  python3 gijiroku_whisper.py           # 1回だけ処理して終了
  python3 gijiroku_whisper.py --watch   # 常駐監視(60秒間隔)

依存:
  brew install ffmpeg
  pip3 install mlx-whisper
"""

import argparse
import datetime
import glob
import os
import shutil
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


# ---- 文字起こし処理 -----------------------------------------------------

def is_stable(path: str, wait: float = 2.0) -> bool:
    """Driveの同期途中でないか、ファイルサイズが安定しているかで判定する。"""
    size1 = os.path.getsize(path)
    time.sleep(wait)
    return os.path.getsize(path) == size1


def transcribe_file(audio_path: str, out_dir: str, done_dir: str) -> None:
    import mlx_whisper  # 起動を速くするため遅延import

    name = os.path.splitext(os.path.basename(audio_path))[0]
    print(f"文字起こし開始: {os.path.basename(audio_path)}")
    start = time.time()

    result = mlx_whisper.transcribe(audio_path, path_or_hf_repo=MODEL)
    text = result["text"].strip()

    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(audio_path))
    date = mtime.strftime("%Y/%m/%d")

    os.makedirs(out_dir, exist_ok=True)
    txt_path = os.path.join(out_dir, f"{name}_文字起こし.txt")
    prompt_path = os.path.join(out_dir, f"{name}_議事録プロンプト.txt")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"会議名: {name}\n日付: {date}\n\n{text}\n")
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(build_prompt(name, date, text))

    os.makedirs(done_dir, exist_ok=True)
    shutil.move(audio_path, os.path.join(done_dir, os.path.basename(audio_path)))

    elapsed = time.time() - start
    print(f"完了 ({elapsed:.0f}秒): {txt_path}")
    print(f"       プロンプト → {prompt_path}")


def process_once(rec_dir: str, out_dir: str, done_dir: str) -> int:
    """録音フォルダ内の音声を全部処理する。処理した件数を返す。"""
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
            transcribe_file(path, out_dir, done_dir)
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
    out_dir = os.path.join(drive, "議事録", "文字起こし")
    done_dir = os.path.join(rec_dir, "処理済み")
    os.makedirs(rec_dir, exist_ok=True)

    print(f"監視フォルダ: {rec_dir}")

    if args.watch:
        print(f"常駐監視を開始します({WATCH_INTERVAL}秒間隔)。Ctrl+C で終了。")
        while True:
            process_once(rec_dir, out_dir, done_dir)
            time.sleep(WATCH_INTERVAL)
    else:
        n = process_once(rec_dir, out_dir, done_dir)
        print(f"処理完了: {n}件")


if __name__ == "__main__":
    main()
