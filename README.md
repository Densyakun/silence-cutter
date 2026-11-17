# Silence Cut (無声・無音カット共通コア)

動画・音声ファイルから不要区間を検出し、**カットプラン**（どの区間を残すか）を作る
Python 製の共通コアです。既定は**無声カット**で、通常はこちらを使用します。
必要に応じて無音カットも選べます。

- **無声カット（既定）**: 発話区間だけを残し、それ以外（無音・サー音・環境音など）を除去
- **無音カット**: 静かな区間（音量がしきい値未満）を除去（通常は無声カットを使用）

次の 2 通りの使われ方を想定し、どちらでも同じ解析結果を使えます。

1. **動画編集ソフトのプラグイン**として使う … プラグインはカットプランを受け取り、
   タイムラインにトリムを適用する（または EDL / XML / JSON を読み込む）。
2. **編集前の事前処理**として使う … このリポジトリの GUI / CLI が FFmpeg で
   カット済みファイルを書き出す、または編集ソフト用の XML / EDL を書き出す。

特定の動画編集ソフト（Premiere / Resolve / AviUtl / Final Cut など）の API には依存しません。
そのため「どのソフト向けか未確定」な段階でも、共通部分をそのまま納品・流用できます。

## 主な機能

- FFmpeg でローカル解析（音声を外部送信しない）。PCM をストリーム処理するため、
  長い素材でもメモリ使用量は一定。
- **無声カット（既定・発話区間検出）**: ノイズフロアからの SNR + ヒステリシス +
  ゼロ交差率 / 低域エネルギー比による定常ノイズ除去 + 発話後ホールド。
- **無音カット（オプション）**: 音量しきい値（dBFS）。`--adaptive` でノイズフロアから自動推定。
- `min_silence` / `min_speech` でチャタリングを抑制、`pad_start` / `pad_end` で余白を保持。
- カット結果の書き出し（FFmpeg、再エンコード）。
- 出力フォーマット:
  - `JSON` … カットプラン（プラグイン連携用の共通フォーマット）
  - `CSV` … 区間一覧
  - `CMX3600 EDL` … 多くの編集ソフトが読み込める編集リスト
  - `EXO` … AviUtl 拡張編集のオブジェクトファイル（動画＋音声を自動配置）
  - `XMEML (FCP7 XML)` … Premiere Pro / DaVinci Resolve 用（素材パス入り）
  - `FCPXML` … Final Cut Pro / DaVinci Resolve 用（素材パス入り）
- Tkinter GUI（波形表示・カット区間の可視化・ストリーミングプレビュー、モード切替）。
  入力ファイルは参照ボタンに加えて、ウィンドウへのドラッグ＆ドロップでも指定できます
  （`tkinterdnd2` を使用）。
- CUI（`analyze` / `cut` / `probe`）。

## 動作環境

- Python 3.9 以上
- FFmpeg / ffprobe が PATH にあること
  - 見つからない場合は `--ffmpeg` / `--ffprobe` でパスを指定できます。
  - GUI のプレビューには `ffplay` を使用します（ffmpeg と同じフォルダにあれば自動検出）。
- GUI のドラッグ＆ドロップには `tkinterdnd2` を使用します（`pip install -e .` で導入）。
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

1. 「参照...」で動画・音声ファイルを選択（ウィンドウへドラッグ＆ドロップでも可）
2. 検出モードを選択（既定は `無声カット`。`無音カット` は通常使いません）
3. 必要ならパラメータを調整（初期値のままで概ね動作します）
   - 「無声カット専用」… SNR（開始・終了）、発話ホールド、ノイズフロア推定
   - 「無音カット専用」… しきい値、自動推定、自動マージン
   - 「共通設定」… 最小無音/非発話、最小発話、余白、解析フレーム
   - 現在のモードで使わない設定は自動的にグレーアウトされます
4. 「解析」… 波形とカット区間（赤）／残す区間（緑）が表示されます
5. 「プレビュー」… 全体の書き出しを待たずに再生を開始します。プレビュー画面の
   「再生 / 一時停止」「停止」とシークバーで操作できます。映像は同じウィンドウ内に
   表示され、一時停止中は最後のフレームが残ります。シークするとその位置から
   FFmpeg が処理し直すため、長い素材でも任意の位置をすぐ確認できます
6. 「書き出し...」… カット済みファイルを保存
7. 「カットリスト保存...」… JSON / CSV / EDL / EXO / XML / FCPXML を保存

### プレビューの仕組み

プレビューはカット済みファイルを先に作らず、FFmpeg の出力を `ffplay` へ
パイプしてストリーミング再生します。

