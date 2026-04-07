from dataclasses import dataclass, field
from typing import Dict, List
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def parse_user_config(config_path: Path) -> dict:
    """解析用户配置文件（txt格式）"""
    config_values = {}
    
    if not config_path.exists():
        logger.info(f"用户配置文件不存在: {config_path}，使用默认配置")
        return config_values
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    try:
                        if '.' in value:
                            config_values[key] = float(value)
                        elif value.isdigit():
                            config_values[key] = int(value)
                        else:
                            config_values[key] = value
                    except ValueError:
                        config_values[key] = value
        
        logger.info(f"已加载用户配置: {config_path}")
    except Exception as e:
        logger.warning(f"读取用户配置文件失败: {e}，使用默认配置")
    
    return config_values


@dataclass
class CoarseFilterConfig:
    dhash_weight: float = 0.20
    histogram_weight: float = 0.10
    loudness_weight: float = 0.15
    zcr_weight: float = 0.15
    energy_change_weight: float = 0.15
    onset_weight: float = 0.15
    stability_weight: float = 0.10
    
    sample_fps: float = 1.0
    blur_threshold: float = 100.0
    min_segment_duration: float = 3.0
    merge_gap: float = 2.0
    retain_percentile: float = 40.0


@dataclass
class FineFilterConfig:
    face_count_weight: float = 0.25
    face_size_weight: float = 0.15
    speech_ratio_weight: float = 0.20
    speech_density_weight: float = 0.10
    scene_diversity_weight: float = 0.10
    stability_weight: float = 0.10
    coarse_score_weight: float = 0.10
    
    face_detection_confidence: float = 0.5
    vad_threshold: float = 0.5
    scene_sample_fps: float = 0.5


@dataclass
class StrategyConfig:
    name: str
    description: str
    weights: Dict[str, float]


@dataclass
class SelectorConfig:
    target_duration: float = 30 * 60
    max_duration_ratio: float = 1.1
    min_duration_ratio: float = 0.9
    overlap_threshold: float = 0.5
    max_solutions: int = 3


@dataclass
class Config:
    input_dir: Path = field(default_factory=lambda: Path("data/input"))
    output_dir: Path = field(default_factory=lambda: Path("data/output"))
    
    coarse_filter: CoarseFilterConfig = field(default_factory=CoarseFilterConfig)
    fine_filter: FineFilterConfig = field(default_factory=FineFilterConfig)
    selector: SelectorConfig = field(default_factory=SelectorConfig)
    
    strategies: List[StrategyConfig] = field(default_factory=lambda: [
        StrategyConfig(
            name="social",
            description="优先保留有人脸、有对话的场景",
            weights={
                "face_count": 0.30,
                "face_size": 0.15,
                "speech_ratio": 0.25,
                "speech_density": 0.10,
                "stability": 0.10,
                "coarse_score": 0.10,
            }
        ),
        StrategyConfig(
            name="event",
            description="优先保留有突发事件、音频突变的场景",
            weights={
                "audio_onset_count": 0.30,
                "coarse_score": 0.25,
                "scene_diversity": 0.15,
                "face_count": 0.15,
                "speech_ratio": 0.10,
                "stability": 0.05,
            }
        ),
        StrategyConfig(
            name="exploration",
            description="优先保留用户停下来关注的场景",
            weights={
                "stability": 0.35,
                "scene_diversity": 0.20,
                "face_count": 0.15,
                "speech_ratio": 0.15,
                "coarse_score": 0.15,
            }
        ),
    ])
    
    supported_formats: List[str] = field(default_factory=lambda: [".mp4", ".mkv", ".avi", ".mov"])
    
    def __post_init__(self):
        self.input_dir = Path(self.input_dir)
        self.output_dir = Path(self.output_dir)
    
    def apply_user_config(self, user_config: dict):
        """应用用户配置"""
        if 'target_duration_minutes' in user_config:
            self.selector.target_duration = user_config['target_duration_minutes'] * 60
            logger.info(f"目标时长设置为: {user_config['target_duration_minutes']} 分钟")
        
        if 'max_solutions' in user_config:
            self.selector.max_solutions = user_config['max_solutions']
            logger.info(f"最大方案数设置为: {user_config['max_solutions']}")
        
        if 'input_dir' in user_config:
            self.input_dir = Path(user_config['input_dir'])
            logger.info(f"输入目录设置为: {self.input_dir}")
        
        if 'output_dir' in user_config:
            self.output_dir = Path(user_config['output_dir'])
            logger.info(f"输出目录设置为: {self.output_dir}")
        
        if 'coarse_sample_fps' in user_config:
            self.coarse_filter.sample_fps = user_config['coarse_sample_fps']
        
        if 'min_segment_duration' in user_config:
            self.coarse_filter.min_segment_duration = user_config['min_segment_duration']
        
        if 'merge_gap' in user_config:
            self.coarse_filter.merge_gap = user_config['merge_gap']


config = Config()

user_config_path = Path(__file__).parent.parent / "user_config.txt"
user_config_values = parse_user_config(user_config_path)
config.apply_user_config(user_config_values)
