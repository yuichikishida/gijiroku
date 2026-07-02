# 議事録自動化プロジェクト (gijiroku)

## プロジェクト概要

会議の録音→文字起こし→議事録生成を無料で完結させるパイプライン。
まず社内(動物病院)で運用し、将来はコンサルティングのクライアント向けに提供する。

## 全体アーキテクチャ

```
[録音]                    [文字起こし]              [議事録生成]
スマホ or PC              M1 MacBook               Claude
ブラウザアプリ    ──→     mlx-whisper      ──→    プロンプト貼り付け
(index.html)             (gijiroku_whisper.py)    (現状は手動・無料)
        │                        ↑
        └── Googleドライブ ──────┘
            議事録/録音 → 議事録/文字起こし
```

## ファイル構成

- `index.html` — 録音Webアプリ(単一ファイル、依存なし)
  - 2モード: リアルタイム文字起こし(Web Speech API / PC Chrome向け)と録音のみ(MediaRecorder / スマホ向け)
  - スマホ判定で録音モードを初期選択
  - 録音モードは会議向けマイク設定(エコーキャンセル/ノイズ抑制OFF、自動ゲインON)
  - PC Chromeでは保存先フォルダを選択可能(File System Access API)。
    GoogleドライブのFile Provider経由アクセスに制限が出る場合があるため、失敗時はダウンロードにフォールバック。
    Macで録音→Drive「議事録/録音」に直接保存→Whisper自動処理、の全Mac完結フローが可能
  - Wake Lockで録音中の画面スリープ防止
  - 議事録プロンプト生成(クリップボードコピー → Claudeチャットに貼る運用)
  - 任意: Claude APIキー直接生成モード(`anthropic-dangerous-direct-browser-access`ヘッダ使用)
  - サーバー送信なし・ブラウザ内完結
- `gijiroku_whisper.py` — 文字起こしスクリプト(M1 Mac用)
  - `mlx-whisper` + モデル `mlx-community/whisper-large-v3-turbo`
  - Google Drive for desktopの同期フォルダ `マイドライブ/議事録/録音` を監視
  - 出力: `議事録/文字起こし/` に「_文字起こし.txt」と「_議事録プロンプト.txt」
  - 処理済み音声は `録音/処理済み/` へ移動
  - `--watch` で常駐監視(60秒間隔)
  - 依存: `brew install ffmpeg`, `pip3 install mlx-whisper`

## デプロイ

- ホスティング: GitHub Pages(このリポジトリ、mainブランチ、root)
- URL: https://yuichikishida.github.io/gijiroku/
- mainにプッシュすれば数分で自動反映される
- HTTPSはマイク使用(getUserMedia)の必須条件。localhost以外の平文HTTPでは動かない

## 設計上の決定事項と理由

- **無料運用が最優先**。文字起こしはWeb Speech API(無料)とローカルWhisper(無料)。議事録生成はClaude Proのチャットに貼る運用でAPI費用ゼロ
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