- 再生開始時に「現在位置から先」の残す区間だけを低画質（幅 640px / ultrafast）で変換
- シーク時は FFmpeg を新しい位置で起動し直し、その位置以降だけを処理
- 再生位置は `ffplay` が実際に表示したフレームのタイムスタンプから取得
- 全体の変換完了を待たないため、1 時間超の素材でも数秒で再生を開始できます
- Windows では `ffplay` の映像をプレビューウィンドウへ埋め込み表示します
- 一時停止は FFmpeg / ffplay を中断して最後のフレームを保持します。一時停止中に
  シークした場合は、再生を押したときにその位置から再開します

## CLI の使い方

```powershell
# 素材の情報
silence-cut probe input.mp4

# 解析だけ（結果を JSON / CSV / EDL / XML / FCPXML に保存）
silence-cut analyze input.mp4 --json cutplan.json --edl cutplan.edl --csv cutplan.csv
silence-cut analyze input.mp4 --exo cutplan.exo --xml cutplan.xml --fcpxml cutplan.fcpxml

# カットして書き出し（無声カット: 発話区間だけ残す。既定）
silence-cut cut input.mp4 -o output.mp4 --crf 20 --preset medium

# カットして書き出し（無音カット: 静かな区間を除去）
silence-cut cut input.mp4 --mode silence -o output.mp4

# しきい値を上げる（無音カット・静かな環境の素材）
silence-cut cut input.mp4 --mode silence -t -50 --min-silence 0.8

# ノイズフロアから自動推定（無音カット）
silence-cut cut input.mp4 --mode silence --adaptive

# ノイズが多い素材で発話検出を厳しめにする
silence-cut cut input.mp4 --mode speech --snr-min 10 --hangover 0.2
```

### パラメータ

| オプション | 既定値 | 意味 |
| --- | --- | --- |
| `--mode` | `speech` | `speech`=無声カット（既定・発話のみ残す） / `silence`=無音カット |
| `-t, --threshold` | `-45` | この値より静かな区間を無音とみなす（dBFS、無音カット用） |
| `-s, --min-silence` | `0.5` | これ以上続く無音/非発話だけをカット（秒） |
| `-m, --min-speech` | `0.15` | これより短い発話は吸収（秒） |
| `--pad-start` | `0.05` | 発話の前に残す余白（秒） |
| `--pad-end` | `0.08` | 発話の後に残す余白（秒） |
| `--adaptive` | off | ノイズフロア + マージンでしきい値を自動決定（無音カット用） |
| `--snr-min` | `8` | 発話の開始とみなすノイズフロアからの SNR（dB、無声カット用） |
| `--snr-exit` | `4` | 発話の終了とみなす SNR（dB、無声カット用） |
| `--hangover` | `0.15` | 発話終了後に発話扱いを続ける長さ（秒） |
| `--noise-percentile` | `10` | ノイズフロア推定に使うパーセンタイル（無声カット用） |
| `--frame-ms` | `20` | 解析フレーム長（ms）。小さくすると精度↑・速度↓ |

### 無声カットの判定方法

1. デジタル無音（-70 dBFS 以下）を除いたフレームからノイズフロアを推定
2. ノイズフロアからの SNR で発話を判定。開始 `snr_min` / 終了 `snr_exit` の
   2 しきい値（ヒステリシス）で細かな on/off を防ぐ
3. ゼロ交差率が高く低域が少ないフレーム（サー音・ヒス）は定常ノイズとして除外
4. `hangover` で発話直後の取りこぼしを救済し、`min_silence` で短い途切れを橋渡し
5. 前後の余白 `pad_start` / `pad_end` を付けてカットプランを確定

音量がほぼ一定でノイズフロアが取れない素材では、2 クラス分離（大津の手法）に
自動フォールバックします。無声カットは解析を 8 kHz で行うため、
16 kHz の無音カットより少しだけ時間がかかります。

## 出力フォーマット

### カットプラン JSON（プラグイン連携用）

