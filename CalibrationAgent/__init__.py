"""Calibration Agent — walk-forward validation of the signal blend weights.

Proposes weights on a rolling TRAIN window and validates them OUT-OF-SAMPLE on
held-out sessions. Advisory only: never writes SignalWeights. See SKILL.md.
"""
