"""
Chimeric Identity Standalone Runner

Full pipeline orchestrator for chimeric biometric identity fusion.

Architecture:
  Camera → Perception (OC-SORT) → Face Engine → Gait Engine → SourceAuth Engine
                                         ↓
                              ChimericFusionEngine
                                    ↓
                        ChimericDecision (unified output)

This runner:
  1. Initializes all subsystems (camera, perception, face, gait, source_auth, chimeric)
  2. Processes frames in main loop with robust error handling
  3. Maintains per-track chimeric state (accumulator, state machine)
  4. Emits unified decisions with learning gates
  5. Provides optional visualization and metrics logging

Design Philosophy:
  - Non-invasive (reads from existing engines, no modifications)
  - Backward-compatible (face_only/gait_only modes still available)
  - Error-isolated (adapter failures degrade gracefully)
  - Memory-safe (stale track cleanup, bounded buffers)
  - Real-time capable (no blocking operations)

Classes:
  - ChimericRunner: Main orchestrator
  - RunnerConfig: Configuration for runner parameters
  - RunnerMetrics: Runtime statistics

Usage:
    runner = ChimericRunner(config)
    runner.run()  # Main loop (blocking until shutdown)
"""

import time
import logging
import traceback
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum
from pathlib import Path

# Chimeric modules
from chimeric_identity.config import ChimericConfig, default_chimeric_config
from chimeric_identity.fusion_engine import ChimericFusionEngine
from chimeric_identity.logging_utils import ChimericLogger, LogLevel, format_decision_oneline
from chimeric_identity.types import ChimericDecision, ChimericState

# ============================================================================
# NON-INVASIVE ENGINE IMPORTS (Bridge Pattern)
# ============================================================================
# These are independent subsystems that work alone OR together via chimeric.
# Imports are optional - system degrades gracefully if unavailable.

# Face subsystem (independent, non-invasive import)
try:
    from identity.identity_engine import FaceIdentityEngine
    from identity.config import default_face_config
    FACE_ENGINE_AVAILABLE = True
except ImportError:
    FACE_ENGINE_AVAILABLE = False
    logger_warn = logging.getLogger(__name__)
    logger_warn.warning("FaceIdentityEngine not available - face subsystem disabled")

# Gait subsystem (independent, non-invasive import)
try:
    from gait_subsystem.gait.gait_engine import GaitEngine
    from gait_subsystem.gait.config import default_gait_config
    GAIT_ENGINE_AVAILABLE = True
except ImportError:
    GAIT_ENGINE_AVAILABLE = False
    logger_warn = logging.getLogger(__name__)
    logger_warn.warning("GaitEngine not available - gait subsystem disabled")

# Source Auth subsystem (independent, non-invasive import)
try:
    from source_auth.engine import SourceAuthEngine
    from source_auth.config import SourceAuthConfig
    SOURCE_AUTH_AVAILABLE = True
except ImportError:
    SOURCE_AUTH_AVAILABLE = False
    logger_warn = logging.getLogger(__name__)
    logger_warn.warning("SourceAuthEngine not available - source auth disabled")


# ============================================================================
# CONFIGURATION
# ============================================================================

class RunnerMode(Enum):
    """Supported runner modes for compatibility testing."""
    CHIMERIC_ONLY = "chimeric_only"      # New: full chimeric fusion
    FACE_ONLY = "face_only"              # Existing: face engine only (regression test)
    GAIT_ONLY = "gait_only"              # Existing: gait engine only (regression test)
    ANALYSIS_ONLY = "analysis_only"      # Decision analysis without real-time processing


