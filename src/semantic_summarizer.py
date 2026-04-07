"""
Semantic Summarizer for Video Segments
Uses VL model and Audio model to generate semantic summaries
"""
import asyncio
import json
import logging
import tempfile
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime
import subprocess

from .aliyun_client import aliyun_client, VLRequest, AudioRequest, TextRequest, VideoRequest
from .step2_config import (
    SUMMARY_MAX_LENGTH, OUTPUT_LANGUAGE,
    MAX_SEGMENTS_TO_PROCESS
)

logger = logging.getLogger(__name__)


@dataclass
class SegmentSummary:
    """Semantic summary of a video segment"""
    segment_id: int
    segment_path: str
    duration: float
    visual_summary: str = ""
    audio_summary: str = ""
    combined_summary: str = ""
    keywords: List[str] = field(default_factory=list)
    scene_type: str = ""
    action_type: str = ""
    emotion: str = ""
    quality_scores: Dict[str, float] = field(default_factory=dict)  # Multi-dimensional scores
    processing_time: float = 0.0
    error: Optional[str] = None
    
    @property
    def overall_score(self) -> float:
        """Overall quality score"""
        return self.quality_scores.get('overall', 0.0)


class SemanticSummarizer:
    """Generate semantic summaries for video segments using multi-modal models"""
    
    VISUAL_PROMPT = """请分析这段宠物视频内容，用简洁的中文描述视频中的主要信息。
请特别关注宠物相关的信息：
1. 场景类型（如：室内/室外/宠物屋/户外活动等）
2. 宠物种类、数量及行为（如：玩耍、进食、休息、探索等）
3. 正在发生的有趣活动或互动
4. 整体氛围或情绪（如：活泼、温馨、放松、兴奋等）
5. 是否有主人或其他动物参与互动
6. 视频中的关键变化或转折点

请用150字以内的中文回答。"""

    AUDIO_PROMPT = """请分析这段宠物视频的音频部分，用简洁的中文描述音频内容。
请特别关注宠物相关的信息：
1. 主要声音类型（如：宠物叫声/人声/环境音/玩具声等）
2. 宠物叫声的情绪（如：兴奋/焦虑/愉悦/求关注等）
3. 人宠互动的声音（如：抚摸声/喂食声/呼唤声等）
4. 整体音频氛围（如：活跃/安静/温馨等）
5. 音频与视频内容的配合情况

请用120字以内的中文回答。"""

    COMBINE_PROMPT = """请将以下宠物视频的视觉和音频描述整合成一个统一的语义摘要。

背景：由于视频分析模型不可用，我们分别获得了视觉和音频的独立分析结果，现在需要将它们整合成一个连贯的描述，以保持与直接视频分析方案的一致性。

视觉描述：{visual}
音频描述：{audio}

要求：
1. 整合视觉和音频的关键信息，突出宠物行为和互动亮点
2. 突出有趣的活动或关键时刻
3. 保留场景氛围和情绪描述
4. 重点标识高光时刻（如：有趣互动、特殊行为、情感表达等）
5. 评估视频的精彩程度和观赏价值
6. 输出连贯、流畅的180字以内中文摘要，适合用于高光片段识别

请直接输出整合后的摘要内容："""

    KEYWORDS_PROMPT = """请从以下宠物视频片段描述中提取5-7个关键词，用于后续检索和分类。

描述：{summary}

要求：
1. 关键词应涵盖宠物种类、行为、场景、情绪等维度
2. 优先提取最具代表性的宠物相关词汇
3. 包含高光时刻的特征词汇（如：互动、玩耍、兴奋、温馨等）
4. 体现视频精彩程度的词汇
5. 输出JSON格式：{"keywords": ["关键词1", "关键词2", ...]}"""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
    def _extract_frame(self, video_path: Path, output_path: Path, timestamp: float = 0.5) -> bool:
        """Extract a frame from video at given timestamp"""
        try:
            ffmpeg_path = Path(__file__).parent.parent / "ffmpeg" / "windows" / "bin" / "ffmpeg.exe"
            if not ffmpeg_path.exists():
                ffmpeg_path = "ffmpeg"  # fallback to system ffmpeg
            
            cmd = [
                str(ffmpeg_path), '-y',
                '-ss', str(timestamp),
                '-i', str(video_path),
                '-vframes', '1',
                '-q:v', '2',
                str(output_path)
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            return result.returncode == 0
        except Exception as e:
            logger.error(f"Frame extraction error: {e}")
            return False
    
    def _extract_audio(self, video_path: Path, output_path: Path) -> bool:
        """Extract audio from video"""
        try:
            ffmpeg_path = Path(__file__).parent.parent / "ffmpeg" / "windows" / "bin" / "ffmpeg.exe"
            if not ffmpeg_path.exists():
                ffmpeg_path = "ffmpeg"  # fallback to system ffmpeg
            
            cmd = [
                str(ffmpeg_path), '-y',
                '-i', str(video_path),
                '-vn',
                '-acodec', 'libmp3lame',
                '-q:a', '2',
                str(output_path)
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=60)
            return result.returncode == 0
        except Exception as e:
            logger.error(f"Audio extraction error: {e}")
            return False
    
    async def _process_segment(self, segment_path: Path, segment_id: int, duration: float) -> SegmentSummary:
        """Process a single segment and generate summary"""
        start_time = datetime.now()
        summary = SegmentSummary(
            segment_id=segment_id,
            segment_path=str(segment_path),
            duration=duration
        )
        
        try:
            # Method 1: Try video model for direct video analysis (best for temporal content)
            video_summary = await self._get_video_summary(segment_path)
            
            if video_summary:
                summary.combined_summary = video_summary
                keywords = await self._extract_keywords(video_summary)
                summary.keywords = keywords
            else:
                # Method 2: Use visual and audio models in parallel (for models that don't support video)
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_path = Path(temp_dir)
                    frame_path = temp_path / "frame.jpg"
                    audio_path = temp_path / "audio.mp3"
                    
                    # Extract audio if needed
                    self._extract_audio(segment_path, audio_path)
                    
                    # Process visual and audio in parallel using the video file directly for both
                    visual_task = self._get_visual_summary_from_video(segment_path)  # Use full video for visual analysis
                    audio_task = self._get_audio_summary(audio_path)  # Use extracted audio
                    
                    results = await asyncio.gather(visual_task, audio_task, return_exceptions=True)
                    
                    visual_summary = results[0] if not isinstance(results[0], Exception) else ""
                    audio_summary = results[1] if not isinstance(results[1], Exception) else ""
                    
                    summary.visual_summary = visual_summary
                    summary.audio_summary = audio_summary
                    
                    if visual_summary or audio_summary:
                        combined = await self._combine_summaries(visual_summary, audio_summary)
                        summary.combined_summary = combined
                        
                        keywords = await self._extract_keywords(combined)
                        summary.keywords = keywords
            
            # Generate multi-dimensional quality scores using LLM based on summary and keywords
            if summary.combined_summary:
                quality_scores = await self._get_quality_score(summary.combined_summary, summary.keywords, duration)
                summary.quality_scores = quality_scores
                
        except Exception as e:
            summary.error = str(e)
            logger.error(f"Error processing segment {segment_id}: {e}")
        
        summary.processing_time = (datetime.now() - start_time).total_seconds()
        return summary
    
    async def _get_video_summary(self, video_path: Path) -> str:
        """Get video summary from video model (temporal analysis)"""
        video_prompt = """请详细分析这段宠物视频的内容，包括：
1. 视频的整体主题和主要内容（如：宠物活动、互动等）
2. 重要的视觉元素和场景变化（如：地点转换、宠物行为变化等）
3. 音频内容（宠物叫声、人声互动、环境音等）
4. 视频的情感氛围和节奏（如：活泼、温馨、平静等）
5. 关键时刻或转折点（如：有趣的互动、特殊行为等）
6. 宠物的行为模式和情绪表现

请用150字以内的中文概括以上要点，突出宠物相关的重要信息。"""
        
        request = VideoRequest(
            video_path=str(video_path),
            prompt=video_prompt,
            max_tokens=300,
            temperature=0.3
        )
        response = await aliyun_client.call_video_model(request)
        if response.success and response.model_used:
            logger.debug(f"Video summary used model: {response.model_used}")
        return response.content if response.success else ""

    async def _get_quality_score(self, summary: str, keywords: List[str], duration: float) -> Dict[str, float]:
        """Get multi-dimensional quality scores from LLM based on summary, keywords and duration"""
        score_prompt = f"""请评估以下视频片段的多维度质量得分（0-1之间的数值）。

视频摘要：{summary}

关键词：{', '.join(keywords)}

视频时长：{duration:.2f}秒

评估维度：
1. content_richness: 内容丰富度（0-1分）- 内容是否丰富有趣
2. pet_behavior: 宠物行为（0-1分）- 宠物行为是否有趣或有意义
3. interaction_level: 互动程度（0-1分）- 人宠互动或宠物间互动情况
4. emotional_value: 情绪价值（0-1分）- 是否具有情感共鸣或温馨感
5. uniqueness: 独特性（0-1分）- 内容是否独特或罕见
6. engagement_potential: 吸引力（0-1分）- 是否能吸引观众注意力
7. entertainment_value: 娱乐性（0-1分）- 是否有趣或令人愉快

请按以下JSON格式返回得分：
{{
  "scores": {{
    "content_richness": 0.XX,
    "pet_behavior": 0.XX,
    "interaction_level": 0.XX,
    "emotional_value": 0.XX,
    "uniqueness": 0.XX,
    "engagement_potential": 0.XX,
    "entertainment_value": 0.XX
  }},
  "overall_score": 0.XX,
  "reason": "简要说明评分理由"
}}

注意：所有得分必须在0-1之间，1分为最高分。"""
        
        request = TextRequest(
            system_prompt="你是一个视频内容质量评估专家，擅长识别高质量的宠物视频片段，能够进行多维度评估。",
            user_prompt=score_prompt,
            max_tokens=200,
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        response = await aliyun_client.call_fast_model(request)
        
        if response.success:
            try:
                result = json.loads(response.content)
                scores = result.get("scores", {})
                overall_score = result.get("overall_score", 0.5)
                
                # Ensure all scores are between 0 and 1
                for key in scores:
                    scores[key] = max(0.0, min(1.0, float(scores[key])))
                
                scores['overall'] = max(0.0, min(1.0, float(overall_score)))
                
                return scores
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse quality score JSON: {response.content}")
                # Return default scores
                return {
                    "content_richness": 0.5,
                    "pet_behavior": 0.5,
                    "interaction_level": 0.5,
                    "emotional_value": 0.5,
                    "uniqueness": 0.5,
                    "engagement_potential": 0.5,
                    "entertainment_value": 0.5,
                    "overall": 0.5
                }
        else:
            logger.warning(f"Quality scoring failed: {response.error}")
            # Return default scores
            return {
                "content_richness": 0.5,
                "pet_behavior": 0.5,
                "interaction_level": 0.5,
                "emotional_value": 0.5,
                "uniqueness": 0.5,
                "engagement_potential": 0.5,
                "entertainment_value": 0.5,
                "overall": 0.5
            }

    async def _get_visual_summary_from_video(self, video_path: Path) -> str:
        """Get visual summary from VL model using full video"""
        video_visual_prompt = """请分析这段宠物视频内容，用简洁的中文描述视频中的主要信息。
请特别关注宠物相关的信息：
1. 场景类型（如：室内/室外/宠物屋/户外活动等）
2. 宠物种类、数量及行为（如：玩耍、进食、休息、探索等）
3. 正在发生的有趣活动或互动
4. 整体氛围或情绪（如：活泼、温馨、放松、兴奋等）
5. 是否有主人或其他动物参与互动
6. 视频中的关键变化或转折点

请用150字以内的中文回答，突出宠物相关的重要信息。"""
        
        request = VLRequest(
            video_path=str(video_path),
            prompt=video_visual_prompt,
            max_tokens=200,
            temperature=0.3
        )
        response = await aliyun_client.call_vl_model(request)
        if response.success and response.model_used:
            logger.debug(f"Visual summary used model: {response.model_used}")
        return response.content if response.success else ""

    async def _get_visual_summary(self, frame_path: Path) -> str:
        """Get visual summary from VL model"""
        request = VLRequest(
            image_path=str(frame_path),
            prompt=self.VISUAL_PROMPT,
            max_tokens=200,
            temperature=0.3
        )
        response = await aliyun_client.call_vl_model(request)
        if response.success and response.model_used:
            logger.debug(f"Visual summary used model: {response.model_used}")
        return response.content if response.success else ""
    
    async def _get_audio_summary(self, audio_path: Path) -> str:
        """Get audio summary from Audio model"""
        request = AudioRequest(
            audio_path=str(audio_path),
            prompt=self.AUDIO_PROMPT,
            max_tokens=200,
            temperature=0.3
        )
        response = await aliyun_client.call_audio_model(request)
        if response.success and response.model_used:
            logger.debug(f"Audio summary used model: {response.model_used}")
        return response.content if response.success else ""
    
    async def _combine_summaries(self, visual: str, audio: str) -> str:
        """Combine visual and audio summaries"""
        request = TextRequest(
            system_prompt="你是一个视频内容分析专家，擅长整合多模态信息。",
            user_prompt=self.COMBINE_PROMPT.format(visual=visual, audio=audio),
            max_tokens=300,
            temperature=0.3
        )
        response = await aliyun_client.call_fast_model(request)
        if response.success and response.model_used:
            logger.debug(f"Combine summaries used model: {response.model_used}")
        return response.content if response.success else f"{visual} {audio}"
    
    async def _extract_keywords(self, summary: str) -> List[str]:
        """Extract keywords from summary"""
        request = TextRequest(
            system_prompt="你是一个关键词提取专家。",
            user_prompt=self.KEYWORDS_PROMPT.format(summary=summary),
            max_tokens=100,
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        response = await aliyun_client.call_fast_model(request)
        
        if response.success:
            if response.model_used:
                logger.debug(f"Extract keywords used model: {response.model_used}")
            try:
                result = json.loads(response.content)
                return result.get("keywords", [])
            except json.JSONDecodeError:
                return []
        return []
    
    async def process_segments(
        self, 
        segment_paths: List[Path], 
        durations: List[float]
    ) -> List[SegmentSummary]:
        """Process multiple segments with small batch processing"""
        
        total = min(len(segment_paths), MAX_SEGMENTS_TO_PROCESS)
        logger.info(f"Processing {total} segments for semantic summarization...")
        
        results = []
        
        # Process in small batches of 2-3 segments to reduce risk
        batch_size = 3
        for batch_start in range(0, total, batch_size):
            batch_end = min(batch_start + batch_size, total)
            batch_paths = segment_paths[batch_start:batch_end]
            batch_durations = durations[batch_start:batch_end]
            
            logger.info(f"Processing batch {batch_start//batch_size + 1}: segments {batch_start}-{batch_end-1}")
            
            # Create tasks for current batch
            batch_tasks = []
            for i, (path, duration) in enumerate(zip(batch_paths, batch_durations)):
                segment_id = batch_start + i
                batch_tasks.append(self._process_segment(path, segment_id, duration))
            
            # Process batch
            batch_summaries = await asyncio.gather(*batch_tasks, return_exceptions=True)
            
            # Add results to main results list
            for i, summary in enumerate(batch_summaries):
                global_idx = batch_start + i
                if isinstance(summary, Exception):
                    results.append(SegmentSummary(
                        segment_id=global_idx,
                        segment_path=str(segment_paths[global_idx]) if global_idx < len(segment_paths) else "",
                        duration=durations[global_idx] if global_idx < len(durations) else 0,
                        error=str(summary)
                    ))
                else:
                    results.append(summary)
            
            logger.info(f"Completed batch {batch_start//batch_size + 1}, processed {len(batch_summaries)} segments")
        
        logger.info(f"Completed processing {len(results)} segments")
        return results
    
    def save_summaries(self, summaries: List[SegmentSummary], output_path: Path) -> None:
        """Save summaries to JSON file"""
        data = {
            "total_segments": len(summaries),
            "processing_timestamp": datetime.now().isoformat(),
            "summaries": [asdict(s) for s in summaries]
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Summaries saved to: {output_path}")
    
    def load_summaries(self, input_path: Path) -> List[SegmentSummary]:
        """Load summaries from JSON file"""
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        summaries = []
        for s in data.get("summaries", []):
            summaries.append(SegmentSummary(**s))
        
        return summaries
