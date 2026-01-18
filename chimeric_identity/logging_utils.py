"""
Chimeric Identity Logging Utilities

This module provides structured, auditable logging for chimeric identity decisions,
including one-line summaries, rich evidence traces, and metrics collection.

Design Philosophy:
  - One-line summaries for production logs (concise, parseable)
  - Rich traces for debugging/audit (complete evidence history)
  - Metrics collection for analysis (per 5-second window)
  - Zero performance impact (buffered, lazy formatting)
  - Privacy-aware (can redact identity names if configured)

Classes:
  - ChimericDecisionFormatter: Convert ChimericDecision → formatted output
  - MetricsCollector: Aggregate decision metrics over time windows
  - ChimericLogger: Main logging interface (wraps Python logger)

Example Usage:
    logger = ChimericLogger(config.logging)
    formatter = ChimericDecisionFormatter(redact_identities=False)
    
    decision = fusion_engine.fuse(...)
    logger.log_decision(decision, formatter)
    
    metrics = logger.get_metrics_snapshot()
    print(metrics)  # Decision rate, state dist, learning blocks, etc.
"""

import json
import logging
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from collections import defaultdict, deque
from enum import Enum

from chimeric_identity.types import (
    ChimericDecision,
    ChimericState,
    ChimericReason,
    FaceEvidence,
    GaitEvidence,
    SourceAuthEvidence,
    EvidenceStatus,
    SourceAuthState,
)


# ============================================================================
# CONSTANTS
# ============================================================================

class LogLevel(Enum):
    """Logging verbosity levels for chimeric decisions."""
    QUIET = 0       # Only final decision, no evidence
    NORMAL = 1      # Decision + one-line summary (production)
    DEBUG = 2       # Full evidence trace + reasoning
    TRACE = 3       # JSON-formatted complete state (verbose)


class DecisionSummaryTemplate:
    """Reusable templates for concise decision logging."""
    
    ONELINE = (
        "[CHIMERIC] track_id={track_id} state={state} identity={identity} "
        "confidence={confidence:.2f} "
        "face=[{face_status} {face_sim:.2f}] "
        "gait=[{gait_status} {gait_sim:.2f}] "
        "reason={reason} learning={learning}"
    )
    
    DETAILED = (
        "[CHIMERIC_DETAILED] track_id={track_id} timestamp={timestamp}\n"
        "  State: {state} → Identity: {identity} (confidence={confidence:.2f})\n"
        "  Face: status={face_status}, sim={face_sim:.2f}, quality={face_quality:.2f}, "
        "binding={face_binding}, margin={face_margin:.2f}\n"
        "  Gait: status={gait_status}, sim={gait_sim:.2f}, quality={gait_quality:.2f}, "
        "seq_len={gait_seq_len}, margin={gait_margin:.2f}, streak={gait_streak}\n"
        "  SourceAuth: state={spoof_state}, score={spoof_score:.2f}\n"
        "  Decision: {reason}\n"
        "  Learning: {learning} (reason: {learning_reason})\n"
        "  Debug Trace: {debug_trace}"
    )


# ============================================================================
# FORMATTING UTILITIES
# ============================================================================