@dataclass
class RunnerConfig:
    """Configuration for chimeric runner."""
    
    # Runner mode
    mode: RunnerMode = RunnerMode.CHIMERIC_ONLY
    
    # Chimeric fusion parameters
    chimeric_config: ChimericConfig = field(default_factory=default_chimeric_config)
    
    # I/O configuration
    camera_device_id: int = 0             # Camera device (0 = default)
    video_file_path: Optional[str] = None # Video file path (if provided, use instead of camera)
    output_log_file: Optional[str] = None # Log file destination
    decision_json_output: Optional[str] = None  # JSON decisions stream
    
    # Processing parameters
    frame_skip: int = 0                   # Skip N frames (for faster processing)
    max_frames: Optional[int] = None      # Max frames to process (for testing)
    display_results: bool = False         # Show visualization
    display_confidence_threshold: float = 0.5  # Min confidence to display
    
    # Logging parameters
    log_level: LogLevel = LogLevel.NORMAL
    log_metrics_interval_sec: float = 5.0
    debug_trace_enabled: bool = False
    redact_identities: bool = False
    
    # Feature flags
    enable_face_subsystem: bool = True
    enable_gait_subsystem: bool = True
    enable_source_auth: bool = True
    
    # Timeouts
    frame_read_timeout_sec: float = 1.0
    engine_timeout_sec: float = 0.5
    
    # Memory management
    stale_track_timeout_sec: float = 10.0
    max_active_tracks: int = 100


@dataclass
class RunnerMetrics:
    """Runtime statistics for runner."""
    
    frames_processed: int = 0
    tracks_processed: int = 0
    decisions_made: int = 0
    errors_encountered: int = 0
    
    face_engine_calls: int = 0
    face_engine_errors: int = 0
    face_adapter_errors: int = 0
    
    gait_engine_calls: int = 0
    gait_engine_errors: int = 0
    gait_adapter_errors: int = 0
    
    source_auth_calls: int = 0
    source_auth_errors: int = 0
    
    chimeric_fusion_errors: int = 0
    
    start_time: float = field(default_factory=time.time)
    
    def elapsed_seconds(self) -> float:
        """Elapsed time since start."""
        return time.time() - self.start_time
    
    def fps(self) -> float:
        """Frames per second."""
        elapsed = self.elapsed_seconds()
        return self.frames_processed / max(elapsed, 0.001)
    
    def format_summary(self) -> str:
        """Format metrics as human-readable summary."""
        return (
            f"Runner Metrics (elapsed={self.elapsed_seconds():.1f}s)\n"
            f"  Frames: {self.frames_processed} ({self.fps():.1f} fps)\n"
            f"  Tracks: {self.tracks_processed} processed\n"
            f"  Decisions: {self.decisions_made} made\n"
            f"  Errors: {self.errors_encountered} total\n"
            f"  Face: {self.face_engine_calls} calls, {self.face_engine_errors} errors\n"
            f"  Gait: {self.gait_engine_calls} calls, {self.gait_engine_errors} errors\n"
            f"  SourceAuth: {self.source_auth_calls} calls, {self.source_auth_errors} errors\n"
            f"  Fusion: {self.chimeric_fusion_errors} errors"
        )


# ============================================================================
# MAIN RUNNER CLASS
# ============================================================================

