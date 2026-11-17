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

__version__ = "0.1.0"

__all__ = [
    "AnalysisResult",
    "AnalysisSettings",
    "AudioLevels",
    "CutPlan",
    "MediaError",
    "MediaInfo",
    "RenderSettings",
    "Span",
    "__version__",
    "analyze_file",
    "build_plan",
    "measure_levels",
    "probe",
    "render",
]
