from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from typing import Any, Dict


@dataclass(frozen=True)
class AnalysisSettings:
    """無音判定のパラメータ。

    すべて秒・dBFS 単位。dBFS は 0 dB がフルスケールで、値が小さいほど静か。
    """

    # この値より静かな区間を無音とみなす (dBFS)
    threshold_db: float = -45.0
    # これ以上続く無音だけをカット対象にする (秒)
    min_silence: float = 0.5
    # これより短い発話は無音とみなして吸収する (秒)
    min_speech: float = 0.15
    # 発話の前後に残す余白 (秒)
    pad_start: float = 0.05
    pad_end: float = 0.08
    # 自動しきい値: ノイズフロア推定値 + マージンを使う
    adaptive: bool = False
    adaptive_margin_db: float = 9.0
    adaptive_percentile: float = 15.0
    # 解析フレーム長 (ミリ秒)。小さいほど精度が上がるが処理は遅くなる
    frame_ms: float = 20.0

    def validated(self) -> "AnalysisSettings":
        if not -100.0 <= self.threshold_db <= 0.0:
            raise ValueError("しきい値は -100〜0 dB の範囲で指定してください")
        if not 0.0 <= self.min_silence <= 600.0:
            raise ValueError("最小無音長は 0〜600 秒の範囲で指定してください")
        if not 0.0 <= self.min_speech <= 60.0:
            raise ValueError("最小発話長は 0〜60 秒の範囲で指定してください")
        if not 0.0 <= self.pad_start <= 10.0 or not 0.0 <= self.pad_end <= 10.0:
            raise ValueError("余白は 0〜10 秒の範囲で指定してください")
        if not 1.0 <= self.frame_ms <= 200.0:
            raise ValueError("フレーム長は 1〜200 ms の範囲で指定してください")
        if self.adaptive:
            if not 0.0 <= self.adaptive_percentile <= 100.0:
                raise ValueError("ノイズフロア推定パーセンタイルは 0〜100 で指定してください")
            if not 0.0 <= self.adaptive_margin_db <= 60.0:
                raise ValueError("自動しきい値のマージンは 0〜60 dB で指定してください")
        return self

    def with_(self, **changes: Any) -> "AnalysisSettings":
        return replace(self, **changes)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AnalysisSettings":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})
