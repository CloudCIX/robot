"""
files containing tasks related to vms
"""
from .build import build_vm
from .quiesce import quiesce_vm
from .restart import restart_vm
from .scrub import scrub_vm
from .update import update_vm

__all__ = [
    'build_vm',
    'quiesce_vm',
    'restart_vm',
    'scrub_vm',
    'update_vm',
]