class ChimericDecisionFormatter:
    """
    Convert ChimericDecision objects to formatted string representations.
    
    Supports multiple formats (one-line, detailed, JSON) and optional
    identity redaction for privacy.
    
    Attributes:
        redact_identities: If True, replace identity names with pseudo-IDs
        redaction_map: Mapping of original identity → pseudo-ID for consistency
    """
    
    def __init__(self, redact_identities: bool = False):
        """
        Initialize formatter.
        
        Args:
            redact_identities: If True, replace identity names with anon_001, etc.
        """
        self.redact_identities = redact_identities
        self.redaction_map: Dict[Optional[str], str] = {}
        self._next_anon_id = 0
    
    def _redact_identity(self, identity: Optional[str]) -> str:
        """Consistently redact an identity name to anonymous ID."""
        if not self.redact_identities:
            return identity if identity else "UNKNOWN"
        
        if identity is None:
            return "UNKNOWN"
        
        if identity not in self.redaction_map:
            self.redaction_map[identity] = f"anon_{self._next_anon_id:03d}"
            self._next_anon_id += 1
        
        return self.redaction_map[identity]
    
    def format_oneline(
        self,
        decision: ChimericDecision,
        include_learning_reason: bool = False,
    ) -> str:
        """
        Format decision as one-line summary (production log format).
        
        Example:
            [CHIMERIC] track_id=123 state=CONFIRMED identity=alice confidence=0.89
              face=[CONFIRMED_STRONG 0.88] gait=[TENTATIVE 0.72]
              reason=FACE_WITH_GAIT_SUPPORT learning=ALLOWED
        
        Args:
            decision: ChimericDecision to format
            include_learning_reason: If True, append learning block reason if blocked
        
        Returns:
            Formatted one-line string
        """
        face_ev = decision.evidence_summary.get("face")
        gait_ev = decision.evidence_summary.get("gait")
        spoof_ev = decision.evidence_summary.get("source_auth")
        
        face_status = self._format_evidence_status(face_ev.status) if face_ev else "MISSING"
        face_sim = f"{face_ev.similarity:.2f}" if face_ev and face_ev.similarity is not None else "N/A"
        
        gait_status = self._format_evidence_status(gait_ev.status) if gait_ev else "MISSING"
        gait_sim = f"{gait_ev.similarity:.2f}" if gait_ev and gait_ev.similarity is not None else "N/A"
        
        learning_str = "ALLOWED" if decision.learning_allowed else "BLOCKED"
        
        identity = self._redact_identity(decision.final_identity)
        
        template = DecisionSummaryTemplate.ONELINE
        formatted = template.format(
            track_id=decision.track_id,
            state=decision.state.name,
            identity=identity,
            confidence=decision.chimeric_confidence,
            face_status=face_status,
            face_sim=face_sim,
            gait_status=gait_status,
            gait_sim=gait_sim,
            reason=decision.decision_reason.name if hasattr(decision.decision_reason, 'name') else str(decision.decision_reason),
            learning=learning_str,
        )
        
        # Optionally append learning block reason
        if include_learning_reason and not decision.learning_allowed and decision.debug_trace:
            reason = decision.debug_trace.get("learning_block_reason", "unknown")
            formatted += f" [learning_reason: {reason}]"
        
        return formatted
    
    def format_detailed(self, decision: ChimericDecision) -> str:
        """
        Format decision with full evidence details (debug mode).
        
        Includes face/gait/spoof attributes, state transitions, confidence synthesis,
        and learning gate reasoning.
        
        Args:
            decision: ChimericDecision to format
        
        Returns:
            Formatted multi-line string with evidence details
        """
        face_ev = decision.evidence_summary.get("face")
        gait_ev = decision.evidence_summary.get("gait")
        spoof_ev = decision.evidence_summary.get("source_auth")
        
        # Extract face attributes
        face_status = self._format_evidence_status(face_ev.status) if face_ev else "MISSING"
        face_sim = face_ev.similarity if face_ev else 0.0
        face_quality = face_ev.quality if face_ev else 0.0
        face_binding = face_ev.binding_state.name if (face_ev and hasattr(face_ev, 'binding_state')) else "N/A"
        face_margin = face_ev.margin if face_ev else 0.0
        
        # Extract gait attributes
        gait_status = self._format_evidence_status(gait_ev.status) if gait_ev else "MISSING"
        gait_sim = gait_ev.similarity if gait_ev else 0.0
        gait_quality = gait_ev.quality if gait_ev else 0.0
        gait_seq_len = gait_ev.sequence_length if (gait_ev and hasattr(gait_ev, 'sequence_length')) else 0
        gait_margin = gait_ev.margin if gait_ev else 0.0
        gait_streak = gait_ev.confirm_streak if (gait_ev and hasattr(gait_ev, 'confirm_streak')) else 0
        
        # Extract spoof attributes
        spoof_state = spoof_ev.state.name if spoof_ev else "MISSING"
        spoof_score = spoof_ev.realness_score if spoof_ev else 0.0
        
        # Prepare learning reason
        learning_str = "ALLOWED" if decision.learning_allowed else "BLOCKED"
        learning_reason = ""
        if not decision.learning_allowed and decision.debug_trace:
            learning_reason = decision.debug_trace.get("learning_block_reason", "unknown")
        
        # Format debug trace
        debug_str = json.dumps(decision.debug_trace, indent=2) if decision.debug_trace else "{}"
        
        identity = self._redact_identity(decision.final_identity)
        reason = decision.decision_reason.name if hasattr(decision.decision_reason, 'name') else str(decision.decision_reason)
        
        timestamp = datetime.fromtimestamp(decision.timestamp).isoformat() if decision.timestamp else "unknown"
        
        template = DecisionSummaryTemplate.DETAILED
        return template.format(
            track_id=decision.track_id,
            timestamp=timestamp,
            state=decision.state.name,
            identity=identity,
            confidence=decision.chimeric_confidence,
            face_status=face_status,
            face_sim=face_sim,
            face_quality=face_quality,
            face_binding=face_binding,
            face_margin=face_margin,
            gait_status=gait_status,
            gait_sim=gait_sim,
            gait_quality=gait_quality,
            gait_seq_len=gait_seq_len,
            gait_margin=gait_margin,
            gait_streak=gait_streak,
            spoof_state=spoof_state,
            spoof_score=spoof_score,
            reason=reason,
            learning=learning_str,
            learning_reason=learning_reason,
            debug_trace=debug_str,
        )
    
    def format_json(self, decision: ChimericDecision) -> str:
        """
        Format decision as JSON (machine-readable, complete state).
        
        Args:
            decision: ChimericDecision to format
        
        Returns:
            JSON-formatted string with complete decision state
        """
        face_ev = decision.evidence_summary.get("face")
        gait_ev = decision.evidence_summary.get("gait")
        spoof_ev = decision.evidence_summary.get("source_auth")
        
        # Serialize evidence
        face_dict = self._serialize_evidence(face_ev) if face_ev else None
        gait_dict = self._serialize_evidence(gait_ev) if gait_ev else None
        spoof_dict = self._serialize_evidence(spoof_ev) if spoof_ev else None
        
        decision_dict = {
            "track_id": decision.track_id,
            "timestamp": decision.timestamp,
            "state": decision.state.name,
            "final_identity": self._redact_identity(decision.final_identity),
            "confidence": decision.chimeric_confidence,
            "reason": decision.decision_reason.name if hasattr(decision.decision_reason, 'name') else str(decision.decision_reason),
            "learning_allowed": decision.learning_allowed,
            "evidence": {
                "face": face_dict,
                "gait": gait_dict,
                "source_auth": spoof_dict,
            },
            "debug_trace": decision.debug_trace,
        }
        
        return json.dumps(decision_dict, indent=2, default=str)
    
    @staticmethod
    def _format_evidence_status(status: EvidenceStatus) -> str:
        """Format EvidenceStatus enum to readable string."""
        if not status:
            return "MISSING"
        return status.name.replace("_", "")  # E.g., "CONFIRMED_STRONG" → "CONFIRMEDSTRONG"
    
    @staticmethod
    def _serialize_evidence(evidence) -> Optional[Dict]:
        """Serialize evidence object to dict."""
        if not evidence:
            return None
        
        return {
            "identity_id": evidence.identity_id if hasattr(evidence, 'identity_id') else None,
            "similarity": evidence.similarity if hasattr(evidence, 'similarity') else None,
            "quality": evidence.quality if hasattr(evidence, 'quality') else None,
            "status": evidence.status.name if hasattr(evidence, 'status') and hasattr(evidence.status, 'name') else str(evidence.status),
            "timestamp": evidence.timestamp if hasattr(evidence, 'timestamp') else None,
            "margin": evidence.margin if hasattr(evidence, 'margin') else None,
            "sequence_length": evidence.sequence_length if hasattr(evidence, 'sequence_length') else None,
            "confirm_streak": evidence.confirm_streak if hasattr(evidence, 'confirm_streak') else None,
            "realness_score": evidence.realness_score if hasattr(evidence, 'realness_score') else None,
            "state": evidence.state.name if hasattr(evidence, 'state') and hasattr(evidence.state, 'name') else str(evidence.state),
        }


