"""
LLM Selector for Step 2 Filtering
Uses LLM to select the best segments based on semantic summaries
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime
import random

from .aliyun_client import aliyun_client, TextRequest
from .semantic_summarizer import SegmentSummary
from .step2_config import (
    TEXT_MODEL, SELECTION_STRICTNESS,
    MIN_KEEP_RATIO, MAX_KEEP_RATIO,
    NUM_SELECTION_SCHEMES
)

logger = logging.getLogger(__name__)


@dataclass
class SelectionResult:
    """Result of segment selection"""
    scheme_id: int
    scheme_name: str
    scheme_description: str
    selected_segments: List[int]  # segment_ids
    total_duration: float
    selection_rationale: str
    quality_score: float


class LLMSelector:
    """Select best segments using LLM based on semantic summaries"""
    
    SELECTION_PROMPT = """你是一个专业的视频编辑专家，需要从多个视频片段中选择最精彩的内容。

## 任务说明
你将收到多个视频片段的语义摘要，请根据以下标准选择最值得保留的片段：
1. 内容精彩程度（动作、事件、情绪）
2. 画面质量描述（清晰度、稳定性）
3. 叙事连贯性（片段之间的关联）
4. 整体观看体验

## 选择要求
- 严格程度：{strictness}
- 最少保留：{min_keep}个片段
- 最多保留：{max_keep}个片段
- 目标总时长：约{target_duration}分钟

## 片段列表
{segments_info}

## 输出要求
请输出JSON格式：
{{
    "selected_segments": [片段ID列表],
    "rationale": "选择理由说明",
    "quality_score": 整体质量评分(0-1)
}}

请直接输出JSON，不要有其他内容。"""

    MULTI_SCHEME_PROMPT = """你是一个专业的视频编辑专家，需要从多个视频片段中选择最精彩的内容，并生成{num_schemes}套不同的选择方案。

## 任务说明
每套方案应该有不同的侧重点：
- 方案1：精彩瞬间（侧重动作、事件、高潮）
- 方案2：叙事完整（侧重故事性、连贯性）
- 方案3：氛围体验（侧重情绪、氛围、美感）

## 选择要求
- 严格程度：{strictness}
- 每套方案最少保留：{min_keep}个片段
- 每套方案最多保留：{max_keep}个片段

## 片段列表
{segments_info}

## 输出要求
请输出JSON格式：
{{
    "schemes": [
        {{
            "scheme_name": "方案名称",
            "scheme_description": "方案描述",
            "selected_segments": [片段ID列表],
            "rationale": "选择理由",
            "quality_score": 评分(0-1)
        }}
    ]
}}

