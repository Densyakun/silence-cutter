# Silence Cut (無音カット共通コア)

動画・音声ファイルから無音区間を検出し、**カットプラン**（どの区間を残すか）を作る
Python 製の共通コアです。次の 2 通りの使われ方を想定し、どちらでも同じ解析結果を使えます。

1. **動画編集ソフトのプラグイン**として使う … プラグインはカットプランを受け取り、
   タイムラインにトリムを適用する（または EDL / JSON を読み込む）。
2. **編集前の事前処理**として使う … このリポジトリの GUI / CLI が FFmpeg で
   カット済みファイルを書き出す。

特定の動画編集ソフト（Premiere / Resolve / AviUtl / Final Cut など）の API には依存しません。
そのため「どのソフト向けか未確定」な段階でも、共通部分をそのまま納品・流用できます。

## 主な機能

- FFmpeg でローカル解析（音声を外部送信しない）。モノラル 16 kHz PCM をストリーム処理するため、
  長い素材でもメモリ使用量は一定。
- 音量しきい値（dBFS）による無音判定。`min_silence` / `min_speech` でチャタリングを抑制。
- ノイズフロアからしきい値を自動推定するモード（`--adaptive`）。
- 発話前後の余白（`pad_start` / `pad_end`）を保持。
- カット結果の書き出し（FFmpeg、再エンコード）。
- ソフト非依存の出力:
  - `JSON` … カットプラン（プラグイン連携用の共通フォーマット）
  - `CSV` … 区間一覧
  - `CMX3600 EDL` … 多くの編集ソフトが読み込める編集リスト
- Tkinter GUI（波形表示・カット区間の可視化・プレビュー）。
- CUI（`analyze` / `cut` / `probe`）。

## 動作環境

- Python 3.9 以上（標準ライブラリのみ。追加パッケージ不要）
- FFmpeg / ffprobe が PATH にあること
  - 見つからない場合は `--ffmpeg` / `--ffprobe` でパスを指定できます。
- GUI は Tkinter（Windows / macOS の標準 Python に同梱）

## セットアップ

```powershell
cd silence-cutter
python -m pip install -e .
```

インストールせずに使う場合:

```powershell
python -m silence_cut --help
python -m silence_cut.gui
```

## GUI の使い方

```powershell
silence-cut-gui
# または
python -m silence_cut gui
# Windows なら start_gui.bat をダブルクリックでも可
```

1. 「参照...」で動画・音声ファイルを選択
2. 必要ならパラメータを調整（初期値のままで概ね動作します）
3. 「解析」… 波形とカット区間（赤）／残す区間（緑）が表示されます
4. 「プレビュー」… 低画質で高速に書き出して再生
5. 「書き出し...」… カット済みファイルを保存
6. 「カットリスト保存...」… JSON / CSV / EDL を保存

## CLI の使い方

```powershell
# 素材の情報
silence-cut probe input.mp4

# 解析だけ（結果を JSON / CSV / EDL に保存）
silence-cut analyze input.mp4 --json cutplan.json --edl cutplan.edl --csv cutplan.csv

# カットして書き出し
silence-cut cut input.mp4 -o output.mp4 --crf 20 --preset medium

# しきい値を上げる（静かな環境の素材）
silence-cut cut input.mp4 -t -50 --min-silence 0.8

# ノイズフロアから自動推定
silence-cut cut input.mp4 --adaptive
```

### パラメータ

| オプション | 既定値 | 意味 |
| --- | --- | --- |
| `-t, --threshold` | `-45` | この値より静かな区間を無音とみなす（dBFS） |
| `-s, --min-silence` | `0.5` | これ以上続く無音だけをカット（秒） |
| `-m, --min-speech` | `0.15` | これより短い発話は無音に吸収（秒） |
| `--pad-start` | `0.05` | 発話の前に残す余白（秒） |
| `--pad-end` | `0.08` | 発話の後に残す余白（秒） |
| `--adaptive` | off | ノイズフロア + マージンでしきい値を自動決定 |
| `--frame-ms` | `20` | 解析フレーム長（ms）。小さくすると精度↑・速度↓ |