```json
{
  "version": 2,
  "source": { "path": "input.mp4", "duration": 12.5, "fps": 29.97,
              "has_video": true, "has_audio": true },
  "settings": { "detection": "speech", "threshold_db": -45.0, "...": "..." },
  "threshold_db": -45.0,
  "summary": { "mode": "speech", "cut_segments": 3, "removed_ratio": 0.31,
               "noise_db": -52.9, "...": "..." },
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
29.97 / 59.94 fps ではドロップフレーム表記（`FCM: DROP FRAME`）になります。

### EXO (AviUtl)

`--exo`（拡張子 `.exo`）で出力します。AviUtl の**拡張編集プラグイン**が
必要です。残す区間を動画レイヤー 1 / 音声レイヤー 2 に順番に並べます。

- AviUtl: `ファイル > オブジェクトファイルのインポート`、または
  拡張編集ウィンドウへ `.exo` をドラッグ＆ドロップ
- 音声のみの素材では、音声オブジェクトをレイヤー 1 に配置します
- `.exo` は AviUtl の仕様に合わせて **Shift_JIS / CRLF** で保存します
  （ファイルパスに Shift_JIS 外の文字があるとエラーになります）

### XMEML (Premiere Pro / DaVinci Resolve)

`--xml`（拡張子 `.xml` / `.xmeml`）で出力します。残す区間だけを並べた
シーケンスで、元素材の絶対パス（`pathurl`）を含みます。

- Premiere Pro: `ファイル > 読み込み` で XML を選択
- DaVinci Resolve: `ファイル > 読み込み > タイムライン` で XML を選択

### FCPXML (Final Cut Pro / DaVinci Resolve)

`--fcpxml`（拡張子 `.fcpxml`）で出力します。Final Cut Pro 10.4.9 以降と
DaVinci Resolve が読み込めます。

- Final Cut Pro: `ファイル > 読み込み > XML` で FCPXML を選択
- DaVinci Resolve: `ファイル > 読み込み > タイムライン` で FCPXML を選択

XMEML / FCPXML は素材を直接参照するため、読み込むだけでカット済みの
タイムラインになります（再リンク作業は不要）。素材を移動した場合は、
読み込み前に元の場所へ戻すか、読み込み後に再リンクしてください。

## 動画編集ソフト別の使い方

| ソフト | おすすめの出力 | 読み込み方 |
| --- | --- | --- |
| Premiere Pro | XMEML (`.xml`) | ファイル > 読み込み |
| DaVinci Resolve | XMEML / FCPXML / EDL | ファイル > 読み込み > タイムライン |
| Final Cut Pro | FCPXML (`.fcpxml`) | ファイル > 読み込み > XML |
| AviUtl | EXO (`.exo`) | 拡張編集へ D&D またはオブジェクトファイルのインポート |
| その他のソフト | EDL / JSON / CSV | 各ソフトの編集リスト読み込み |

EDL は映像・音声を別イベントとして出力するため、多くのソフトで
そのままカット済みタイムラインになります。素材名は `FROM CLIP NAME` に
入るので、読み込み時に元ファイルを指定してください。

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
  settings.py    解析パラメータ
  media.py       FFmpeg 呼び出し・PCM ストリーム解析・特徴量・素材情報
  stats.py       パーセンタイル / 大津のしきい値
  thresholds.py  無音しきい値の決定
  vad.py         発話区間検出（無声カット）
  detect.py      カットプラン生成（純粋関数、FFmpeg 非依存）
  pipeline.py    解析のまとめ役
  export.py      JSON / CSV / EDL / EXO / XMEML / FCPXML 出力
  preview.py     ストリーミングプレビュー（FFmpeg → ffplay）
  dnd.py         ファイルのドラッグ＆ドロップ（tkinterdnd2）
  render.py      FFmpeg によるカット書き出し
  cli.py         CUI
  gui.py         Tkinter GUI
tests/
```

`detect.py` と `vad.py` はファイル I/O を持たないため、単体テストやプラグイン側での再利用が容易です。

## テスト

```powershell
python -m unittest discover -s tests -v
```

FFmpeg が無い環境では、FFmpeg を使うテストだけ自動でスキップされます。

## 制限事項 / 今後拡張しやすい点

- 書き出しは再エンコード方式です（フレーム精度優先）。高速化したい場合は
  `render.py` にキーフレーム単位のカット（ストリームコピー）を追加できます。
- GUI のプレビューは低画質ストリーミングです。シーク時は FFmpeg を起動し直すため、
  再生開始まで少し待つことがあります。最終的な画質は「書き出し...」で確認してください。
- ファイルのドラッグ＆ドロップは `tkinterdnd2` が利用できる環境で有効です
  （使えない場合は「参照...」で選択してください）。
- 可変フレームレート（VFR）素材では EDL / XMEML / FCPXML のタイムコードが
  近似になります。正確な編集が必要な場合は JSON の秒数を使うプラグイン連携を推奨します。
- 自動しきい値・無声カットは音量と簡易スペクトル特徴による判定です。
  話者性の識別はしないため、大きな音楽・環境音・笑い声などは発話として残ることがあります。
  精度が必要な案件では、AI の音声区間検出（VAD / Silero など）の結果を
  `build_plan(..., activity=...)` に渡してください。`vad.detect_speech()` の出力と同じ形です。
- BGM が小音量で流れ続ける素材では、音量ベースの判定は無音になりません。
  その場合も `activity` 経由で AI の VAD を差し込む想定です。
- 無声カットは解析サンプリングを 8 kHz にしています（子音の高域成分は 4 kHz まで）。
  16 kHz で解析したい場合は `pipeline.analyze_file()` の `sample_rate` 相当を変更してください。
- 音声ストリームの開始位置が映像と大きくずれている素材では、カット位置がずれる
  可能性があります。事前に素材を確認してください。