请直接输出JSON，不要有其他内容。"""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def _format_segments_info(self, summaries: List[SegmentSummary]) -> str:
        """Format segment summaries for LLM prompt"""
        lines = []
        for s in summaries:
            info = f"[ID:{s.segment_id}] 时长:{s.duration:.1f}秒"
            if s.combined_summary:
                info += f"\n  摘要: {s.combined_summary}"
            if s.keywords:
                info += f"\n  关键词: {', '.join(s.keywords)}"
            lines.append(info)
        return "\n\n".join(lines)
    
    def _calculate_keep_range(self, total_segments: int, total_duration: float, target_duration: float) -> tuple:
        """Calculate min and max segments to keep"""
        if total_duration <= 0:
            return 1, max(1, int(total_segments * MIN_KEEP_RATIO))
        
        avg_duration = total_duration / total_segments
        target_segments = int(target_duration * 60 / avg_duration)
        
        min_keep = max(1, int(target_segments * MIN_KEEP_RATIO))
        max_keep = min(total_segments, int(target_segments * MAX_KEEP_RATIO))
        
        return min_keep, max_keep
    
    async def select_segments(
        self,
        summaries: List[SegmentSummary],
        target_duration: float = 15.0,
        num_schemes: int = NUM_SELECTION_SCHEMES
    ) -> List[SelectionResult]:
        """Select best segments using LLM"""
        
        if not summaries:
            logger.warning("No summaries to select from")
            return []
        
        total_duration = sum(s.duration for s in summaries)
        total_segments = len(summaries)
        
        min_keep, max_keep = self._calculate_keep_range(
            total_segments, total_duration, target_duration
        )
        
        segments_info = self._format_segments_info(summaries)
        
        strictness_map = {
            "high": "严格（只保留最精彩的片段）",
            "medium": "适中（保留较为精彩的内容）",
            "low": "宽松（保留大部分内容）"
        }
        strictness_text = strictness_map.get(SELECTION_STRICTNESS, strictness_map["high"])
        
        prompt = self.MULTI_SCHEME_PROMPT.format(
            num_schemes=num_schemes,
            strictness=strictness_text,
            min_keep=min_keep,
            max_keep=max_keep,
            segments_info=segments_info
        )
        
        logger.info(f"Requesting LLM selection for {total_segments} segments...")
        
        request = TextRequest(
            system_prompt="你是一个专业的视频编辑专家，擅长从大量素材中选择最精彩的内容。",
            user_prompt=prompt,
            max_tokens=4000,
            temperature=0.5,
            response_format={"type": "json_object"}
        )
        
        response = await aliyun_client.call_text_model(request)
        
        if not response.success:
            logger.error(f"LLM selection failed: {response.error}")
            return self._fallback_selection(summaries, min_keep, num_schemes)
        
        try:
            result = json.loads(response.content)
            schemes = result.get("schemes", [])
            
            selection_results = []
            for i, scheme in enumerate(schemes[:num_schemes], 1):
                selected_ids = scheme.get("selected_segments", [])
                
                valid_ids = [sid for sid in selected_ids if 0 <= sid < total_segments]
                
                selected_summaries = [s for s in summaries if s.segment_id in valid_ids]
                total_dur = sum(s.duration for s in selected_summaries)
                
                selection_results.append(SelectionResult(
                    scheme_id=i,
                    scheme_name=scheme.get("scheme_name", f"Scheme {i}"),
                    scheme_description=scheme.get("scheme_description", ""),
                    selected_segments=valid_ids,
                    total_duration=total_dur,
                    selection_rationale=scheme.get("rationale", ""),
                    quality_score=scheme.get("quality_score", 0.5)
                ))
            
            logger.info(f"LLM selected {len(selection_results)} schemes")
            return selection_results
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response: {e}")
            return self._fallback_selection(summaries, min_keep, num_schemes)
    
    def _fallback_selection(
        self, 
        summaries: List[SegmentSummary], 
        min_keep: int,
        num_schemes: int
    ) -> List[SelectionResult]:
        """Fallback selection when LLM fails"""
        logger.warning("Using fallback selection")
        
        results = []
        for i in range(num_schemes):
            num_to_select = min(min_keep + i * 2, len(summaries))
            selected = random.sample(
                range(len(summaries)), 
                min(num_to_select, len(summaries))
            )
            
            selected_summaries = [summaries[sid] for sid in selected]
            total_dur = sum(s.duration for s in selected_summaries)
            
            results.append(SelectionResult(
                scheme_id=i + 1,
                scheme_name=f"Fallback Scheme {i + 1}",
                scheme_description="Fallback selection due to LLM failure",
                selected_segments=selected,
                total_duration=total_dur,
                selection_rationale="Random selection fallback",
                quality_score=0.3
            ))
        
        return results
    
    def save_selection_results(
        self, 
        results: List[SelectionResult], 
        output_path: Path
    ) -> None:
        """Save selection results to JSON"""
        data = {
            "total_schemes": len(results),
            "selection_timestamp": datetime.now().isoformat(),
            "schemes": [asdict(r) for r in results]
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Selection results saved to: {output_path}")
    
    def load_selection_results(self, input_path: Path) -> List[SelectionResult]:
        """Load selection results from JSON"""
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        results = []
        for r in data.get("schemes", []):
            results.append(SelectionResult(**r))
        
        return results
