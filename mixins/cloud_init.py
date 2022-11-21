"""
mixin class containing methods that are needed by linux vm task classes
methods included;
    - method to deploy a given command to a given host
    - a helper method to fully retrieve the response from paramiko outputs
"""

__all__ = [
    'CloudInitMixin',
]


class CloudInitMixin:

    METADATA_VERSION = 1
    METADATA_SUBNET = '169.254.0.1/16'

    @staticmethod
    def metadata_content_dir(project_id: int) -> str:
        """
        The directory in PodNet where metadata for a project will be stored
        :param project_id: The id of the project that owns the content
        """
        return f'/var/www/P{project_id}'

    @classmethod
    def version_str(cls):
        return f'v{cls.METADATA_VERSION}'
