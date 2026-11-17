"""無音カットの共通コア。

動画編集ソフトのプラグインからも、事前処理の単体アプリからも同じ解析結果を
使えるようにするため、このパッケージは「カットプラン」を中心に設計している。

    from silence_cut import AnalysisSettings, analyze_file

    result = analyze_file("input.mp4", AnalysisSettings())
    print(result.plan.summary())
"""

from .settings import AnalysisSettings
from .detect import CutPlan, Span, build_plan
from .media import AudioLevels, MediaError, MediaInfo, measure_levels, probe
from .pipeline import AnalysisResult, analyze_file
from .render import RenderSettings, render
from .vad import SpeechActivity, detect_speech

__version__ = "0.2.0"

__all__ = [
    "AnalysisResult",
    "AnalysisSettings",
    "AudioLevels",
    "CutPlan",
    "MediaError",
    "MediaInfo",
    "RenderSettings",
    "Span",
    "SpeechActivity",
    "__version__",
    "analyze_file",
    "build_plan",
    "detect_speech",
    "measure_levels",
    "probe",
    "render",
]
