"""
files containing tasks related to virtual_routers
"""
from .build import build_virtual_router
from .debug import debug_logs
from .quiesce import quiesce_virtual_router
from .restart import restart_virtual_router
from .scrub import scrub_virtual_router
from .update import update_virtual_router

__all__ = [
    'build_virtual_router',
    'debug_logs',
    'quiesce_virtual_router',
    'restart_virtual_router',
    'scrub_virtual_router',
    'update_virtual_router',
]