# ============================================================================
# METRICS COLLECTION
# ============================================================================

@dataclass
class DecisionMetrics:
    """Snapshot of decision metrics over a time window."""
    
    window_start: float = 0.0
    window_end: float = 0.0
    decision_count: int = 0
    
    # State distribution
    state_distribution: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    
    # Learning gate analysis
    learning_allowed_count: int = 0
    learning_blocked_count: int = 0
    learning_block_reasons: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    
    # Confidence statistics
    avg_confidence: float = 0.0
    min_confidence: float = 0.0
    max_confidence: float = 1.0
    confidence_histogram: Dict[str, int] = field(default_factory=lambda: defaultdict(int))  # "0.0-0.2", "0.2-0.4", etc.
    
    # Modality-specific rates
    face_evidence_rate: float = 0.0  # % of decisions with face evidence
    gait_evidence_rate: float = 0.0  # % of decisions with gait evidence
    spoof_evidence_rate: float = 0.0  # % of decisions with spoof detection
    
    # Conflict tracking
    conflict_count: int = 0
    conflict_resolution_rate: float = 0.0  # % of conflicts resolved in this window
    
    # Track management
    active_track_count: int = 0
    stale_track_cleanup_count: int = 0
    
    def to_dict(self) -> Dict:
        """Convert metrics to dictionary representation."""
        return {
            "window_start": self.window_start,
            "window_end": self.window_end,
            "duration_sec": self.window_end - self.window_start,
            "decision_count": self.decision_count,
            "decisions_per_sec": self.decision_count / max(self.window_end - self.window_start, 0.001),
            "state_distribution": dict(self.state_distribution),
            "learning_allowed": self.learning_allowed_count,
            "learning_blocked": self.learning_blocked_count,
            "learning_block_reasons": dict(self.learning_block_reasons),
            "avg_confidence": self.avg_confidence,
            "confidence_range": (self.min_confidence, self.max_confidence),
            "confidence_histogram": dict(self.confidence_histogram),
            "face_evidence_rate": self.face_evidence_rate,
            "gait_evidence_rate": self.gait_evidence_rate,
            "spoof_evidence_rate": self.spoof_evidence_rate,
            "conflict_count": self.conflict_count,
            "conflict_resolution_rate": self.conflict_resolution_rate,
            "active_tracks": self.active_track_count,
            "stale_cleanups": self.stale_track_cleanup_count,
        }
    
    def format_summary(self) -> str:
        """Format metrics as human-readable summary."""
        duration = self.window_end - self.window_start
        rate = self.decision_count / max(duration, 0.001)
        
        summary = (
            f"Metrics [{datetime.fromtimestamp(self.window_start).isoformat()} → "
            f"{datetime.fromtimestamp(self.window_end).isoformat()}] "
            f"({duration:.1f}s)\n"
            f"  Decisions: {self.decision_count} ({rate:.1f}/sec)\n"
            f"  States: {dict(self.state_distribution)}\n"
            f"  Learning: {self.learning_allowed_count} allowed, "
            f"{self.learning_blocked_count} blocked\n"
            f"  Confidence: avg={self.avg_confidence:.2f}, "
            f"range=[{self.min_confidence:.2f}, {self.max_confidence:.2f}]\n"
            f"  Evidence: face={self.face_evidence_rate:.1%}, "
            f"gait={self.gait_evidence_rate:.1%}, spoof={self.spoof_evidence_rate:.1%}\n"
            f"  Conflicts: {self.conflict_count} detected, "
            f"resolution_rate={self.conflict_resolution_rate:.1%}\n"
            f"  Tracks: {self.active_track_count} active, "
            f"{self.stale_track_cleanup_count} stale cleaned"
        )
        
        return summary


