"""
Step 2 Filter - Semantic Analysis and LLM Selection
Main entry point for the second stage filtering process
"""
import asyncio
import json
import logging
import shutil
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass, asdict
from datetime import datetime

from .semantic_summarizer import SemanticSummarizer, SegmentSummary
from .llm_selector import SelectionResult
from .advanced_llm_selector import AdvancedLLMSelector
from .step2_config import NUM_SELECTION_SCHEMES

logger = logging.getLogger(__name__)


@dataclass
class Step2Result:
    """Complete result of step 2 filtering"""
    video_name: str
    input_segments_dir: str
    total_input_segments: int
    processing_timestamp: str
    summaries: List[SegmentSummary]
    selection_results: List[SelectionResult]
    output_dirs: List[str]


class Step2Filter:
    """Step 2 Filter: Semantic Analysis and LLM Selection"""
    
    def __init__(self, output_base_dir: Path):
        # Create step-specific output directory
        self.output_base_dir = Path(output_base_dir) / "step2_output"
        self.output_base_dir.mkdir(parents=True, exist_ok=True)
        
    def _find_step1_results(self, step1_output_dir: Path) -> List[Path]:
        """Find segment directories from step 1 output"""
        segment_dirs = []
        
        for solution_dir in step1_output_dir.glob("solution_*"):
            segments_dir = solution_dir / "segments"
            if segments_dir.exists():
                segment_dirs.append(segments_dir)
        
        return segment_dirs
    
    def _get_segment_files(self, segments_dir: Path) -> List[Path]:
        """Get all segment video files from directory"""
        return sorted(segments_dir.glob("segment_*.mp4"))
    
    def _load_segment_metadata(self, segments_dir: Path) -> dict:
        """Load segment metadata from segments.json"""
        solution_dir = segments_dir.parent
        metadata_file = solution_dir / "segments.json"
        
        if metadata_file.exists():
            with open(metadata_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    
    async def process_solution(
        self,
        segments_dir: Path,
        video_name: str,
        solution_name: str,
        target_duration: float = 15.0
    ) -> Optional[Step2Result]:
        """Process a single solution from step 1"""
        
        logger.info(f"Processing solution: {solution_name}")
        
        segment_files = self._get_segment_files(segments_dir)
        if not segment_files:
            logger.warning(f"No segment files found in {segments_dir}")
            return None
        
        metadata = self._load_segment_metadata(segments_dir)
        segments_info = metadata.get("segments", [])
        
        durations = []
        for seg_file in segment_files:
            seg_id = int(seg_file.stem.split("_")[1]) - 1
            if seg_id < len(segments_info):
                durations.append(segments_info[seg_id].get("duration", 5.0))
            else:
                durations.append(5.0)
        
        output_dir = self.output_base_dir / "step2_output"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        summarizer = SemanticSummarizer(output_dir)
        
        summaries_file = output_dir / "semantic_summaries.json"
        if summaries_file.exists():
            logger.info(f"Loading existing summaries from {summaries_file}")
            summaries = summarizer.load_summaries(summaries_file)
        else:
            logger.info(f"Generating semantic summaries for {len(segment_files)} segments...")
            summaries = await summarizer.process_segments(segment_files, durations)
            summarizer.save_summaries(summaries, summaries_file)
        
        # Advanced multi-dimensional scoring and intelligent selection
        selector = AdvancedLLMSelector(output_dir)
        
        selection_file = output_dir / "selection_results.json"
        if selection_file.exists():
            logger.info(f"Loading existing selection from {selection_file}")
            selection_results = selector.load_selection_results(selection_file)
        else:
            logger.info("Running advanced multi-dimensional scoring and intelligent selection...")
            
            selection_results = selector.select_segments(
                summaries=summaries,
                target_duration=min(target_duration, 120.0),  # Cap at 2 minutes for intelligent selection
                num_strategies=NUM_SELECTION_SCHEMES
            )
            
            selector.save_selection_results(selection_results, selection_file)
        
        output_dirs = self._organize_selected_segments(
            segment_files, 
            summaries, 
            selection_results, 
            output_dir
        )
        
        return Step2Result(
            video_name=video_name,
            input_segments_dir=str(segments_dir),
            total_input_segments=len(segment_files),
            processing_timestamp=datetime.now().isoformat(),
            summaries=summaries,
            selection_results=selection_results,
            output_dirs=output_dirs
        )
    
    def _organize_selected_segments(
        self,
        segment_files: List[Path],
        summaries: List[SegmentSummary],
        selection_results: List[SelectionResult],
        output_dir: Path
    ) -> List[str]:
        """Organize selected segments into output directories"""
        
        output_dirs = []
        
        for result in selection_results:
            # Create scheme directory directly under output_dir
            scheme_dir = output_dir / f"scheme_{result.scheme_id}_{result.scheme_name}"
            scheme_dir.mkdir(parents=True, exist_ok=True)
            
            selected_dir = scheme_dir / "selected_segments"
            selected_dir.mkdir(parents=True, exist_ok=True)
            
            for seg_id in result.selected_segments:
                if seg_id < len(segment_files):
                    src_file = segment_files[seg_id]
                    dst_file = selected_dir / src_file.name
                    if not dst_file.exists():
                        shutil.copy2(src_file, dst_file)
            
            scheme_summary = {
                "scheme_id": result.scheme_id,
                "scheme_name": result.scheme_name,
                "scheme_description": result.scheme_description,
                "total_duration": result.total_duration,
                "selection_rationale": result.selection_rationale,
                "quality_score": result.quality_score,
                "selected_segments": [
                    {
                        "segment_id": seg_id,
                        "summary": summaries[seg_id].combined_summary if seg_id < len(summaries) else "",
                        "duration": summaries[seg_id].duration if seg_id < len(summaries) else 0
                    }
                    for seg_id in result.selected_segments
                ]
            }
            
            with open(scheme_dir / "scheme_info.json", 'w', encoding='utf-8') as f:
                json.dump(scheme_summary, f, ensure_ascii=False, indent=2)
            
            output_dirs.append(str(scheme_dir))
            logger.info(f"Scheme {result.scheme_id}: {len(result.selected_segments)} segments, "
                       f"{result.total_duration:.1f}s total")
        
        return output_dirs
    
    async def process_video(
        self,
        step1_output_dir: Path,
        video_name: str,
        target_duration: float = 15.0
    ) -> List[Step2Result]:
        """Process the main solution from step 1 for a video (only one solution branch)"""
        
        segment_dirs = self._find_step1_results(step1_output_dir)
        
        if not segment_dirs:
            logger.warning(f"No step 1 results found in {step1_output_dir}")
            return []
        
        logger.info(f"Found {len(segment_dirs)} solutions to process, using only the first one")
        
        # Only process the first solution (main branch) to simplify structure
        if segment_dirs:
            segments_dir = segment_dirs[0]  # Only use the first solution
            solution_name = segments_dir.parent.name
            
            result = await self.process_solution(
                segments_dir=segments_dir,
                video_name=video_name,
                solution_name=solution_name,
                target_duration=target_duration
            )
            
            if result:
                return [result]
        
        return []
    
    def save_final_report(self, results: List[Step2Result], output_path: Path) -> None:
        """Save final report of step 2 processing"""
        report = {
            "processing_timestamp": datetime.now().isoformat(),
            "total_videos_processed": len(results),
            "results": [asdict(r) for r in results]
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Final report saved to: {output_path}")


async def run_step2_filter(
    step1_output_dir: Path,
    output_base_dir: Path,
    video_name: str,
    target_duration: float = 15.0
) -> List[Step2Result]:
    """Main entry point for step 2 filter"""
    
    step2 = Step2Filter(output_base_dir)
    results = await step2.process_video(
        step1_output_dir=step1_output_dir,
        video_name=video_name,
        target_duration=target_duration
    )
    
    if results:
        step2.save_final_report(
            results, 
            output_base_dir / f"{video_name}_step2_report.json"
        )
    
    return results
