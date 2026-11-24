"""Unified V0.3x product capabilities. Live monitoring and networking; no video replay."""
try:
    from build_profile import EDITION
except ImportError:
    EDITION = ''
if EDITION not in ('', 'PMC', 'Fence'):
    raise ValueError('Unknown edition')
FENCE = EDITION != 'PMC'
NETWORK = True
VERSION = 'V0.31'
BUILD = '2026.11.24-v0.31-raid-log-71'
