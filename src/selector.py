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
    # 用于多样性去重 (P3)
    scene_key: str = ""
    clip_embedding: Optional[np.ndarray] = None


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
        clip_div = getattr(fine_features.scene_features, 'clip_diversity', 0.0) or 0.0
        heur_div = fine_features.scene_features.scene_diversity or 0.0
        # 有真实 CLIP 语义多样性时优先使用
        features['scene_diversity'] = normalize(clip_div if clip_div > 0 else heur_div, 0, 1)
        features['clip_diversity'] = normalize(clip_div, 0, 1)
    else:
        features['scene_diversity'] = 0.0
        features['clip_diversity'] = 0.0
    
    if fine_features.speech_features:
        features['speech_ratio'] = normalize(fine_features.speech_features.speech_ratio, 0, 1)
        features['speech_density'] = normalize(fine_features.speech_features.speech_density, 0, 2)
    else:
        features['speech_ratio'] = 0.0
        features['speech_density'] = 0.0
    
    # YOLO 物体级语义特征 (P1)
    if fine_features.object_features:
        of = fine_features.object_features
        features['pet_presence'] = normalize(of.pet_presence, 0, 1)
        features['pet_count'] = normalize(of.pet_count, 0, 3)
        features['person_count'] = normalize(of.person_count, 0, 5)
        features['interaction_ratio'] = normalize(of.interaction_ratio, 0, 1)
        features['toy_presence'] = normalize(of.toy_presence, 0, 1)
        features['object_diversity'] = normalize(of.object_diversity, 0, 1)
        features['action_intensity'] = normalize(of.action_intensity, 0, 1)
        features['has_close_pet'] = 1.0 if of.has_close_pet > 0.3 else 0.0
    else:
        for k in ('pet_presence', 'pet_count', 'person_count', 'interaction_ratio',
                  'toy_presence', 'object_diversity', 'action_intensity', 'has_close_pet'):
            features[k] = 0.0
    
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


def select_segments_constrained(segments: List[ScoredSegment], target_duration: float,
                                max_ratio: float, min_ratio: float,
                                min_gap: float = 3.0,
                                diversity_threshold: float = 0.92) -> List[ScoredSegment]:
    """
    带约束的选择 (P3):
    - 总时长约束 (0/1 背包式, 允许小超调)
    - 片段间最小时间间隔 (避免高光扎堆)
    - 语义/场景多样性去重 (避免连续选中相似片段)

    若约束过严导致结果不足, 自动回退到贪心选择。
    """
    max_duration = target_duration * max_ratio
    min_duration = target_duration * min_ratio
    sorted_segments = sorted(segments, key=lambda s: s.score, reverse=True)

    def _build(pick):
        return sorted((s for s in segments if s.segment_id in pick), key=lambda s: s.start_time)

    def _diverse(seg, selected):
        """判断新片段是否与已选片段过于相似"""
        for s in selected:
            # 场景类型相同 且 时间上相邻 -> 认为相似
            if seg.scene_key and seg.scene_key == s.scene_key:
                t_overlap = min(seg.end_time, s.end_time) - max(seg.start_time, s.start_time)
                if t_overlap > 0:
                    return False
                if min(seg.duration, s.duration) > 0 and \
                   abs(seg.start_time - s.start_time) < max(min_gap, min(seg.duration, s.duration)):
                    return False
            # CLIP embedding 余弦相似度太高
            if (seg.clip_embedding is not None and s.clip_embedding is not None):
                sim = float(np.dot(seg.clip_embedding, s.clip_embedding))
                if sim > diversity_threshold:
                    return False
        return True

    selected = []
    total = 0.0
    for seg in sorted_segments:
        if total + seg.duration > max_duration:
            continue
        # 时间间隔约束: 与已选片段保持间隔 (允许边界相接)
        ok_gap = True
        for s in selected:
            if seg.start_time < s.end_time + min_gap and seg.end_time > s.start_time - min_gap:
                # 相邻但没有重叠时, 若场景不同仍允许
                if seg.scene_key and seg.scene_key != s.scene_key:
                    continue
                ok_gap = False
                break
        if not ok_gap:
            continue
        if not _diverse(seg, selected):
            continue
        selected.append(seg)
        total += seg.duration
        if total >= max_duration:
            break

    result = _build({s.segment_id for s in selected})

    # 约束过严导致结果不足 -> 回退到贪心
    if sum(s.duration for s in result) < min_duration * 0.5 and len(segments) > 1:
        logger.info("约束选择结果不足, 回退到贪心选择")
        return select_segments_greedy(segments, target_duration, max_ratio, min_ratio)
    return result


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
            scene_key = ""
            clip_embedding = None
            if ff.scene_features is not None:
                scene_key = ff.scene_features.dominant_scene or ""
                emb = getattr(ff.scene_features, 'clip_embedding', None)
                if emb is not None:
                    emb = np.asarray(emb, dtype=np.float32).reshape(-1)
                    norm = np.linalg.norm(emb)
                    clip_embedding = emb / norm if norm > 0 else emb
            scored_segments.append(ScoredSegment(
                segment_id=ff.segment_id,
                start_time=ff.start_time,
                end_time=ff.end_time,
                duration=ff.duration,
                score=0.0,
                features=features,
                scene_key=scene_key,
                clip_embedding=clip_embedding
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
                features=s.features.copy(),
                scene_key=s.scene_key,
                clip_embedding=s.clip_embedding
            ) for s in scored_segments]
            
            if getattr(self.config, 'use_constrained_selection', True):
                selected = select_segments_constrained(
                    strategy_segments,
                    self.config.target_duration,
                    self.config.max_duration_ratio,
                    self.config.min_duration_ratio,
                    min_gap=getattr(self.config, 'min_gap', 3.0),
                    diversity_threshold=getattr(self.config, 'diversity_threshold', 0.92)
                )
            else:
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
