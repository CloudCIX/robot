from .backup import Linux as LinuxBackup
from .backup import Windows as WindowsBackup
from .snapshot import Linux as LinuxSnapshot
from .snapshot import Windows as WindowsSnapshot
from .virtual_router import VirtualRouter
from .vm import Linux as LinuxVM
from .vm import Windows as WindowsVM


__all__ = [
    # backup
    'LinuxBackup',
    'WindowsBackup',
    # snapshot
    'LinuxSnapshot',
    'WindowsSnapshot',
    # vm
    'LinuxVM',
    'WindowsVM',
    # virtual router
    'VirtualRouter',
]
