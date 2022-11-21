# stdlib
import logging
# local
from tasks import backup as backup_tasks


class Backup:
    """
    A class that handles 'dispatching' a Backup to various services such as builders, scrubbers and updaters
    """

    # Network password used to login to the routers
    password: str

    def __init__(self, password: str):
        self.password = password

    def build(self, backup_id: int):
        """
        Dispatches a celery task to build the specified backup
        :param backup_id: The id of the Backup to build
        """
        # log a message about the dispatch, and pass the request to celery
        logging.getLogger('robot.dispatchers.backup.build').debug(
            f'Passing Backup #{backup_id} to the build task queue',
        )
        backup_tasks.build_backup.delay(backup_id)

    def scrub(self, backup_id: int):
        """
        Dispatches a celery task to scrub the specified backup
        :param backup_id: The id of the backup to scrub
        """
        # log a message about the dispatch, and pass the request to celery
        logging.getLogger('robot.dispatchers.backup.scrub').debug(
            f'Passing Backup #{backup_id} to the scrub task queue',
        )
        backup_tasks.scrub_backup.delay(backup_id)

    def update(self, backup_id: int):
        """
        Dispatches a celery task to update the specified backup
        :param backup_id: The id of the Backup to update
        """
        # log a message about the dispatch, and pass the request to celery
        logging.getLogger('robot.dispatchers.backup.update').debug(
            f'passsing Backup #{backup_id} to the update task queue',
        )
        backup_tasks.update_backup.delay(backup_id)
