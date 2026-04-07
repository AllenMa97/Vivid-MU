import sys
import logging
import time
import json
from pathlib import Path
from typing import Optional, List, Any
from dataclasses import asdict
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import config
from video_processor import VideoProcessor, find_videos
from coarse_filter import CoarseFilter, Segment, CoarseFeatures
from fine_filter import FineFilter, FineFeatures, FaceFeatures, SceneFeatures, SpeechFeatures
from selector import Selector, Solution, ScoredSegment
from exporter import Exporter


def setup_logging():
    """配置日志系统，同时输出到控制台和文件"""
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"process_{timestamp}.log"
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    return log_file


log_file_path = setup_logging()
logger = logging.getLogger(__name__)


def save_coarse_results(segments: List[Segment], output_path: Path) -> None:
    """保存粗过滤结果到JSON文件"""
    results = []
    for i, seg in enumerate(segments):
        seg_data = {
            "segment_id": i,
            "start_time": round(seg.start_time, 2),
            "end_time": round(seg.end_time, 2),
            "duration": round(seg.duration, 2),
            "avg_score": round(seg.avg_score, 4),
            "features_count": len(seg.features) if seg.features else 0
        }
        results.append(seg_data)
    
    output_data = {
        "stage": "coarse_filter",
        "total_segments": len(segments),
        "total_duration": round(sum(seg.duration for seg in segments), 2),
        "segments": results
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    logger.info(f"Coarse filter results saved: {output_path}")


def save_fine_results(features: List[FineFeatures], output_path: Path) -> None:
    """保存细过滤结果到JSON文件"""
    results = []
    for feat in features:
        feat_data = {
            "segment_id": feat.segment_id,
            "start_time": round(feat.start_time, 2),
            "end_time": round(feat.end_time, 2),
            "duration": round(feat.duration, 2),
            "coarse_score": round(feat.coarse_score, 4),
            "stability_score": round(feat.stability_score, 4),
            "audio_onset_count": feat.audio_onset_count
        }
        
        if feat.face_features:
            feat_data["face_features"] = {
                "face_count": round(feat.face_features.face_count, 2),
                "avg_face_size": round(feat.face_features.avg_face_size, 4),
                "max_face_size": round(feat.face_features.max_face_size, 4),
                "has_large_face": feat.face_features.has_large_face,
                "avg_center_distance": round(feat.face_features.avg_center_distance, 4)
            }
        
        if feat.scene_features:
            feat_data["scene_features"] = {
                "dominant_scene": feat.scene_features.dominant_scene,
                "scene_diversity": round(feat.scene_features.scene_diversity, 4),
                "scene_scores": {k: round(v, 4) for k, v in feat.scene_features.scene_scores.items()}
            }
        
        if feat.speech_features:
            feat_data["speech_features"] = {
                "speech_ratio": round(feat.speech_features.speech_ratio, 4),
                "speech_density": round(feat.speech_features.speech_density, 4),
                "speech_segments": feat.speech_features.speech_segments
            }
        
        results.append(feat_data)
    
    output_data = {
        "stage": "fine_filter",
        "total_segments": len(features),
        "total_duration": round(sum(f.duration for f in features), 2),
        "segments": results
    }
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    logger.info(f"Fine filter results saved: {output_path}")


def load_coarse_results(output_path: Path) -> Optional[List[Segment]]:
    """从JSON文件加载粗过滤结果"""
    if not output_path.exists():
        return None
    
    try:
        with open(output_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        segments = []
        for seg_data in data.get("segments", []):
            seg = Segment(
                start_time=seg_data["start_time"],
                end_time=seg_data["end_time"],
                duration=seg_data["duration"],
                avg_score=seg_data.get("avg_score", 0.0),
                features=[]
            )
            segments.append(seg)
        
        logger.info(f"Loaded coarse filter results from: {output_path}")
        logger.info(f"  Total segments: {len(segments)}")
        return segments
    except Exception as e:
        logger.warning(f"Failed to load coarse results: {e}")
        return None


def load_fine_results(output_path: Path) -> Optional[List[FineFeatures]]:
    """从JSON文件加载细过滤结果"""
    if not output_path.exists():
        return None
    
    try:
        with open(output_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        features = []
        for feat_data in data.get("segments", []):
            face_features = None
            if "face_features" in feat_data:
                ff = feat_data["face_features"]
                face_features = FaceFeatures(
                    face_count=ff.get("face_count", 0),
                    avg_face_size=ff.get("avg_face_size", 0),
                    max_face_size=ff.get("max_face_size", 0),
                    has_large_face=ff.get("has_large_face", False),
                    avg_center_distance=ff.get("avg_center_distance", 0)
                )
            
            scene_features = None
            if "scene_features" in feat_data:
                sf = feat_data["scene_features"]
                scene_features = SceneFeatures(
                    dominant_scene=sf.get("dominant_scene", "unknown"),
                    scene_diversity=sf.get("scene_diversity", 0),
                    scene_scores=sf.get("scene_scores", {})
                )
            
            speech_features = None
            if "speech_features" in feat_data:
                sf = feat_data["speech_features"]
                speech_features = SpeechFeatures(
                    speech_ratio=sf.get("speech_ratio", 0),
                    speech_density=sf.get("speech_density", 0),
                    speech_segments=sf.get("speech_segments", [])
                )
            
            feat = FineFeatures(
                segment_id=feat_data["segment_id"],
                start_time=feat_data["start_time"],
                end_time=feat_data["end_time"],
                duration=feat_data["duration"],
                face_features=face_features,
                scene_features=scene_features,
                speech_features=speech_features,
                coarse_score=feat_data.get("coarse_score", 0),
                stability_score=feat_data.get("stability_score", 0),
                audio_onset_count=feat_data.get("audio_onset_count", 0)
            )
            features.append(feat)
        
        logger.info(f"Loaded fine filter results from: {output_path}")
        logger.info(f"  Total segments: {len(features)}")
        return features
    except Exception as e:
        logger.warning(f"Failed to load fine results: {e}")
        return None


def load_existing_solutions(output_dir: Path) -> List[Solution]:
    """从现有solution目录加载已处理的结果"""
    solutions = []
    solution_dirs = sorted(output_dir.glob("solution_*"))
    
    for sol_dir in solution_dirs:
        segments_file = sol_dir / "segments.json"
        video_file = sol_dir / "highlights.mp4"
        
        if segments_file.exists() and not video_file.exists():
            try:
                with open(segments_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                segments = []
                for seg_data in data.get("segments", []):
                    seg = ScoredSegment(
                        segment_id=seg_data["segment_id"],
                        start_time=seg_data["start_time"],
                        end_time=seg_data["end_time"],
                        duration=seg_data["duration"],
                        score=seg_data["score"],
                        features=seg_data.get("features", {})
                    )
                    segments.append(seg)
                
                solution = Solution(
                    strategy_name=data.get("strategy_name", "unknown"),
                    strategy_description=data.get("strategy_description", ""),
                    segments=segments,
                    total_duration=data.get("total_duration", 0),
                    avg_score=data.get("avg_score", 0)
                )
                solutions.append(solution)
                logger.info(f"Loaded existing solution from: {sol_dir}")
                logger.info(f"  Strategy: {solution.strategy_name}, Segments: {len(segments)}")
            except Exception as e:
                logger.warning(f"Failed to load solution from {sol_dir}: {e}")
    
    return solutions


def check_all_videos_exist(output_dir: Path) -> bool:
    """检查所有solution目录是否都有视频文件"""
    solution_dirs = list(output_dir.glob("solution_*"))
    if not solution_dirs:
        return False
    
    for sol_dir in solution_dirs:
        if not (sol_dir / "highlights.mp4").exists():
            return False
    return True


def count_existing_solutions(output_dir: Path) -> int:
    """统计现有solution目录数量"""
    return len(list(output_dir.glob("solution_*")))


def process_video(video_path: Path, output_base_dir: Path) -> bool:
    video_name = video_path.stem
    output_dir = output_base_dir / f"{video_name}_related"
    
    logger.info(f"Processing video: {video_path}")
    logger.info(f"Output directory: {output_dir}")
    
    start_time = time.time()
    
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        
        if check_all_videos_exist(output_dir):
            logger.info("=" * 60)
            logger.info("All video outputs exist, skipping processing")
            logger.info("=" * 60)
            return True
        
        existing_solutions = load_existing_solutions(output_dir)
        existing_solution_count = count_existing_solutions(output_dir)
        max_solutions = config.selector.max_solutions
        
        if existing_solutions:
            logger.info("=" * 60)
            logger.info(f"Found {len(existing_solutions)} existing solutions without videos")
            logger.info("Exporting videos from existing results...")
            logger.info("=" * 60)
            
            exporter = Exporter(output_dir, video_name)
            for i, solution in enumerate(existing_solutions, 1):
                exporter.export_solution(solution, video_path, i)
            
            if existing_solution_count < max_solutions:
                logger.warning("=" * 60)
                logger.warning(f"Note: Only {existing_solution_count} solutions found, "
                             f"but max_solutions is set to {max_solutions}")
                logger.warning("To generate more solutions, delete existing solution_* folders")
                logger.warning("and re-run to regenerate from scratch.")
                logger.warning("=" * 60)
            
            elapsed_time = time.time() - start_time
            logger.info("=" * 60)
            logger.info("Video export complete")
            logger.info("=" * 60)
            logger.info(f"Total time: {elapsed_time:.1f}s")
            return True
        
        coarse_result_path = output_dir / f"{video_name}_coarse_filter.json"
        fine_result_path = output_dir / f"{video_name}_fine_filter.json"
        
        has_coarse = coarse_result_path.exists()
        has_fine = fine_result_path.exists()
        
        logger.info("=" * 60)
        logger.info("Stage 1: Video Info")
        logger.info("=" * 60)
        
        video_processor = VideoProcessor(video_path)
        info = video_processor.get_info()
        
        original_video_path = video_path
        if info.original_path:
            original_video_path = info.original_path
        
        logger.info(f"Duration: {info.duration:.1f}s ({info.duration/60:.1f}min)")
        logger.info(f"FPS: {info.fps:.2f}")
        logger.info(f"Resolution: {info.width}x{info.height}")
        logger.info(f"Total frames: {info.frame_count}")
        logger.info(f"Audio: {'Yes' if info.has_audio else 'No'}")
        
        target_duration = config.selector.target_duration
        if info.duration < target_duration:
            logger.warning("=" * 60)
            logger.warning("WARNING: Video duration is less than target!")
            logger.warning(f"  Video duration: {info.duration/60:.1f} min")
            logger.warning(f"  Target duration: {target_duration/60:.1f} min")
            logger.warning("Please adjust target_duration_minutes in user_config.txt")
            logger.warning("=" * 60)
        
        coarse_segments = None
        fine_features = None
        
        if has_fine:
            logger.info("=" * 60)
            logger.info("Stage 2-3: Loading existing filter results")
            logger.info("=" * 60)
            
            fine_features = load_fine_results(fine_result_path)
            if fine_features:
                coarse_segments = load_coarse_results(coarse_result_path)
        
        if fine_features is None:
            audio_path = None
            if info.has_audio:
                logger.info("Extracting audio...")
                audio_path = video_processor.extract_audio()
                if audio_path:
                    logger.info(f"Audio extracted: {audio_path}")
                else:
                    logger.warning("Audio extraction failed, will skip audio features")
            
            if has_coarse:
                logger.info("=" * 60)
                logger.info("Stage 2: Loading existing coarse filter results")
                logger.info("=" * 60)
                coarse_segments = load_coarse_results(coarse_result_path)
            
            if coarse_segments is None:
                logger.info("=" * 60)
                logger.info("Stage 2: Coarse Filter")
                logger.info("=" * 60)
                
                coarse_filter = CoarseFilter(config.coarse_filter)
                coarse_segments = coarse_filter.process(video_processor, audio_path)
                
                total_coarse_duration = sum(seg.duration for seg in coarse_segments)
                logger.info(f"Coarse filter done: {len(coarse_segments)} segments")
                logger.info(f"Total duration: {total_coarse_duration:.1f}s ({total_coarse_duration/60:.1f}min)")
                
                save_coarse_results(coarse_segments, coarse_result_path)
            
            if not coarse_segments:
                logger.warning("No valid segments found, processing ended")
                return False
            
            logger.info("=" * 60)
            logger.info("Stage 3: Fine Filter")
            logger.info("=" * 60)
            
            fine_filter = FineFilter(config.fine_filter)
            fine_features = fine_filter.process_segments(coarse_segments, video_processor, audio_path)
            
            logger.info(f"Fine filter done: {len(fine_features)} segments processed")
            
            save_fine_results(fine_features, fine_result_path)
            
            if audio_path and audio_path.exists():
                audio_path.unlink()
                logger.info(f"Temp audio file deleted: {audio_path}")
        
        if not fine_features:
            logger.warning("No fine features available, processing ended")
            return False
        
        logger.info("=" * 60)
        logger.info("Stage 4: Segment Selection")
        logger.info("=" * 60)
        
        selector = Selector(config.selector, config.strategies)
        solutions = selector.process(fine_features)
        
        logger.info(f"Segment selection done: {len(solutions)} solutions")
        print(selector.get_solution_summary(solutions))
        
        logger.info("=" * 60)
        logger.info("Stage 5: Export Results")
        logger.info("=" * 60)
        
        exporter = Exporter(output_dir, video_name)
        output_paths = exporter.export_all_solutions(solutions, original_video_path)
        
        logger.info(f"Export done: {len(output_paths)} solutions saved")
        
        elapsed_time = time.time() - start_time
        logger.info("=" * 60)
        logger.info("Processing Complete")
        logger.info("=" * 60)
        logger.info(f"Total time: {elapsed_time:.1f}s ({elapsed_time/60:.1f}min)")
        logger.info(f"Output directory: {output_dir}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error processing video: {e}", exc_info=True)
        return False


def main():
    logger.info("=" * 60)
    logger.info("Video Highlight Extractor")
    logger.info("=" * 60)
    logger.info(f"Log file: {log_file_path}")
    
    input_dir = Path(config.input_dir)
    output_dir = Path(config.output_dir)
    
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    videos = find_videos(input_dir, config.supported_formats)
    
    if not videos:
        logger.warning(f"No video files found in {input_dir}")
        logger.info(f"Supported formats: {config.supported_formats}")
        return
    
    logger.info(f"Found {len(videos)} video files")
    
    success_count = 0
    for video_path in videos:
        logger.info(f"\nProcessing [{success_count + 1}/{len(videos)}]: {video_path.name}")
        
        if process_video(video_path, output_dir):
            success_count += 1
    
    logger.info("=" * 60)
    logger.info(f"All done: {success_count}/{len(videos)} videos processed successfully")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
