from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .media import AudioLevels
from .settings import AnalysisSettings
from .stats import otsu_threshold, percentile

# これ以下のフレームはデジタル無音とみなし、ノイズフロア推定から除外する
ABSOLUTE_FLOOR_DB = -70.0
# ノイズフロアと発話レベルの差がこれ未満なら 2 クラス分離できない
MIN_CONTRAST_DB = 6.0
# 定常的な広帯域ノイズ (サー音・ヒス) を弾く条件
HISS_ZCR = 0.35
HISS_LOW_RATIO = 0.55
HISS_MARGIN_DB = 10.0


@dataclass
class SpeechActivity:
    """フレームごとの発話判定結果。"""

    mask: List[bool]
    noise_db: float
    threshold_db: float
    fallback: bool = False

    @property
    def speech_frames(self) -> int:
        return sum(1 for value in self.mask if value)


def _fallback_threshold(db: List[float], settings: AnalysisSettings) -> float:
    estimated = (
        percentile(db, settings.adaptive_percentile / 100.0)
        + settings.adaptive_margin_db
    )
    separated = otsu_threshold(db)
    return max(-80.0, min(min(estimated, separated), -20.0))


def _apply_hangover(mask: List[bool], frames: int) -> List[bool]:
    if frames <= 0 or not mask:
        return mask
    result = mask[:]
    remaining = 0
    for index, active in enumerate(mask):
        if active:
            remaining = frames
        elif remaining > 0:
            result[index] = True
            remaining -= 1
    return result


def detect_speech(levels: AudioLevels, settings: AnalysisSettings) -> SpeechActivity:
    """エネルギー + スペクトル特徴から発話区間を判定する。

    1. デジタル無音を除いたフレームからノイズフロアを推定
    2. ノイズフロアからの SNR で発話を判定 (開始/終了で別しきい値 = ヒステリシス)
    3. サー音・ヒスのような定常的な広帯域ノイズを ZCR と低域比で除去
    4. 発話終了後の取りこぼしを hangover で救済

    音楽や環境音が大きい素材では誤判定が残るため、その場合は AI の VAD を
    ``build_plan(..., activity=...)`` で差し込む。
    """
    db = levels.db
    if not db:
        return SpeechActivity([], settings.threshold_db, settings.threshold_db)

    audible = [value for value in db if value > ABSOLUTE_FLOOR_DB] or list(db)
    noise_db = percentile(audible, settings.noise_percentile / 100.0)
    loud_db = percentile(db, 0.9)

    if loud_db - noise_db < MIN_CONTRAST_DB:
        # ノイズフロアが取れない (ほぼ一定音量) → 2 クラス分離へフォールバック
        threshold = _fallback_threshold(db, settings)
        mask = [value >= threshold for value in db]
        return SpeechActivity(mask, noise_db, threshold, fallback=True)

    enter_db = settings.snr_min_db
    exit_db = settings.snr_exit_db
    has_features = levels.has_spectral_features
    zcr = levels.zcr
    low_ratio = levels.low_ratio

    mask: List[bool] = []
    active = False
    for index, value in enumerate(db):
        snr = value - noise_db
        active = snr >= exit_db if active else snr >= enter_db
        if active and has_features:
            if (snr < enter_db + HISS_MARGIN_DB
                    and zcr[index] >= HISS_ZCR
                    and low_ratio[index] <= HISS_LOW_RATIO):
                active = False
        mask.append(active)

    hangover_frames = int(round(settings.hangover / levels.frame_seconds))
    mask = _apply_hangover(mask, hangover_frames)
    return SpeechActivity(
        mask=mask,
        noise_db=noise_db,
        threshold_db=min(-20.0, noise_db + enter_db),
    )
