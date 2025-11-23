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
from gait.config import GaitConfig,default_gait_config
from gait.gait_extractor import GaitExtractor
from gait.gait_gallery import GaitGallery

logger= logging.getLogger(__name__)

class GaitEngine:
    """
    The orchestrator for the gait recognition pipeline.

    How it works:
    This class brings together the `GaitExtractor` (responsible for converting
    pose sequences into gait embeddings) and the `GaitGallery` (for storing and
    searching known gait embeddings). Its primary role is to process incoming
    `Tracklet` objects, extract gait features, attempt to identify them against
    the gallery, and then produce `IdSignal` objects indicating potential
    identity matches. It also translates these signals into final `IdentityDecision`s.

    Attributes:
    - config (GaitConfig): The central configuration object for all gait-related parameters.
    - extractor (GaitExtractor): An instance of `GaitExtractor` used to generate
                                 gait embeddings and quality scores from pose sequences.
    - gallery (GaitGallery): An instance of `GaitGallery` used to manage known
                             gait identities and perform similarity searches.
    """
    def __init__(self):
        #Instance GaitConfig
        self.config=default_gait_config()

        #Instance GaitExtractor and GaitGallery
        self.extractor = GaitExtractor(config=self.config)
        self.gallery= GaitGallery(config=self.config)

        logger.info("GaitEngine initialized")

    def update_signals(self, frame:Frame, tracks:List[Tracklet])->List[IdSignal]:
        """
        Processes active Tracklets to extract gait embeddings, search the gallery,
        and generate identity signals.

        How it works:
        This method iterates through each `Tracklet` currently being tracked.
        For each `tracklet`:
        1.  **Reset Gait Fields**: Resets the `gait_embedding`, `gait_quality`,
            `gait_identity_id`, and `gait_confidence` fields of the `Tracklet`
            for the current frame to ensure fresh data.
        2.  **Sequence Length Check**: It first checks if the `tracklet.gait_sequence_data`
            (containing pose history) meets the `min_sequence_length` required for gait analysis.
            If not, it skips the track.
        3.  **Embedding Extraction**: Calls `self.extractor.extract_gait_embedding_and_quality`
            to convert the pose sequence into a gait embedding and calculate its quality.
            If embedding extraction fails (e.g., due to low quality), it skips the track.
        4.  **Gallery Search**: If a valid gait embedding is obtained, it calls
            `self.gallery.search` to find the closest matching identity in the gallery.
        5.  **Tracklet Update**: Updates the `tracklet`'s gait-related fields
            (`gait_embedding`, `gait_identity_id`, `gait_confidence`) with the results
            from the extraction and search.
        6.  **IdSignal Generation**: Creates an `IdSignal` object containing the
            `track_id`, recognized `identity_id` (or `None`), `confidence`, and method ("gait"),
            and adds it to a list of `gait_signals`.
        Detailed logging is provided for both recognized and unknown identities.

        Args:
            frame (Frame): The current video frame object (used for context, but not directly for processing in this method).
            tracks (List[Tracklet]): A list of active `Tracklet` objects, each potentially
                                     containing a history of pose data.

        Returns:
            List[IdSignal]: A list of `IdSignal` objects, each representing a gait-based
                            identity recognition result for a track.
        """
        gait_signals: List[IdSignal]=[]

        for tracklet in tracks:
            #reset gait fields for the current frame
            tracklet.gait_embedding = None
            tracklet.gait_quality= 0.0
            tracklet.gait_identity_id=None
            tracklet.gait_confidence= None

            #check minimum length of the sequence of poses
            if len(tracklet.gait_sequence_data) < self.config.route.min_sequence_length:
                logger.debug(f"Track {tracklet.track_id}: Not enough poses "
                             f"({len(tracklet.gait_sequence_data)} < {self.config.route.min_sequence_length}) "
                             "for gait analysis.")
                continue

            #extract gait embedding and quality
            gait_embedding, gait_quality = self.extractor.extract_gait_embedding_and_quality(tracklet.gait_sequence_data)
            tracklet.gait_quality = gait_quality # Always update quality, even if embedding is None
            if gait_embedding is None:
                logger.debug(f"Track {tracklet.track_id}: Failed to extract gait embedding (sequence might be invalid).")
                continue

            tracklet.gait_embedding = gait_embedding

            identity_id,confidence = self.gallery.search(gait_embedding)

            tracklet.gait_identity_id=identity_id
            tracklet.gait_confidence=confidence if confidence is not None else 0.0

            #Create an idsignal
            gait_signals.append(
                IdSignal(
                    track_id=tracklet.track_id,
                    identity_id=identity_id,
                    confidence=tracklet.gait_confidence,
                    method="gait"
                )
            )
            # Log detailed only if it's not an unknown match to avoid excessive spam
            if identity_id:
                logger.info(f"Track {tracklet.track_id}: Gait recognized as {identity_id} with confidence {tracklet.gait_confidence:.2f}.")
            else:
                 logger.debug(f"Track {tracklet.track_id}: Gait recognized as UNKNOWN with confidence {tracklet.gait_confidence:.2f}.")
        return gait_signals
        
    def decide(self, signals: List[IdSignal]) -> List[IdentityDecision]:
        """
        Translates gait identity signals into final identity decisions.

        How it works:
        In this Minimum Viable Product (MVP) implementation, every valid `IdSignal`
        received is directly converted into an `IdentityDecision`. This means
        if a gait signal suggests an identity (even an "UNKNOWN" one with a confidence),
        it will result in a decision. In a more sophisticated system, this method
        might incorporate logic to combine signals from multiple modalities (e.g.,
        face and gait), resolve conflicting identities, or filter decisions based
        on higher-level confidence thresholds before finalizing.

        Args:
            signals (List[IdSignal]): A list of `IdSignal` objects produced by
                                      the `update_signals` method or other identity routes.

        Returns:
            List[IdentityDecision]: A list of `IdentityDecision` objects, representing
                                    the final identity determinations.
        """
        decisions: List[IdentityDecision] = []
        if signals:
            for signal in signals:
                
                decisions.append(
                    IdentityDecision(
                        track_id=signal.track_id,
                        identity_id=signal.identity_id,
                        confidence=signal.confidence
                    )
                )
        return decisions