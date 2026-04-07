import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


@dataclass
class ScoredSegment:
    segment_id: int
    start_time: float
    end_time: float
    duration: float
    score: float
    features: Dict[str, float] = field(default_factory=dict)


@dataclass
class Solution:
    strategy_name: str
    strategy_description: str
    segments: List[ScoredSegment]
    total_duration: float
    avg_score: float


def normalize(value: float, min_val: float = 0.0, max_val: float = 1.0) -> float:
    return max(0.0, min(1.0, (value - min_val) / (max_val - min_val) if max_val > min_val else 0.0))


def extract_feature_dict(fine_features) -> Dict[str, float]:
    features = {}
    
    features['coarse_score'] = normalize(fine_features.coarse_score, 0, 1)
    features['stability'] = normalize(fine_features.stability_score, 0, 1)
    features['audio_onset_count'] = normalize(fine_features.audio_onset_count, 0, 10)
    
    if fine_features.face_features:
        features['face_count'] = normalize(fine_features.face_features.face_count, 0, 5)
        features['face_size'] = normalize(fine_features.face_features.avg_face_size, 0, 0.5)
        features['has_large_face'] = 1.0 if fine_features.face_features.has_large_face else 0.0
    else:
        features['face_count'] = 0.0
        features['face_size'] = 0.0
        features['has_large_face'] = 0.0
    
    if fine_features.scene_features:
        features['scene_diversity'] = normalize(fine_features.scene_features.scene_diversity, 0, 1)
    else:
        features['scene_diversity'] = 0.0
    
    if fine_features.speech_features:
        features['speech_ratio'] = normalize(fine_features.speech_features.speech_ratio, 0, 1)
        features['speech_density'] = normalize(fine_features.speech_features.speech_density, 0, 2)
    else:
        features['speech_ratio'] = 0.0
        features['speech_density'] = 0.0
    
    return features


def score_segment(features: Dict[str, float], weights: Dict[str, float]) -> float:
    score = 0.0
    total_weight = 0.0
    
    for feature_name, weight in weights.items():
        if feature_name in features:
            score += weight * features[feature_name]
            total_weight += weight
    
    return score / total_weight if total_weight > 0 else 0.0


def select_segments_greedy(segments: List[ScoredSegment], target_duration: float,
                           max_ratio: float, min_ratio: float) -> List[ScoredSegment]:
    sorted_segments = sorted(segments, key=lambda s: s.score, reverse=True)
    
    selected = []
    total_duration = 0.0
    max_duration = target_duration * max_ratio
    min_duration = target_duration * min_ratio
    
    for segment in sorted_segments:
        if total_duration + segment.duration <= max_duration:
            selected.append(segment)
            total_duration += segment.duration
        
        if total_duration >= min_duration:
            break
    
    selected.sort(key=lambda s: s.start_time)
    
    return selected


def calc_time_overlap(segments1: List[ScoredSegment], segments2: List[ScoredSegment]) -> float:
    if not segments1 or not segments2:
        return 0.0
    
    total_overlap = 0.0
    total_duration = 0.0
    
    for s1 in segments1:
        total_duration += s1.duration
        for s2 in segments2:
            overlap_start = max(s1.start_time, s2.start_time)
            overlap_end = min(s1.end_time, s2.end_time)
            if overlap_start < overlap_end:
                total_overlap += overlap_end - overlap_start
    
    return total_overlap / total_duration if total_duration > 0 else 0.0


def deduplicate_solutions(solutions: List[Solution], overlap_threshold: float,
                          max_solutions: int) -> List[Solution]:
    if not solutions:
        return []
    
    final_solutions = []
    
    for solution in solutions:
        is_different = True
        
        for existing in final_solutions:
            overlap = calc_time_overlap(solution.segments, existing.segments)
            if overlap > overlap_threshold:
                is_different = False
                break
        
        if is_different:
            final_solutions.append(solution)
        
        if len(final_solutions) >= max_solutions:
            break
    
    return final_solutions


class Selector:
    def __init__(self, config, strategies):
        self.config = config
        self.strategies = strategies
    
    def process(self, fine_features_list: List) -> List[Solution]:
        scored_segments = []
        
        for ff in fine_features_list:
            features = extract_feature_dict(ff)
            scored_segments.append(ScoredSegment(
                segment_id=ff.segment_id,
                start_time=ff.start_time,
                end_time=ff.end_time,
                duration=ff.duration,
                score=0.0,
                features=features
            ))
        
        solutions = []
        
        for strategy in self.strategies:
            for segment in scored_segments:
                segment.score = score_segment(segment.features, strategy.weights)
            
            strategy_segments = [ScoredSegment(
                segment_id=s.segment_id,
                start_time=s.start_time,
                end_time=s.end_time,
                duration=s.duration,
                score=s.score,
                features=s.features.copy()
            ) for s in scored_segments]
            
            selected = select_segments_greedy(
                strategy_segments,
                self.config.target_duration,
                self.config.max_duration_ratio,
                self.config.min_duration_ratio
            )
            
            if selected:
                total_duration = sum(s.duration for s in selected)
                avg_score = np.mean([s.score for s in selected])
                
                solutions.append(Solution(
                    strategy_name=strategy.name,
                    strategy_description=strategy.description,
                    segments=selected,
                    total_duration=total_duration,
                    avg_score=avg_score
                ))
        
        solutions.sort(key=lambda s: s.avg_score, reverse=True)
        
        final_solutions = deduplicate_solutions(
            solutions,
            self.config.overlap_threshold,
            self.config.max_solutions
        )
        
        return final_solutions
    
    def get_solution_summary(self, solutions: List[Solution]) -> str:
        lines = []
        lines.append("=" * 60)
        lines.append("片段选择结果摘要")
        lines.append("=" * 60)
        
        for i, solution in enumerate(solutions, 1):
            lines.append(f"\n方案 {i}: {solution.strategy_name}")
            lines.append(f"  描述: {solution.strategy_description}")
            lines.append(f"  总时长: {solution.total_duration:.1f}秒 ({solution.total_duration/60:.1f}分钟)")
            lines.append(f"  平均得分: {solution.avg_score:.4f}")
            lines.append(f"  片段数量: {len(solution.segments)}")
            
            lines.append("  片段详情:")
            for j, seg in enumerate(solution.segments, 1):
                lines.append(f"    {j}. [{seg.start_time:.1f}s - {seg.end_time:.1f}s] "
                           f"时长:{seg.duration:.1f}s 得分:{seg.score:.4f}")
        
        return "\n".join(lines)
