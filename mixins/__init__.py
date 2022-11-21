"""
mixin classes that have functions that might be used in multiple places
"""
from .cloud_init import CloudInitMixin
from .linux import LinuxMixin
from .vm import VMImageMixin, VMUpdateMixin
from .windows import WindowsMixin

__all__ = [
    'CloudInitMixin',
    'LinuxMixin',
    'VMImageMixin',
    'VMUpdateMixin',
    'WindowsMixin',
]
