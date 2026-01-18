"""
Chimeric Identity Command-Line Interface

Provides command-line access to chimeric identity fusion system with support for:
  - Multiple runner modes (chimeric_only, face_only, gait_only, analysis_only)
  - Configuration management and override
  - Output options (log files, JSON streams, visualization)
  - Verbosity control (quiet, normal, debug, trace)

Usage:
    python -m chimeric_identity.cli run \
      --mode chimeric_only \
      --config config/chimeric.yaml \
      --log-level debug \
      --output-log decisions.log
    
    python -m chimeric_identity.cli analyze decisions.log \
      --metrics-only
    
    python -m chimeric_identity.cli validate \
      --config config/chimeric.yaml
"""

import argparse
import sys
import json
import logging
from pathlib import Path
from typing import Optional, Dict, List, Any
import yaml

from chimeric_identity.config import ChimericConfig, default_chimeric_config
from chimeric_identity.runner_standalone import (
    ChimericRunner,
    RunnerConfig,
    RunnerMode,
    create_runner,
)
from chimeric_identity.logging_utils import LogLevel, ChimericDecisionFormatter


# ============================================================================
# CLI SUBCOMMANDS
# ============================================================================

class CLICommand:
    """Base class for CLI subcommands."""
    
    def __init__(self, args):
        """Initialize command with parsed arguments."""
        self.args = args
    
    def run(self) -> int:
        """Execute command. Return 0 for success, non-zero for error."""
        raise NotImplementedError


class RunCommand(CLICommand):
    """
    Subcommand: run
    
    Runs the chimeric identity fusion pipeline.
    """
    
    def run(self) -> int:
        """Execute run command."""
        try:
            # Load/create configuration
            chimeric_config = self._load_config()
            
            # Create runner
            runner = ChimericRunner(
                RunnerConfig(
                    mode=RunnerMode[self.args.mode.upper()],
                    chimeric_config=chimeric_config,
                    camera_device_id=self.args.camera_device,
                    video_file_path=self.args.video_file,
                    output_log_file=self.args.output_log,
                    decision_json_output=self.args.decision_json_output,
                    frame_skip=self.args.frame_skip,
                    max_frames=self.args.max_frames,
                    display_results=self.args.display_results,
                    log_level=LogLevel[self.args.log_level.upper()],
                    debug_trace_enabled=self.args.debug_trace,
                    redact_identities=self.args.redact_identities,
                    enable_face_subsystem=not self.args.disable_face,
                    enable_gait_subsystem=not self.args.disable_gait,
                    enable_source_auth=not self.args.disable_source_auth,
                    stale_track_timeout_sec=self.args.stale_track_timeout,
                    max_active_tracks=self.args.max_active_tracks,
                )
            )
            
            # Run main loop
            runner.run()
            
            # Print final metrics
            print("\n" + runner.get_metrics_summary())
            
            return 0
        
        except KeyboardInterrupt:
            print("\nInterrupted by user")
            return 130
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            if self.args.verbose:
                import traceback
                traceback.print_exc()
            return 1
    
    def _load_config(self) -> ChimericConfig:
        """Load chimeric configuration from file or use defaults."""
        if self.args.config:
            try:
                with open(self.args.config, 'r') as f:
                    config_dict = yaml.safe_load(f)
                print(f"Loaded config from {self.args.config}")
                return ChimericConfig(**config_dict.get("chimeric", {}))
            except Exception as e:
                print(f"Warning: Failed to load config {self.args.config}: {e}", file=sys.stderr)
                print("Using default configuration")
        
        return default_chimeric_config()