class MetricsCollector:
    """
    Collect and aggregate decision metrics over time windows.
    
    Maintains sliding window of decisions and periodically emits metrics snapshots.
    
    Attributes:
        window_duration_sec: Time window for metrics aggregation (default 5.0)
        max_history_windows: Number of historical windows to retain (default 10)
    """
    
    def __init__(self, window_duration_sec: float = 5.0, max_history_windows: int = 10):
        """
        Initialize metrics collector.
        
        Args:
            window_duration_sec: Duration of each metrics window (default 5.0 seconds)
            max_history_windows: Max historical windows to keep (for analysis)
        """
        self.window_duration_sec = window_duration_sec
        self.max_history_windows = max_history_windows
        
        self.current_window_start = time.time()
        self.current_metrics = DecisionMetrics(window_start=self.current_window_start)
        
        self.history: deque[DecisionMetrics] = deque(maxlen=max_history_windows)
        
        self.decision_buffer: List[ChimericDecision] = []
        self.conflict_resolution_map: Dict[str, bool] = {}  # track_id → was_resolved
    
    def add_decision(self, decision: ChimericDecision) -> None:
        """
        Add a decision to metrics collection.
        
        Args:
            decision: ChimericDecision to add to metrics
        """
        now = time.time()
        
        # Check if we need to rotate window
        if now - self.current_window_start >= self.window_duration_sec:
            self._finalize_window(now)
        
        # Add to buffer and current metrics
        self.decision_buffer.append(decision)
        self.current_metrics.decision_count += 1
        
        # Update state distribution
        self.current_metrics.state_distribution[decision.state.name] += 1
        
        # Update learning metrics
        if decision.learning_allowed:
            self.current_metrics.learning_allowed_count += 1
        else:
            self.current_metrics.learning_blocked_count += 1
            if decision.debug_trace:
                reason = decision.debug_trace.get("learning_block_reason", "unknown")
                self.current_metrics.learning_block_reasons[reason] += 1
        
        # Update confidence stats
        self._update_confidence_stats(decision.chimeric_confidence)
        
        # Update evidence rates
        face_ev = decision.evidence_summary.get("face")
        gait_ev = decision.evidence_summary.get("gait")
        spoof_ev = decision.evidence_summary.get("source_auth")
        
        if face_ev and face_ev.status != EvidenceStatus.MISSING:
            self.current_metrics.face_evidence_rate = (
                (self.current_metrics.face_evidence_rate * (self.current_metrics.decision_count - 1) + 1.0) /
                self.current_metrics.decision_count
            )
        
        if gait_ev and gait_ev.status != EvidenceStatus.MISSING:
            self.current_metrics.gait_evidence_rate = (
                (self.current_metrics.gait_evidence_rate * (self.current_metrics.decision_count - 1) + 1.0) /
                self.current_metrics.decision_count
            )
        
        if spoof_ev and spoof_ev.state != SourceAuthState.MISSING:
            self.current_metrics.spoof_evidence_rate = (
                (self.current_metrics.spoof_evidence_rate * (self.current_metrics.decision_count - 1) + 1.0) /
                self.current_metrics.decision_count
            )
        
        # Track conflicts
        if decision.state == ChimericState.HOLD_CONFLICT:
            self.current_metrics.conflict_count += 1
            self.conflict_resolution_map[decision.track_id] = False
        elif decision.state != ChimericState.HOLD_CONFLICT and decision.track_id in self.conflict_resolution_map:
            if not self.conflict_resolution_map[decision.track_id]:
                self.conflict_resolution_map[decision.track_id] = True
                if self.current_metrics.conflict_count > 0:
                    self.current_metrics.conflict_resolution_rate = min(
                        1.0,
                        sum(1 for v in self.conflict_resolution_map.values() if v) / self.current_metrics.conflict_count
                    )
    
    def _update_confidence_stats(self, confidence: float) -> None:
        """Update confidence statistics (mean, min, max, histogram)."""
        count = self.current_metrics.decision_count
        
        # Update min/max
        self.current_metrics.min_confidence = min(self.current_metrics.min_confidence, confidence)
        self.current_metrics.max_confidence = max(self.current_metrics.max_confidence, confidence)
        
        # Update average (running mean)
        prev_avg = self.current_metrics.avg_confidence
        self.current_metrics.avg_confidence = (
            (prev_avg * (count - 1) + confidence) / count
        )
        
        # Update histogram (0.0-0.2, 0.2-0.4, etc.)
        bin_idx = int(confidence * 5)  # 5 bins: [0-0.2), [0.2-0.4), [0.4-0.6), [0.6-0.8), [0.8-1.0]
        bin_idx = min(4, bin_idx)  # Cap at bin 4 for 1.0
        bin_ranges = ["0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"]
        self.current_metrics.confidence_histogram[bin_ranges[bin_idx]] += 1
    
    def _finalize_window(self, now: float) -> None:
        """Finalize current window and start new one."""
        self.current_metrics.window_end = now
        self.history.append(self.current_metrics)
        
        # Start new window
        self.current_metrics = DecisionMetrics(window_start=now)
        self.current_window_start = now
    
    def get_current_metrics(self) -> DecisionMetrics:
        """Get current (unfinal ized) metrics snapshot."""
        snapshot = self.current_metrics
        snapshot.window_end = time.time()
        return snapshot
    
    def get_history(self) -> List[DecisionMetrics]:
        """Get all finalized metric windows in history."""
        return list(self.history)
    
    def get_aggregate_metrics(self) -> DecisionMetrics:
        """Get aggregate metrics across all historical windows."""
        if not self.history:
            return DecisionMetrics()
        
        aggregate = DecisionMetrics(
            window_start=self.history[0].window_start,
            window_end=self.history[-1].window_end,
        )
        
        # Sum counts
        for window in self.history:
            aggregate.decision_count += window.decision_count
            aggregate.learning_allowed_count += window.learning_allowed_count
            aggregate.learning_blocked_count += window.learning_blocked_count
            aggregate.conflict_count += window.conflict_count
            aggregate.stale_track_cleanup_count += window.stale_track_cleanup_count
            
            for state, count in window.state_distribution.items():
                aggregate.state_distribution[state] += count
            
            for reason, count in window.learning_block_reasons.items():
                aggregate.learning_block_reasons[reason] += count
        
        # Compute averages
        if self.history:
            face_rate = sum(w.face_evidence_rate for w in self.history) / len(self.history)
            gait_rate = sum(w.gait_evidence_rate for w in self.history) / len(self.history)
            spoof_rate = sum(w.spoof_evidence_rate for w in self.history) / len(self.history)
            conflict_res = sum(w.conflict_resolution_rate for w in self.history) / len(self.history)
            
            aggregate.face_evidence_rate = face_rate
            aggregate.gait_evidence_rate = gait_rate
            aggregate.spoof_evidence_rate = spoof_rate
            aggregate.conflict_resolution_rate = conflict_res
        
        return aggregate


