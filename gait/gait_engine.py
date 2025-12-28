"""
gait/gait_engine.py
Integrates GaitExtractor and GaitGallery to process Tracklets with pose data
and produce identity recognition decisions based on gait.
"""
from __future__ import annotations
import logging
from typing import List, Optional
import numpy as np
from schemas import Frame, Tracklet
from schemas.id_signals import IdSignal
from schemas.identity_decision import IdentityDecision
from gait.config import GaitConfig, default_gait_config
from gait.gait_extractor import GaitExtractor
from gait.gait_gallery import GaitGallery

logger = logging.getLogger(__name__)

class GaitEngine:
    def __init__(self):
        """
        Initializes the GaitEngine by loading the configuration,
        instantiating the Extractor (model) and the Gallery (database).
        """
        self.config = default_gait_config()
        self.extractor = GaitExtractor(self.config)
        self.gallery = GaitGallery(self.config)
        logger.info("GaitEngine initialized")

    def update_signals(self, frame: Frame, tracks: List[Tracklet]) -> List[IdSignal]:
        """
        Processes active tracklets to generate identification signals.
        1. Checks if tracklet has sufficient gait sequence data.
        2. Extracts the gait embedding.
        3. Searches the gallery for a match.
        4. Creates an IdSignal with the result.
        """
        signals = []
        for track in tracks:
            if not track.gait_sequence_data:
                continue
                
            # Extract embedding
            embedding, quality = self.extractor.extract_gait_embedding_and_quality(track.gait_sequence_data)
            
            # Update quality for overlay
            track.gait_quality = quality
            
            if embedding is not None:
                # Search in gallery
                match_id, confidence = self.gallery.search(embedding)
                
                # Create an identity signal
                # track_id, identity_id (name), confidence (0-1), method="gait"
                signal = IdSignal(
                    track_id=track.track_id,
                    identity_id=match_id,  # Will be None if unknown or below threshold
                    confidence=confidence if confidence else 0.0,
                    method="gait"
                )
                signals.append(signal)
            
        return signals

    def decide(self, signals: List[IdSignal]) -> List[IdentityDecision]:
        """
        Converts raw IdSignals into final IdentityDecisions.
        Enriches the decision with category information from the gallery
        to be displayed in the UI/Overlay.
        """
        decisions = []
        for signal in signals:
            identity_id = None
            category = "unknown"
            confidence = 0.0
            
            # If the signal found a valid match
            if signal.identity_id:
                identity_id = signal.identity_id
                confidence = signal.confidence
                
                # Retrieve category from gallery
                if self.gallery.get_category(identity_id):
                    category = self.gallery.get_category(identity_id)
            
            # Create final decision for overlay
            decision = IdentityDecision(
                track_id=signal.track_id,
                identity_id=identity_id,
                category=category, 
                confidence=confidence,
                reason=f"Matched by {signal.method}"
            )
            decisions.append(decision)
            
        return decisions