class AnalyzeCommand(CLICommand):
    """
    Subcommand: analyze
    
    Analyzes decision log files to extract metrics and insights.
    """
    
    def run(self) -> int:
        """Execute analyze command."""
        try:
            if not self.args.input_log:
                print("Error: --input-log required for analyze command", file=sys.stderr)
                return 1
            
            log_file = Path(self.args.input_log)
            if not log_file.exists():
                print(f"Error: Log file not found: {log_file}", file=sys.stderr)
                return 1
            
            # Parse log file
            decisions = self._parse_log_file(log_file)
            
            if not decisions:
                print("No decisions found in log file")
                return 0
            
            # Compute metrics
            metrics = self._compute_metrics(decisions)
            
            # Output results
            if self.args.metrics_only:
                self._print_metrics(metrics)
            else:
                self._print_summary(decisions, metrics)
            
            # Optional: Output to JSON
            if self.args.output_json:
                self._write_json_output(metrics, self.args.output_json)
            
            return 0
        
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            if self.args.verbose:
                import traceback
                traceback.print_exc()
            return 1
    
    def _parse_log_file(self, log_file: Path) -> List[Dict]:
        """Parse log file and extract decisions."""
        decisions = []
        
        with open(log_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or not line.startswith("[CHIMERIC]"):
                    continue
                
                # Parse one-line decision format
                # [CHIMERIC] track_id=X state=Y identity=Z ...
                decision_dict = self._parse_oneline_decision(line)
                if decision_dict:
                    decisions.append(decision_dict)
        
        return decisions
    
    def _parse_oneline_decision(self, line: str) -> Optional[Dict]:
        """Parse one-line decision format."""
        try:
            # Simple parsing: extract key=value pairs
            parts = line.split()
            decision_dict = {}
            
            for part in parts:
                if '=' in part:
                    key, value = part.split('=', 1)
                    decision_dict[key] = value
            
            return decision_dict if decision_dict else None
        except Exception:
            return None
    
    def _compute_metrics(self, decisions: List[Dict]) -> Dict[str, Any]:
        """Compute metrics from decisions."""
        from collections import defaultdict
        
        metrics = {
            "total_decisions": len(decisions),
            "state_distribution": defaultdict(int),
            "learning_allowed_count": 0,
            "learning_blocked_count": 0,
            "avg_confidence": 0.0,
            "conflict_count": 0,
            "track_ids": set(),
        }
        
        confidence_sum = 0
        
        for decision in decisions:
            state = decision.get("state", "UNKNOWN")
            metrics["state_distribution"][state] += 1
            
            learning = decision.get("learning", "BLOCKED")
            if learning == "ALLOWED":
                metrics["learning_allowed_count"] += 1
            else:
                metrics["learning_blocked_count"] += 1
            
            # Track confidence
            try:
                confidence = float(decision.get("confidence", "0.0").split()[0])
                confidence_sum += confidence
            except (ValueError, IndexError):
                pass
            
            # Track conflicts
            if state == "HOLD_CONFLICT":
                metrics["conflict_count"] += 1
            
            # Collect track IDs
            track_id = decision.get("track_id")
            if track_id:
                metrics["track_ids"].add(track_id)
        
        if decisions:
            metrics["avg_confidence"] = confidence_sum / len(decisions)
        
        metrics["state_distribution"] = dict(metrics["state_distribution"])
        metrics["unique_tracks"] = len(metrics["track_ids"])
        metrics.pop("track_ids", None)  # Remove set before JSON serialization
        
        return metrics
    
    def _print_summary(self, decisions: List[Dict], metrics: Dict[str, Any]) -> None:
        """Print summary of analysis."""
        print(f"\nDecision Log Analysis: {self.args.input_log}")
        print("=" * 60)
        print(f"Total decisions: {metrics['total_decisions']}")
        print(f"Unique tracks: {metrics['unique_tracks']}")
        print(f"\nState Distribution:")
        for state, count in metrics["state_distribution"].items():
            pct = 100 * count / metrics["total_decisions"]
            print(f"  {state}: {count} ({pct:.1f}%)")
        print(f"\nLearning Gates:")
        print(f"  Allowed: {metrics['learning_allowed_count']}")
        print(f"  Blocked: {metrics['learning_blocked_count']}")
        print(f"\nConfidence:")
        print(f"  Average: {metrics['avg_confidence']:.3f}")
        print(f"\nConflicts:")
        print(f"  Total: {metrics['conflict_count']}")
    
    def _print_metrics(self, metrics: Dict[str, Any]) -> None:
        """Print metrics only (compact format)."""
        for key, value in metrics.items():
            print(f"{key}: {value}")
    
    def _write_json_output(self, metrics: Dict[str, Any], output_file: str) -> None:
        """Write metrics to JSON file."""
        with open(output_file, 'w') as f:
            json.dump(metrics, f, indent=2)
        print(f"\nMetrics saved to {output_file}")


class ValidateCommand(CLICommand):
    """
    Subcommand: validate
    
    Validates chimeric configuration for correctness and consistency.
    """
    
    def run(self) -> int:
        """Execute validate command."""
        try:
            config = self._load_config()
            
            # Validate configuration
            errors, warnings = self._validate_config(config)
            
            # Print results
            print(f"\nValidating Chimeric Configuration")
            print("=" * 60)
            
            if not errors and not warnings:
                print("✓ Configuration is valid!")
                return 0
            
            if warnings:
                print(f"\n⚠ Warnings ({len(warnings)}):")
                for warning in warnings:
                    print(f"  - {warning}")
            
            if errors:
                print(f"\n✗ Errors ({len(errors)}):")
                for error in errors:
                    print(f"  - {error}")
                return 1
            
            return 0
        
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
    
    def _load_config(self) -> ChimericConfig:
        """Load configuration from file."""
        if not self.args.config:
            return default_chimeric_config()
        
        try:
            with open(self.args.config, 'r') as f:
                config_dict = yaml.safe_load(f)
            return ChimericConfig(**config_dict.get("chimeric", {}))
        except Exception as e:
            raise ValueError(f"Failed to load config {self.args.config}: {e}")
    
    def _validate_config(self, config: ChimericConfig) -> tuple[List[str], List[str]]:
        """
        Validate configuration for correctness.
        
        Returns:
            (errors, warnings) tuples of strings
        """
        errors = []
        warnings = []
        
        # Check confirmation thresholds
        if config.quality_gates.min_face_quality < 0 or config.quality_gates.min_face_quality > 1:
            errors.append("min_face_quality must be in [0, 1]")
        
        if config.quality_gates.min_gait_quality < 0 or config.quality_gates.min_gait_quality > 1:
            errors.append("min_gait_quality must be in [0, 1]")
        
        # Check confirmation thresholds ordering
        if config.confirmation.face_confirm_threshold > config.confirmation.face_strong_threshold:
            errors.append("face_confirm_threshold must be <= face_strong_threshold")
        
        if config.confirmation.face_switch_threshold < config.confirmation.face_confirm_threshold:
            warnings.append("face_switch_threshold < face_confirm_threshold (hysteresis not enforced)")
        
        # Check temporal windows
        if config.temporal.face_evidence_window_sec <= 0:
            errors.append("face_evidence_window_sec must be > 0")
        
        if config.temporal.gait_evidence_window_sec <= 0:
            errors.append("gait_evidence_window_sec must be > 0")
        
        # Check conflict parameters
        if config.conflict.conflict_hold_max_frames <= 0:
            errors.append("conflict_hold_max_frames must be > 0")
        
        # Check learning gates
        if config.learning_gate.face_min_quality > config.quality_gates.min_face_quality:
            warnings.append(
                f"Learning face quality ({config.learning_gate.face_min_quality}) > "
                f"gate quality ({config.quality_gates.min_face_quality})"
            )
        
        return errors, warnings


# ============================================================================
# MAIN CLI ENTRY POINT
# ============================================================================

def main(argv: Optional[List[str]] = None) -> int:
    """
    Main CLI entry point.
    
    Args:
        argv: Command-line arguments (or None to use sys.argv)
    
    Returns:
        Exit code (0 = success)
    """
    parser = argparse.ArgumentParser(
        prog="chimeric_identity",
        description="Chimeric Biometric Identity Fusion System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run chimeric fusion pipeline
  python -m chimeric_identity.cli run \\
    --mode chimeric_only \\
    --config config/chimeric.yaml \\
    --log-level debug

  # Run from video file
  python -m chimeric_identity.cli run \\
    --video-file input.mp4 \\
    --max-frames 1000

  # Analyze decision log
  python -m chimeric_identity.cli analyze \\
    --input-log decisions.log \\
    --metrics-only

  # Validate configuration
  python -m chimeric_identity.cli validate \\
    --config config/chimeric.yaml
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")
    
    # ========== RUN SUBCOMMAND ==========
    run_parser = subparsers.add_parser("run", help="Run chimeric fusion pipeline")
    
    run_parser.add_argument(
        "--mode",
        default="chimeric_only",
        choices=["chimeric_only", "face_only", "gait_only", "analysis_only"],
        help="Runner mode (default: chimeric_only)"
    )
    
    run_parser.add_argument(
        "--config",
        type=str,
        help="Chimeric config file (YAML)"
    )
    
    run_parser.add_argument(
        "--camera-device",
        type=int,
        default=0,
        help="Camera device ID (default: 0)"
    )
    
    run_parser.add_argument(
        "--video-file",
        type=str,
        help="Video file path (if provided, use instead of camera)"
    )
    
    run_parser.add_argument(
        "--output-log",
        type=str,
        help="Output log file path"
    )
    
    run_parser.add_argument(
        "--decision-json-output",
        type=str,
        help="JSON decisions output file"
    )
    
    run_parser.add_argument(
        "--log-level",
        default="normal",
        choices=["quiet", "normal", "debug", "trace"],
        help="Logging verbosity (default: normal)"
    )
    
    run_parser.add_argument(
        "--frame-skip",
        type=int,
        default=0,
        help="Skip N frames (for faster processing, default: 0)"
    )
    
    run_parser.add_argument(
        "--max-frames",
        type=int,
        help="Max frames to process (for testing)"
    )
    
    run_parser.add_argument(
        "--display-results",
        action="store_true",
        help="Show visualization"
    )
    
    run_parser.add_argument(
        "--debug-trace",
        action="store_true",
        help="Enable detailed debug trace"
    )
    
    run_parser.add_argument(
        "--redact-identities",
        action="store_true",
        help="Redact identity names in logs"
    )
    
    run_parser.add_argument(
        "--disable-face",
        action="store_true",
        help="Disable face subsystem"
    )
    
    run_parser.add_argument(
        "--disable-gait",
        action="store_true",
        help="Disable gait subsystem"
    )
    
    run_parser.add_argument(
        "--disable-source-auth",
        action="store_true",
        help="Disable source auth (spoof detection)"
    )
    
    run_parser.add_argument(
        "--stale-track-timeout",
        type=float,
        default=10.0,
        help="Stale track timeout in seconds (default: 10.0)"
    )
    
    run_parser.add_argument(
        "--max-active-tracks",
        type=int,
        default=100,
        help="Max active tracks (default: 100)"
    )
    
    # ========== ANALYZE SUBCOMMAND ==========
    analyze_parser = subparsers.add_parser("analyze", help="Analyze decision log")
    
    analyze_parser.add_argument(
        "--input-log",
        type=str,
        required=True,
        help="Input log file path"
    )
    
    analyze_parser.add_argument(
        "--metrics-only",
        action="store_true",
        help="Output metrics only (no summary)"
    )
    
    analyze_parser.add_argument(
        "--output-json",
        type=str,
        help="Output metrics to JSON file"
    )
    
    # ========== VALIDATE SUBCOMMAND ==========
    validate_parser = subparsers.add_parser("validate", help="Validate configuration")
    
    validate_parser.add_argument(
        "--config",
        type=str,
        help="Config file to validate"
    )
    
    # ========== COMMON ARGUMENTS ==========
    for subparser in [run_parser, analyze_parser, validate_parser]:
        subparser.add_argument(
            "--verbose",
            "-v",
            action="store_true",
            help="Verbose output (print tracebacks)"
        )
    
    # Parse arguments
    args = parser.parse_args(argv)
    
    if not args.command:
        parser.print_help()
        return 1
    
    # Dispatch to command
    if args.command == "run":
        command = RunCommand(args)
    elif args.command == "analyze":
        command = AnalyzeCommand(args)
    elif args.command == "validate":
        command = ValidateCommand(args)
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1
    
    return command.run()


if __name__ == "__main__":
    sys.exit(main())
