"""
Multi-dimensional Scoring and Intelligent Selection Module
Handles multi-dimensional evaluation and intelligent selection of video segments
"""
import json
import logging
from typing import List, Dict, Tuple
from dataclasses import dataclass
from pathlib import Path

from .semantic_summarizer import SegmentSummary

logger = logging.getLogger(__name__)


@dataclass
class SelectedSegment:
    """Selected segment with enhanced information"""
    segment_id: int
    start_time: float
    end_time: float
    duration: float
    summary: str
    keywords: List[str]
    scores: Dict[str, float]
    original_path: str


@dataclass
class SelectionResult:
    """Result of intelligent selection"""
    selected_segments: List[SelectedSegment]
    total_duration: float
    selection_strategy: str
    selection_reason: str


class MultiDimensionalScorer:
    """Handles multi-dimensional scoring and intelligent selection"""
    
    def __init__(self):
        self.selection_strategies = {
            'balanced': self._select_balanced,
            'engaging': self._select_engaging,
            'emotional': self._select_emotional,
            'diverse': self._select_diverse,
            'highlight': self._select_highlight
        }
    
    def select_best_segments(
        self, 
        segments: List[SegmentSummary], 
        target_duration: float = 120.0  # 2 minutes
    ) -> List[SelectionResult]:
        """
        Select best segments using multiple creative strategies
        Returns multiple selection results for user choice
        """
        results = []
        
        for strategy_name, strategy_func in self.selection_strategies.items():
            try:
                selected = strategy_func(segments, target_duration)
                if selected:
                    result = SelectionResult(
                        selected_segments=selected,
                        total_duration=sum(s.duration for s in selected),
                        selection_strategy=strategy_name,
                        selection_reason=f"Selected using {strategy_name} strategy"
                    )
                    results.append(result)
            except Exception as e:
                logger.error(f"Strategy {strategy_name} failed: {e}")
                continue
        
        return results
    
    def _calculate_composite_score(self, segment: SegmentSummary, weights: Dict[str, float]) -> float:
        """Calculate composite score based on weighted dimensions"""
        score = 0.0
        total_weight = 0.0
        
        for dimension, weight in weights.items():
            dim_score = segment.quality_scores.get(dimension, 0.0)
            score += dim_score * weight
            total_weight += weight
        
        return score / total_weight if total_weight > 0 else 0.0
    
    def _select_balanced(self, segments: List[SegmentSummary], target_duration: float) -> List[SelectedSegment]:
        """Balanced selection considering all dimensions equally"""
        weights = {
            'content_richness': 1.0,
            'pet_behavior': 1.0,
            'interaction_level': 1.0,
            'emotional_value': 1.0,
            'uniqueness': 1.0,
            'engagement_potential': 1.0,
            'entertainment_value': 1.0
        }
        
        return self._greedy_selection(segments, target_duration, weights, "Balanced strategy")
    
    def _select_engaging(self, segments: List[SegmentSummary], target_duration: float) -> List[SelectedSegment]:
        """Focus on engagement and entertainment value"""
        weights = {
            'engagement_potential': 2.0,
            'entertainment_value': 2.0,
            'uniqueness': 1.5,
            'content_richness': 1.0,
            'pet_behavior': 1.0
        }
        
        return self._greedy_selection(segments, target_duration, weights, "Engaging strategy")
    
    def _select_emotional(self, segments: List[SegmentSummary], target_duration: float) -> List[SelectedSegment]:
        """Focus on emotional value and interactions"""
        weights = {
            'emotional_value': 2.0,
            'interaction_level': 2.0,
            'pet_behavior': 1.5,
            'content_richness': 1.0,
            'uniqueness': 1.0
        }
        
        return self._greedy_selection(segments, target_duration, weights, "Emotional strategy")
    
    def _select_diverse(self, segments: List[SegmentSummary], target_duration: float) -> List[SelectedSegment]:
        """Select diverse content types to maintain interest"""
        # First sort by diversity indicators
        sorted_segments = sorted(segments, 
                                key=lambda s: (s.quality_scores.get('uniqueness', 0) + 
                                            s.quality_scores.get('content_richness', 0)) / 2,
                                reverse=True)
        
        selected = []
        current_duration = 0.0
        
        for segment in sorted_segments:
            if current_duration + segment.duration <= target_duration:
                # Calculate start time from segment path or estimate
                start_time = self._estimate_start_time(segment.segment_id, segments)
                
                selected_segment = SelectedSegment(
                    segment_id=segment.segment_id,
                    start_time=start_time,
                    end_time=start_time + segment.duration,
                    duration=segment.duration,
                    summary=segment.combined_summary,
                    keywords=segment.keywords,
                    scores=segment.quality_scores,
                    original_path=segment.segment_path
                )
                
                selected.append(selected_segment)
                current_duration += segment.duration
                
                if current_duration >= target_duration * 0.9:  # Allow slight overshoot
                    break
        
        return selected
    
    def _select_highlight(self, segments: List[SegmentSummary], target_duration: float) -> List[SelectedSegment]:
        """Select highest-scoring segments focusing on peak moments"""
        weights = {
            'pet_behavior': 2.0,
            'uniqueness': 2.0,
            'entertainment_value': 1.5,
            'emotional_value': 1.5,
            'interaction_level': 1.0
        }
        
        return self._greedy_selection(segments, target_duration, weights, "Highlight strategy")
    
    def _greedy_selection(self, segments: List[SegmentSummary], target_duration: float, 
                         weights: Dict[str, float], strategy_desc: str) -> List[SelectedSegment]:
        """Greedy selection based on weighted scores"""
        # Calculate composite scores
        scored_segments = []
        for segment in segments:
            composite_score = self._calculate_composite_score(segment, weights)
            scored_segments.append((segment, composite_score))
        
        # Sort by score descending
        scored_segments.sort(key=lambda x: x[1], reverse=True)
        
        selected = []
        current_duration = 0.0
        
        for segment, score in scored_segments:
            if current_duration + segment.duration <= target_duration:
                # Calculate start time from segment path or estimate
                start_time = self._estimate_start_time(segment.segment_id, segments)
                
                selected_segment = SelectedSegment(
                    segment_id=segment.segment_id,
                    start_time=start_time,
                    end_time=start_time + segment.duration,
                    duration=segment.duration,
                    summary=segment.combined_summary,
                    keywords=segment.keywords,
                    scores=segment.quality_scores,
                    original_path=segment.segment_path
                )
                
                selected.append(selected_segment)
                current_duration += segment.duration
                
                if current_duration >= target_duration * 0.95:  # Allow small overshoot
                    break
        
        return selected
    
    def _estimate_start_time(self, segment_id: int, all_segments: List[SegmentSummary]) -> float:
        """Estimate start time based on segment ID and ordering"""
        # Simple estimation: assume segments are ordered by time
        # In practice, you might have more sophisticated time mapping
        return segment_id * 5.0  # Assume average 5 seconds per segment ID