# ============================================================================
# MAIN LOGGER CLASS
# ============================================================================

class ChimericLogger:
    """
    Main logging interface for chimeric identity decisions.
    
    Manages decision logging with configurable verbosity, metrics collection,
    and optional file output.
    
    Attributes:
        logger: Python logger instance
        formatter: ChimericDecisionFormatter for output formatting
        metrics: MetricsCollector for aggregating statistics
        level: LogLevel (QUIET, NORMAL, DEBUG, TRACE)
    """
    
    def __init__(
        self,
        log_level: LogLevel = LogLevel.NORMAL,
        redact_identities: bool = False,
        logger_name: str = "chimeric_identity",
        file_path: Optional[str] = None,
    ):
        """
        Initialize chimeric logger.
        
        Args:
            log_level: Verbosity level (QUIET, NORMAL, DEBUG, TRACE)
            redact_identities: If True, anonymize identity names in logs
            logger_name: Name for Python logger instance
            file_path: Optional file to write logs to (in addition to console)
        """
        self.level = log_level
        self.formatter = ChimericDecisionFormatter(redact_identities=redact_identities)
        self.metrics = MetricsCollector()
        
        # Set up Python logger
        self.logger = logging.getLogger(logger_name)
        self.logger.setLevel(logging.DEBUG)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
        
        # File handler (if provided)
        if file_path:
            file_handler = logging.FileHandler(file_path, mode='a')
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
    
    def log_decision(self, decision: ChimericDecision) -> None:
        """
        Log a chimeric decision.
        
        Formatting depends on configured verbosity level.
        
        Args:
            decision: ChimericDecision to log
        """
        # Add to metrics
        self.metrics.add_decision(decision)
        
        # Format and log based on level
        if self.level == LogLevel.QUIET:
            # Only log non-UNKNOWN decisions
            if decision.state != ChimericState.UNKNOWN:
                msg = f"[CHIMERIC] track_id={decision.track_id} state={decision.state.name}"
                self.logger.info(msg)
        
        elif self.level == LogLevel.NORMAL:
            # Log one-line summary
            msg = self.formatter.format_oneline(decision, include_learning_reason=True)
            self.logger.info(msg)
        
        elif self.level == LogLevel.DEBUG:
            # Log detailed evidence trace
            msg = self.formatter.format_detailed(decision)
            self.logger.debug(msg)
        
        elif self.level == LogLevel.TRACE:
            # Log complete JSON state
            msg = f"[CHIMERIC_TRACE] {self.formatter.format_json(decision)}"
            self.logger.debug(msg)
    
    def log_metrics_snapshot(self) -> None:
        """Log current metrics snapshot."""
        metrics = self.metrics.get_current_metrics()
        summary = metrics.format_summary()
        self.logger.info(summary)
    
    def log_error(self, track_id: str, error: Exception, context: str = "") -> None:
        """
        Log an error during decision processing.
        
        Args:
            track_id: Track ID where error occurred
            error: Exception object
            context: Additional context about what was being processed
        """
        msg = f"[CHIMERIC_ERROR] track_id={track_id} context={context} error={type(error).__name__}: {str(error)}"
        self.logger.error(msg)
    
    def log_state_transition(
        self,
        track_id: str,
        old_state: ChimericState,
        new_state: ChimericState,
        reason: str,
    ) -> None:
        """
        Log a state machine transition (debug level).
        
        Args:
            track_id: Track ID
            old_state: Previous state
            new_state: New state
            reason: Reason for transition
        """
        if self.level.value >= LogLevel.DEBUG.value:
            msg = f"[STATE_TRANSITION] track_id={track_id} {old_state.name} → {new_state.name} ({reason})"
            self.logger.debug(msg)
    
    def get_metrics_snapshot(self) -> Dict:
        """Get current metrics as dictionary."""
        return self.metrics.get_current_metrics().to_dict()
    
    def get_metrics_history(self) -> List[Dict]:
        """Get all historical metrics windows as list of dicts."""
        return [m.to_dict() for m in self.metrics.get_history()]
    
    def get_aggregate_metrics(self) -> Dict:
        """Get aggregate metrics across all windows."""
        return self.metrics.get_aggregate_metrics().to_dict()


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def create_logger(
    log_level: str = "NORMAL",
    redact_identities: bool = False,
    file_path: Optional[str] = None,
) -> ChimericLogger:
    """
    Factory function to create a ChimericLogger.
    
    Args:
        log_level: String ("QUIET", "NORMAL", "DEBUG", "TRACE")
        redact_identities: If True, anonymize identities
        file_path: Optional file to write logs to
    
    Returns:
        ChimericLogger instance
    """
    level = LogLevel[log_level.upper()]
    return ChimericLogger(
        log_level=level,
        redact_identities=redact_identities,
        file_path=file_path,
    )


def format_decision_oneline(decision: ChimericDecision, redact: bool = False) -> str:
    """
    Convenience function to format a single decision as one-line.
    
    Args:
        decision: ChimericDecision to format
        redact: If True, anonymize identities
    
    Returns:
        Formatted string
    """
    formatter = ChimericDecisionFormatter(redact_identities=redact)
    return formatter.format_oneline(decision)
