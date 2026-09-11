"""Deterministic port assignments derived from a worktree's real path."""
import hashlib
import os

PORT_BASE = 4000
PORT_RANGE = 1000


def _path_hash(path):
    real = os.path.realpath(path)
    return int(hashlib.sha256(real.encode()).hexdigest()[:8], 16)


def derive_port(path):
    """Same real worktree path -> same board preview port, forever."""
    return PORT_BASE + _path_hash(path) % PORT_RANGE

