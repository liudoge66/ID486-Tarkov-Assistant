"""Unified V0.3 product capabilities. Live monitoring and networking; no video replay."""
try:
    from build_profile import EDITION
except ImportError:
    EDITION = ''
if EDITION not in ('', 'PMC', 'Fence'):
    raise ValueError('Unknown edition')
FENCE = EDITION != 'PMC'
NETWORK = True
VERSION = 'V0.3'
BUILD = '2026.11.04-v0.3-release-52'
