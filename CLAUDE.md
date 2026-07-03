# 議事録自動化プロジェクト (gijiroku)

## プロジェクト概要

会議の録音→文字起こし→議事録生成を無料で完結させるパイプライン。
まず社内(動物病院)で運用し、将来はコンサルティングのクライアント向けに提供する。

## 全体アーキテクチャ

```
[録音]                    [文字起こし]              [議事録生成]
スマホ or PC              M1 MacBook               Gemini API(無料枠)
ブラウザアプリ    ──→     mlx-whisper      ──→    md + docx 自動生成
(index.html)             (gijiroku_whisper.py)    (またはClaudeチャット貼り付け)
        │                        ↑
        └── Googleドライブ ──────┘
            議事録/録音(受信箱)→ 議事録/日付_時刻_会議名/ に全データ集約
```

## データ構成(Googleドライブ)

```
議事録/
├── 録音/                          ← 受信箱。音声を入れると自動処理される
└── 2026-07-03_1430_週次ミーティング/  ← 会議ごとに自動作成
    ├── 〈音声ファイル〉             ← 処理後ここへ移動
    ├── 文字起こし.txt
    ├── 議事録プロンプト.txt
    ├── 議事録.md
    └── 議事録.docx                 ← Word/Googleドキュメントで開ける
```

ブラウザアプリの録音は「YYYY-MM-DD_HHMM_会議名_録音N.拡張子」で命名され、
Whisperスクリプトが同じ日時・会議名のフォルダに合流する(リアルタイムモードの
保存物「リアルタイム文字起こし.txt」「議事録(リアルタイム).md/.doc」も同フォルダ)。

## ファイル構成

- `index.html` — 録音Webアプリ(単一ファイル、依存なし)
  - 2モード: リアルタイム文字起こし(Web Speech API / PC Chrome向け)と録音のみ(MediaRecorder / スマホ向け)
  - リアルタイムモードは「音声も同時に録音」可能(既定ON)。認識が乱れても後からWhisperで完全版を作れる。
    認識が途切れた際は未確定(interim)テキストを破棄せず確定として残す救済処理あり
  - 2モードともPC/スマホの両方で利用可能(スマホは録音のみを初期選択)。
    リアルタイムはWeb Speech API対応ブラウザのみ(PC Chrome/Android Chrome。iPhoneはSafariで動く場合あり)
  - スマホからはGoogleドライブアプリで「議事録/録音」へアップロードする運用
  - 録音モードは会議向けマイク設定(エコーキャンセル/ノイズ抑制OFF、自動ゲインON)
  - 録音モードは使用マイクを画面内で選択可能(選択はlocalStorageに保存)。
    macOSの連係機能でiPhoneのマイクが既定になってしまう問題への対策。
    リアルタイムモードはWeb Speech APIの仕様上マイク選択不可(システム既定を使用)
  - PC Chromeでは保存先にGoogleドライブの「議事録」フォルダを一度選ぶと(File System Access API)、
    録音は「録音」サブフォルダ、文字起こしテキスト・生成議事録は「文字起こし」サブフォルダに直接保存。
    フォルダハンドルはIndexedDBに記憶され再選択不要。失敗時・未選択時はダウンロードにフォールバック。
    Macで録音→Drive直接保存→Whisper自動処理、の全Mac完結フローが可能
  - Wake Lockで録音中の画面スリープ防止
  - 議事録のその場生成: Gemini API(gemini-2.5-flash、無料枠あり)。キーはlocalStorageに保存
  - 議事録プロンプト生成も残置(クリップボードコピー → Claudeチャットに貼る運用)
  - 任意: Claude APIキー直接生成モード(`anthropic-dangerous-direct-browser-access`ヘッダ使用)
  - サーバー送信なし・ブラウザ内完結
- `gijiroku_whisper.py` — 文字起こしスクリプト(M1 Mac用)
  - `mlx-whisper` + モデル `mlx-community/whisper-large-v3-turbo`
  - Google Drive for desktopの同期フォルダ `マイドライブ/議事録/録音`(受信箱)を監視
  - 会議フォルダ `議事録/日付_時刻_会議名/` を自動作成し、文字起こし・プロンプト・議事録・音声を集約
  - `--watch` で常駐監視(60秒間隔)
  - Gemini APIキー設定時(env `GEMINI_API_KEY` または `gemini_api_key.txt`・gitignore済み)は
    議事録.mdと議事録.docx(macOS標準textutilで変換)まで自動生成。未設定なら文字起こしのみ
  - 依存: `brew install ffmpeg`, `pip3 install mlx-whisper`

## デプロイ

- ホスティング: GitHub Pages(このリポジトリ、mainブランチ、root)
- URL: https://yuichikishida.github.io/gijiroku/
- mainにプッシュすれば数分で自動反映される
- HTTPSはマイク使用(getUserMedia)の必須条件。localhost以外の平文HTTPでは動かない

## 設計上の決定事項と理由

- **無料運用が最優先**。文字起こしはWeb Speech API(無料)とローカルWhisper(無料)。議事録生成はGemini APIの無料枠(または従来のClaudeチャット貼り付け)でAPI費用ゼロ
- Google Cloud STTは不採用(有料: 無料枠60分/月、以降約$0.016/分)。将来精度が必要になれば再検討
- スマホのブラウザ録音は画面ロック/アプリ切替で止まる制約あり(特にiOS)。会議中にスマホで他作業をしたい場合は標準ボイスメモで録音し、音声だけDriveに上げる運用も許容する
- 音声データを外部サービスに出さない構成(ローカルWhisper)は、クライアント提供時のセキュリティ説明上の強みとして意図的に選択

## 今後のロードマップ

1. エンドツーエンドの実地テスト(スマホ録音→Drive→Whisper→Claude)← 未完了
2. 精度・速度チューニング(モデル変更、initial_promptへの専門用語注入など)
3. 話者分離(pyannote等)の検討
4. クライアント提供版: Privateリポジトリに分離、自前インフラでのホスティング、
   議事録生成のAPI自動化(Claude API)を有料オプションとして追加
5. 既存のエンタープライズ向けエージェント基盤(マルチ部門構成)への組み込み

## 規約

- UIテキスト・コメントは日本語
- index.htmlは単一ファイル構成を維持(ビルド工程を入れない)
- クライアント固有の設定・情報はこのPublicリポジトリに入れない