## 出力フォーマット

### カットプラン JSON（プラグイン連携用）

```json
{
  "version": 1,
  "source": { "path": "input.mp4", "duration": 12.5, "fps": 29.97,
              "has_video": true, "has_audio": true },
  "settings": { "threshold_db": -45.0, "min_silence": 0.5, "...": "..." },
  "threshold_db": -45.0,
  "summary": { "cut_segments": 3, "removed_ratio": 0.31, "...": "..." },
  "keep": [ { "start": 0.0, "end": 2.4, "duration": 2.4 } ],
  "cut":  [ { "start": 2.4, "end": 3.8, "duration": 1.4 } ],
  "timeline": [
    { "source_start": 0.0, "source_end": 2.4,
      "record_start": 0.0, "record_end": 2.4 }
  ]
}
```

`keep` をそのままタイムラインに並べればカット済みになります。
`timeline` は「元動画の時刻 → カット後タイムラインの時刻」の対応表です。

### EDL

`CMX3600` 形式。録画開始は `01:00:00:00`（慣例）。fps は素材から自動取得し、
取得できない場合は 30 として出力します（`--fps` で上書き可能）。

## プラグインとして組み込む場合

共通コアの境界は「カットプラン」です。プラグイン側は次のどちらかを実装すれば済みます。

- **JSON を読む**: `silence_cut.export.write_json()` の出力をプラグインの設定ファイルに保存し、
  `keep` の区間をタイムラインへ適用する。
- **ライブラリとして呼ぶ**: `silence_cut.analyze_file(path, settings)` を呼び、
  `result.plan.keep` / `result.plan.cut` を使う。GUI に依存しないため、
  Blender のアドオン、AviUtl のプラグイン用ヘルパー、DaVinci 用スクリプトなどからも利用可能。

音声解析を AI（VAD など）に差し替える場合は、`build_plan(..., activity=[bool, ...])` に
フレームごとの発話判定を渡します。`activity` のフレーム数は音声解析フレーム数と一致させてください。
前後の余白・最小無音・最小発話の処理はコア側がそのまま行います。

動作する最小例は `examples/plugin_adapter_demo.py` にあります。

```powershell
python examples\plugin_adapter_demo.py input.mp4
```

## 構成

```
silence_cut/
  settings.py  解析パラメータ
  media.py     FFmpeg 呼び出し・PCM ストリーム解析・素材情報
  detect.py    無音判定とカットプラン生成（純粋関数、FFmpeg 非依存）
  pipeline.py  解析のまとめ役
  export.py    JSON / CSV / EDL 出力
  render.py    FFmpeg によるカット書き出し・プレビュー
  cli.py       CUI
  gui.py       Tkinter GUI
tests/
```

`detect.py` はファイル I/O を持たないため、単体テストやプラグイン側での再利用が容易です。

## テスト

```powershell
python -m unittest discover -s tests -v
```

FFmpeg が無い環境では、FFmpeg を使うテストだけ自動でスキップされます。

## 制限事項 / 今後拡張しやすい点

- 書き出しは再エンコード方式です（フレーム精度優先）。高速化したい場合は
  `render.py` にキーフレーム単位のカット（ストリームコピー）を追加できます。
- 可変フレームレート（VFR）素材では EDL のタイムコードが近似になります。
  正確な編集が必要な場合は JSON の秒数を使うプラグイン連携を推奨します。
- 自動しきい値は補助機能です。重要な案件では代表サンプルで固定値を確認してください。
- BGM が小音量で流れ続ける素材では、音量ベースの判定は無音になりません。
  その場合は AI の音声区間検出（VAD）を `activity` 経由で差し込む想定です。
- 音声ストリームの開始位置が映像と大きくずれている素材では、カット位置がずれる
  可能性があります。事前に素材を確認してください。
