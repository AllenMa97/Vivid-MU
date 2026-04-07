"""
Advanced LLM Selector with Multi-dimensional Scoring
Handles intelligent selection based on multi-dimensional quality scores
"""
import json
import logging
from typing import List, Dict, Optional
from dataclasses import dataclass
from pathlib import Path

from .semantic_summarizer import SegmentSummary
from .multi_dimensional_scorer import MultiDimensionalScorer, SelectedSegment, SelectionResult as MultiDimSelectionResult

logger = logging.getLogger(__name__)


@dataclass
class SelectionResult:
    """Result of segment selection"""
    scheme_id: int
    scheme_name: str
    scheme_description: str
    selected_segments: List[int]  # List of segment IDs
    total_duration: float
    selection_rationale: str
    quality_score: float


class AdvancedLLMSelector:
    """Advanced selector with multi-dimensional scoring and intelligent selection"""
    
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.scorer = MultiDimensionalScorer()
    
    def select_segments(
        self,
        summaries: List[SegmentSummary],
        target_duration: float = 120.0,  # 2 minutes default
        num_strategies: int = 3
    ) -> List[SelectionResult]:
        """
        Select segments using multiple intelligent strategies
        """
        logger.info(f"Performing multi-dimensional selection for {len(summaries)} segments, "
                   f"target duration: {target_duration}s")
        
        # Get multi-dimensional selections using different strategies
        multi_dim_results = self.scorer.select_best_segments(
            segments=summaries,
            target_duration=target_duration
        )
        
        # Convert to compatible SelectionResult format
        results = []
        for i, multi_result in enumerate(multi_dim_results[:num_strategies]):
            # Calculate average quality score from selected segments
            avg_quality = 0.0
            if multi_result.selected_segments:
                total_score = sum(seg.scores.get('overall', 0.0) for seg in multi_result.selected_segments)
                avg_quality = total_score / len(multi_result.selected_segments)
            
            result = SelectionResult(
                scheme_id=i + 1,
                scheme_name=multi_result.selection_strategy,
                scheme_description=multi_result.selection_reason,
                selected_segments=[seg.segment_id for seg in multi_result.selected_segments],
                total_duration=multi_result.total_duration,
                selection_rationale=f"Multi-dimensional scoring using {multi_result.selection_strategy} strategy",
                quality_score=avg_quality
            )
            
            results.append(result)
        
        logger.info(f"Generated {len(results)} selection schemes")
        return results
    
    def save_selection_results(self, results: List[SelectionResult], output_path: Path) -> None:
        """Save selection results to JSON file"""
        data = {
            "total_schemes": len(results),
            "selection_timestamp": "TODO",  # Would add timestamp
            "schemes": [
                {
                    "scheme_id": r.scheme_id,
                    "scheme_name": r.scheme_name,
                    "scheme_description": r.scheme_description,
                    "selected_segments": r.selected_segments,
                    "total_duration": r.total_duration,
                    "selection_rationale": r.selection_rationale,
                    "quality_score": r.quality_score
                }
                for r in results
            ]
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Selection results saved to: {output_path}")
    
    def load_selection_results(self, input_path: Path) -> List[SelectionResult]:
        """Load selection results from JSON file"""
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        results = []
        for scheme_data in data.get("schemes", []):
            result = SelectionResult(
                scheme_id=scheme_data["scheme_id"],
                scheme_name=scheme_data["scheme_name"],
                scheme_description=scheme_data["scheme_description"],
                selected_segments=scheme_data["selected_segments"],
                total_duration=scheme_data["total_duration"],
                selection_rationale=scheme_data["selection_rationale"],
                quality_score=scheme_data.get("quality_score", 0.0)
            )
            results.append(result)
        
        logger.info(f"Loaded {len(results)} selection schemes from: {input_path}")
        return results