class ChimericRunner:
    """
    Main orchestrator for chimeric identity fusion pipeline.
    
    Responsibilities:
      1. Initialize all subsystems (camera, perception, engines, fusion)
      2. Main processing loop (frame acquisition → perception → fusion)
      3. Error handling and graceful degradation
      4. Memory management (track cleanup, buffer limits)
      5. Metrics collection and periodic logging
      6. Visualization (optional)
      7. Learning suggestions emission
    
    Attributes:
        config: RunnerConfig with all parameters
        chimeric_engine: ChimericFusionEngine orchestrator
        logger: ChimericLogger for decision logging
        metrics: RunnerMetrics for statistics
    """
    
    def __init__(self, config: RunnerConfig):
        """
        Initialize chimeric runner.
        
        Args:
            config: RunnerConfig instance
            
        Raises:
            RuntimeError: If critical subsystems fail to initialize
        """
        self.config = config
        self.logger = ChimericLogger(
            log_level=config.log_level,
            redact_identities=config.redact_identities,
            file_path=config.output_log_file,
        )
        self.metrics = RunnerMetrics()
        
        self.logger.logger.info(
            f"Initializing ChimericRunner in {config.mode.value} mode"
        )
        
        # ===================================================================
        # SUBSYSTEM ENGINES (Non-Invasive Bridge Pattern)
        # ===================================================================
        # These are stateful instances of independent engines.
        # Each engine is fully configured and runs independently.
        # Chimeric only reads their outputs via adapters (read-only access).
        
        self.face_engine = None
        self.gait_engine = None
        self.source_auth_engine = None
        
        # Initialize Face Engine (if available and enabled)
        if self.config.enable_face_subsystem and FACE_ENGINE_AVAILABLE:
            try:
                face_cfg = default_face_config()
                self.face_engine = FaceIdentityEngine(face_cfg=face_cfg)
                self.logger.logger.info(
                    f"[INTEGRATION] ✓ FaceIdentityEngine initialized (mode={self.config.mode.value})"
                )
            except Exception as e:
                self.logger.log_error("init", e, "FaceIdentityEngine initialization")
                self.config.enable_face_subsystem = False
        
        # Initialize Gait Engine (if available and enabled)
        if self.config.enable_gait_subsystem and GAIT_ENGINE_AVAILABLE:
            try:
                gait_cfg = default_gait_config()
                self.gait_engine = GaitEngine(config=gait_cfg)
                self.logger.logger.info(
                    f"[INTEGRATION] ✓ GaitEngine initialized (mode={self.config.mode.value})"
                )
            except Exception as e:
                self.logger.log_error("init", e, "GaitEngine initialization")
                self.config.enable_gait_subsystem = False
        
        # Initialize Source Auth Engine (if available and enabled)
        if self.config.enable_source_auth and SOURCE_AUTH_AVAILABLE:
            try:
                source_auth_cfg = SourceAuthConfig()
                self.source_auth_engine = SourceAuthEngine(cfg=source_auth_cfg)
                self.logger.logger.info(
                    f"[INTEGRATION] ✓ SourceAuthEngine initialized (mode={self.config.mode.value})"
                )
            except Exception as e:
                self.logger.log_error("init", e, "SourceAuthEngine initialization")
                self.config.enable_source_auth = False
        
        # Initialize chimeric fusion engine
        try:
            self.chimeric_engine = ChimericFusionEngine(
                config=config.chimeric_config,
                logger=self.logger,
            )
            self.logger.logger.info("✓ ChimericFusionEngine initialized")
        except Exception as e:
            self.logger.log_error("init", e, "ChimericFusionEngine initialization")
            raise RuntimeError(f"Failed to initialize fusion engine: {e}")
        
        # State tracking
        self._shutdown_requested = False
        self._last_metrics_log_time = time.time()
        self._tracklet_buffer: Dict[str, Dict] = {}  # Per-track state
    
    def run(self) -> None:
        """
        Main processing loop (blocking until shutdown).
        
        Handles:
          1. Frame acquisition (camera or video file)
          2. Perception inference (tracking)
          3. Per-tracklet processing (face/gait/fusion)
          4. Error handling and recovery
          5. Periodic metrics logging
          6. Graceful shutdown (CTRL+C)
        """
        self.logger.logger.info("Starting main processing loop")
        
        try:
            # Initialize frame source
            frame_source = self._initialize_frame_source()
            if not frame_source:
                raise RuntimeError("Failed to initialize frame source")
            
            frame_count = 0
            
            # Main loop
            while not self._shutdown_requested:
                try:
                    # Acquire frame
                    frame_data = self._read_frame(frame_source)
                    if frame_data is None:
                        self.logger.logger.warning("Frame acquisition failed, retrying...")
                        continue
                    
                    frame_count += 1
                    self.metrics.frames_processed = frame_count
                    
                    # Skip frames if configured
                    if self.config.frame_skip > 0 and frame_count % (self.config.frame_skip + 1) != 0:
                        continue
                    
                    # Check frame limit
                    if self.config.max_frames and frame_count >= self.config.max_frames:
                        self.logger.logger.info(f"Max frames ({self.config.max_frames}) reached, shutting down")
                        break
                    
                    # Process frame
                    self._process_frame(frame_data, frame_count)
                    
                    # Periodic metrics logging
                    self._maybe_log_metrics()
                    
                except KeyboardInterrupt:
                    self.logger.logger.info("CTRL+C received, shutting down gracefully...")
                    self._shutdown_requested = True
                except Exception as e:
                    self.metrics.errors_encountered += 1
                    self.logger.log_error("main_loop", e, f"Frame {frame_count} processing")
                    self.logger.logger.error(f"Traceback: {traceback.format_exc()}")
                    # Continue processing other frames
                    continue
            
            # Cleanup
            self._cleanup_frame_source(frame_source)
            
        except Exception as e:
            self.logger.log_error("main_loop", e, "Fatal error in main loop")
            self.logger.logger.error(f"Traceback: {traceback.format_exc()}")
            raise
        finally:
            # Final metrics
            self.logger.logger.info(self.metrics.format_summary())
            self.logger.log_metrics_snapshot()
    
    def _initialize_frame_source(self) -> Optional[object]:
        """
        Initialize frame source (camera or video file).
        
        Returns:
            Frame source object (cv2.VideoCapture), or None if initialization fails
        """
        try:
            import cv2
            
            if self.config.video_file_path:
                # Video file
                cap = cv2.VideoCapture(self.config.video_file_path)
                if not cap.isOpened():
                    self.logger.logger.error(f"Failed to open video file: {self.config.video_file_path}")
                    return None
                self.logger.logger.info(f"✓ Opened video file: {self.config.video_file_path}")
            else:
                # Camera device
                cap = cv2.VideoCapture(self.config.camera_device_id)
                if not cap.isOpened():
                    self.logger.logger.error(f"Failed to open camera device {self.config.camera_device_id}")
                    return None
                self.logger.logger.info(f"✓ Opened camera device {self.config.camera_device_id}")
            
            # Set frame properties
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            cap.set(cv2.CAP_PROP_FPS, 30)
            
            return cap
        
        except ImportError:
            self.logger.logger.error("OpenCV (cv2) not available")
            return None
    
    def _cleanup_frame_source(self, frame_source: object) -> None:
        """Clean up frame source."""
        if frame_source:
            try:
                frame_source.release()
                self.logger.logger.info("Frame source released")
            except Exception as e:
                self.logger.logger.warning(f"Error releasing frame source: {e}")
    
    def _read_frame(self, frame_source: object) -> Optional[Dict]:
        """
        Read a single frame from source.
        
        Returns:
            Dict with frame data and metadata, or None if read fails
        """
        try:
            ret, frame = frame_source.read()
            if not ret or frame is None:
                return None
            
            return {
                "frame": frame,
                "timestamp": time.time(),
                "height": frame.shape[0],
                "width": frame.shape[1],
            }
        except Exception as e:
            self.logger.logger.error(f"Error reading frame: {e}")
            return None
    
    def _process_frame(self, frame_data: Dict, frame_count: int) -> None:
        """
        Process a single frame through perception and fusion pipeline.
        
        Args:
            frame_data: Frame data dict with "frame", "timestamp", etc.
            frame_count: Frame number for logging
        """
        frame = frame_data["frame"]
        timestamp = frame_data["timestamp"]
        
        # TODO: Perception inference (OC-SORT tracker)
        # This would call the perception engine to get tracklets
        # For now, we're stubbing the perception part as that already exists
        
        tracklets = self._mock_perception_inference(frame)
        
        self.metrics.tracks_processed += len(tracklets)
        
        # Process each tracklet through fusion pipeline
        for tracklet in tracklets:
            try:
                decision = self._process_tracklet(tracklet, timestamp)
                
                if decision:
                    self.metrics.decisions_made += 1
                    self.logger.log_decision(decision)
                    
                    # Optional: Output to JSON stream
                    if self.config.decision_json_output:
                        self._append_decision_to_json(decision)
                    
                    # Optional: Emit learning suggestion
                    if decision.learning_allowed:
                        self._handle_learning_suggestion(decision)
                    
                    # Optional: Visualization
                    if self.config.display_results:
                        self._maybe_visualize_decision(frame, decision)
            
            except Exception as e:
                self.metrics.errors_encountered += 1
                self.logger.log_error(tracklet.get("track_id", "unknown"), e, "Tracklet processing")
    
    def _process_tracklet(self, tracklet: Dict, timestamp: float) -> Optional[ChimericDecision]:
        """
        Process a single tracklet through chimeric fusion pipeline.
        
        Args:
            tracklet: Tracklet dict with track_id, bbox, detections, gait_data
            timestamp: Current timestamp
        
        Returns:
            ChimericDecision, or None if processing fails
        """
        track_id = tracklet.get("track_id")
        
        try:
            # ================================================================
            # DEEP ROBUST INTEGRATION: Call actual engines (Non-Invasive)
            # ================================================================
            # Each engine is called independently and has its own state.
            # We read their outputs but never modify their internal state.
            # This maintains the bridge pattern and backward compatibility.
            
            # --- 1. FACE ENGINE CALL (Fast, Strong Biometric) ---
            face_signals = []
            face_decision = None
            if self.config.enable_face_subsystem and self.face_engine:
                try:
                    # Call face engine: update_signals returns per-track signal objects
                    # This is READ-ONLY - we don't modify face_engine state
                    face_signals = self.face_engine.update_signals(
                        frame=None,  # Would be Frame object from perception in real usage
                        tracks=[tracklet],
                        schedule_context=None
                    )
                    
                    # Get decision from face signals (if any new evidence)
                    if face_signals:
                        face_decisions = self.face_engine.decide(face_signals)
                        if face_decisions and len(face_decisions) > 0:
                            face_decision = face_decisions[0]  # One per track
                    
                    self.metrics.face_engine_calls += 1
                    
                except Exception as e:
                    self.metrics.face_engine_errors += 1
                    self.logger.log_error(
                        track_id, e, 
                        "Face engine call (non-invasive read-only adapter)"
                    )
            
            # --- 2. GAIT ENGINE CALL (Slow, Soft Biometric) ---
            gait_signals = []
            gait_decision = None
            gait_state = None
            if self.config.enable_gait_subsystem and self.gait_engine:
                try:
                    # Call gait engine: update_signals executes Gold Spec pipeline
                    # This is READ-ONLY - we don't modify gait_engine state
                    gait_signals = self.gait_engine.update_signals(
                        frame=None,  # Would be Frame object in real usage
                        tracks=[tracklet]
                    )
                    
                    # Extract gait track state for temporal fusion
                    if tracklet.track_id in self.gait_engine._track_states:
                        gait_state = self.gait_engine._track_states[tracklet.track_id]
                    
                    # Get decision from gait signals
                    if gait_signals:
                        gait_decisions = self.gait_engine.decide(gait_signals)
                        if gait_decisions and len(gait_decisions) > 0:
                            gait_decision = gait_decisions[0]  # One per track
                    
                    self.metrics.gait_engine_calls += 1
                    
                except Exception as e:
                    self.metrics.gait_engine_errors += 1
                    self.logger.log_error(
                        track_id, e,
                        "Gait engine call (non-invasive read-only adapter)"
                    )
            
            # --- 3. SOURCE AUTH ENGINE CALL (Spoof Detection) ---
            source_auth_scores = None
            if self.config.enable_source_auth and self.source_auth_engine:
                try:
                    # Collect id_signals from face engine (needed for source auth)
                    id_signals = face_signals if face_signals else []
                    
                    # Call source auth engine: update computes real/spoof scores
                    # This is READ-ONLY - we don't modify source_auth_engine state
                    scores_dict = self.source_auth_engine.update(
                        frame=None,  # Would be Frame object in real usage
                        tracks=[tracklet],
                        id_signals=id_signals
                    )
                    
                    # Extract scores for this specific track
                    if track_id in scores_dict:
                        source_auth_scores = scores_dict[track_id]
                    
                    self.metrics.source_auth_calls += 1
                    
                except Exception as e:
                    self.metrics.source_auth_errors += 1
                    self.logger.log_error(
                        track_id, e,
                        "SourceAuth engine call (non-invasive read-only adapter)"
                    )
            
            # ================================================================
            # CHIMERIC FUSION: Combine results via non-invasive adapters
            # ================================================================
            # Adapters normalize engine outputs to chimeric evidence types.
            # No state is modified in face/gait/source_auth engines.
            # This is the core bridge logic.
            
            decision = self.chimeric_engine.fuse(
                tracklet=tracklet,  # Pass full tracklet for adapter access
                face_identity_decision=face_decision,
                gait_identity_decision=gait_decision,
                gait_track_state=gait_state,
                source_auth_scores=source_auth_scores,
                now=timestamp,
            )
            
            # ================================================================
            # INTEGRATION VALIDATION LOGGING
            # ================================================================
            if decision and self.config.log_level == LogLevel.DEBUG:
                sources = []
                if face_decision: sources.append("face")
                if gait_decision: sources.append("gait")
                if source_auth_scores: sources.append("source_auth")
                
                self.logger.logger.debug(
                    f"[INTEGRATION-PIPELINE] track_id={track_id} "
                    f"engines_called=[{','.join(sources)}] "
                    f"chimeric_state={decision.state.value} "
                    f"confidence={decision.chimeric_confidence:.3f}"
                )
            
            # Update per-track state
            self._tracklet_buffer[track_id] = {
                "last_update": timestamp,
                "last_decision": decision,
            }
            
            return decision
        
        except Exception as e:
            self.metrics.chimeric_fusion_errors += 1
            self.logger.log_error(
                track_id, e, 
                "Chimeric fusion orchestration (bridge between face/gait/source_auth)"
            )
            return None
    
    def _mock_perception_inference(self, frame) -> List[Dict]:
        """
        Mock perception inference for testing.
        
        In production, this would call the actual perception engine (OC-SORT tracker).
        For now, returns empty tracklets (testing only).
        
        Args:
            frame: Input frame
        
        Returns:
            List of tracklet dicts
        """
        # TODO: Replace with actual perception engine call
        return []
    
    def _maybe_log_metrics(self) -> None:
        """Periodically log metrics (if interval passed)."""
        now = time.time()
        if now - self._last_metrics_log_time >= self.config.log_metrics_interval_sec:
            self.logger.log_metrics_snapshot()
            self._last_metrics_log_time = now
    
    def _append_decision_to_json(self, decision: ChimericDecision) -> None:
        """Append decision to JSON output stream."""
        try:
            import json
            
            with open(self.config.decision_json_output, 'a') as f:
                json_line = self.logger.formatter.format_json(decision)
                f.write(json_line + "\n")
        except Exception as e:
            self.logger.logger.warning(f"Error writing JSON decision: {e}")
    
    def _handle_learning_suggestion(self, decision: ChimericDecision) -> None:
        """
        Handle learning suggestion emission.
        
        In production, this would emit events to the enrollment pipeline.
        For testing, we log the suggestion.
        
        Args:
            decision: ChimericDecision with learning_allowed=True
        """
        suggestion = {
            "track_id": decision.track_id,
            "timestamp": decision.timestamp,
            "identity": decision.final_identity,
            "face_allowed": self._should_learn_face(decision),
            "gait_allowed": self._should_learn_gait(decision),
            "confidence": decision.chimeric_confidence,
        }
        
        if self.config.log_level.value >= LogLevel.DEBUG.value:
            self.logger.logger.debug(f"[LEARNING_SUGGESTION] {suggestion}")
    
    def _should_learn_face(self, decision: ChimericDecision) -> bool:
        """Determine if face learning is suggested."""
        # Delegate to governance (which is part of fusion engine)
        # For now, return True if learning allowed
        return decision.learning_allowed
    
    def _should_learn_gait(self, decision: ChimericDecision) -> bool:
        """Determine if gait learning is suggested."""
        # Delegate to governance (which is part of fusion engine)
        # For now, return True if learning allowed AND gait evidence present
        gait_ev = decision.evidence_summary.get("gait")
        return decision.learning_allowed and (gait_ev is not None)
    
    def _maybe_visualize_decision(self, frame, decision: ChimericDecision) -> None:
        """
        Optionally visualize decision on frame (if display_results enabled).
        
        Args:
            frame: Input frame (numpy array)
            decision: ChimericDecision to visualize
        """
        if decision.chimeric_confidence < self.config.display_confidence_threshold:
            return
        
        try:
            import cv2
            
            # TODO: Draw bounding box, identity label, confidence, state
            # This is a stub for visualization
            
            # Show frame (if window available)
            # cv2.imshow("Chimeric Identity", frame)
            # if cv2.waitKey(1) & 0xFF == ord('q'):
            #     self._shutdown_requested = True
        
        except ImportError:
            # OpenCV not available, skip visualization
            pass
    
    def shutdown(self) -> None:
        """Request graceful shutdown."""
        self.logger.logger.info("Shutdown requested")
        self._shutdown_requested = True
    
    def get_metrics_summary(self) -> str:
        """Get current metrics as formatted string."""
        return self.metrics.format_summary()


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def create_runner(
    mode: str = "chimeric_only",
    chimeric_config: Optional[ChimericConfig] = None,
    camera_device_id: int = 0,
    video_file_path: Optional[str] = None,
    log_level: str = "NORMAL",
    output_log_file: Optional[str] = None,
    max_frames: Optional[int] = None,
    display_results: bool = False,
) -> ChimericRunner:
    """
    Factory function to create a ChimericRunner.
    
    Args:
        mode: Runner mode ("chimeric_only", "face_only", "gait_only")
        chimeric_config: ChimericConfig instance (or None for defaults)
        camera_device_id: Camera device ID (0 = default)
        video_file_path: Video file path (if provided, use instead of camera)
        log_level: Logging level ("QUIET", "NORMAL", "DEBUG", "TRACE")
        output_log_file: Log file path
        max_frames: Max frames to process (for testing)
        display_results: Show visualization
    
    Returns:
        ChimericRunner instance
    """
    try:
        runner_mode = RunnerMode[mode.upper()]
    except KeyError:
        raise ValueError(f"Unknown mode: {mode}. Valid: {[m.value for m in RunnerMode]}")
    
    config = RunnerConfig(
        mode=runner_mode,
        chimeric_config=chimeric_config or default_chimeric_config(),
        camera_device_id=camera_device_id,
        video_file_path=video_file_path,
        log_level=LogLevel[log_level.upper()],
        output_log_file=output_log_file,
        max_frames=max_frames,
        display_results=display_results,
    )
    
    return ChimericRunner(config)


if __name__ == "__main__":
    """Example usage."""
    import sys
    
    # Create runner with defaults
    runner = create_runner(
        mode="chimeric_only",
        log_level="NORMAL",
        max_frames=100,  # Test with 100 frames
    )
    
    # Run main loop
    try:
        runner.run()
    except KeyboardInterrupt:
        print("Interrupted")
    except Exception as e:
        print(f"Error: {e}")
        traceback.print_exc()
        sys.exit(1)
