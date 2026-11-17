"""プラグイン側から共通コアを使う最小例。

どの編集ソフト向けであっても「カットプランを得る → 区間を並べる」という
流れは同じです。ホストの API 呼び出しだけを差し替えてください。

    python examples/plugin_adapter_demo.py input.mp4
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from silence_cut import AnalysisSettings, analyze_file
from silence_cut.export import to_json


def build_trim_operations(source: Path) -> Tuple[object, List[dict]]:
    """ホストへ渡すトリム操作のリスト (ソフト非依存の形)。"""
    result = analyze_file(source, AnalysisSettings())
    print(result.plan.describe())
    operations = [
        {
            "clip": source.name,
            "source_in": item["source_start"],
            "source_out": item["source_end"],
            "timeline_in": item["record_start"],
            "timeline_out": item["record_end"],
        }
        for item in result.plan.timeline()
    ]
    return result, operations


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print("使い方: python examples/plugin_adapter_demo.py <動画/音声>", file=sys.stderr)
        return 2
    source = Path(argv[1])
    result, operations = build_trim_operations(source)

    # ここがプラグイン固有の部分。例: タイムラインへ順番に配置する。
    for operation in operations:
        print(
            "配置: {clip} {source_in:.3f}-{source_out:.3f}s "
            "-> {timeline_in:.3f}-{timeline_out:.3f}s".format(**operation)
        )

    # ホスト側に設定として保存する場合は JSON をそのまま渡せる。
    Path("cutplan.json").write_text(to_json(result.plan) + "\n", encoding="utf-8")
    print("cutplan.json を保存しました")
    print(json.dumps(operations, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
