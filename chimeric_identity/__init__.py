# chimeric_identity/__init__.py
# ============================================================================
# CHIMERIC BIOMETRIC IDENTITY FUSION MODULE
# ============================================================================
#
# Purpose:
#   Fuse Face (strong, fast biometric) and Gait (soft, slow biometric) into
#   a single conservative identity decision engine. Prevents identity drift
#   and false matches via evidence-based governance.
#
# Design Principles:
#   1. NON-INVASIVE: Zero modifications to existing face/gait subsystems
#   2. PLUG-IN STYLE: Operates via adapters (read-only access)
#   3. CONSERVATIVE: Face dominates, gait proposes, conflicts halt decisions
#   4. TEMPORAL AWARE: Bridges face (100ms) vs gait (1000ms) desync
#   5. LEARNING GATED: Template updates only when evidence aligned
#
# Module Structure:
#   - types.py: Evidence/Decision/State dataclasses
#   - state_machine.py: Per-track state transitions
#   - evidence_accumulator.py: Temporal buffering + hysteresis
#   - adapters/: Face/Gait/SourceAuth wrappers
#   - fusion_engine.py: Core decision synthesis logic
#   - governance.py: Learning gates + confidence policies
#   - runner_standalone.py: Full pipeline runner
#   - cli.py: Command-line interface
#
# Version: 1.0.0 (Phase 1: Foundation)
# Architecture: Conservative Evidence-Based Fusion with State Machine

__version__ = "1.0.0"
__author__ = "GaitGuard Chimeric Team"

# Core types for external import
from .types import (
    ChimericState,
    ChimericReason,
    FaceEvidence,
    GaitEvidence,
    SourceAuthEvidence,
    ChimericDecision,
    EvidenceStatus,
    SourceAuthState,
)

__all__ = [
    "ChimericState",
    "ChimericReason",
    "FaceEvidence",
    "GaitEvidence",
    "SourceAuthEvidence",
    "ChimericDecision",
    "EvidenceStatus",
    "SourceAuthState",
]
