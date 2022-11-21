# stdlib
import logging
# local
from tasks import snapshot as snapshot_tasks


class Snapshot:
    """
    A class that handles 'dispatching' a Snapshot to various services such as builders, scrubbers and updaters
    """

    # Network password used to login to the routers
    password: str

    def __init__(self, password: str):
        self.password = password

    def build(self, snapshot_id: int):
        """
        Dispatches a celery task to build the specified snapshot
        :param snapshot_id: The id of the Snapshot to build
        """
        # log a message about the dispatch, and pass the request to celery
        logging.getLogger('robot.dispatchers.snapshot.build').debug(
            f'Passing Snapshot #{snapshot_id} to the build task queue',
        )
        snapshot_tasks.build_snapshot.delay(snapshot_id)

    def scrub(self, snapshot_id: int):
        """
        Dispatches a celery task to scrub the specified snapshot
        :param snapshot_id: The id of the snapshot to scrub
        """
        # log a message about the dispatch, and pass the request to celery
        logging.getLogger('robot.dispatchers.snapshot.scrub').debug(
            f'Passing Snapshot #{snapshot_id} to the scrub task queue',
        )
        snapshot_tasks.scrub_snapshot.delay(snapshot_id)

    def update(self, snapshot_id: int):
        """
        Dispatches a celery task to update the specified snapshot
        :param snapshot_id: The id of the Snapshot to update
        """
        # log a message about the dispatch, and pass the request to celery
        logging.getLogger('robot.dispatchers.snapshot.update').debug(
            f'passsing Snapshot #{snapshot_id} to the update task queue',
        )
        snapshot_tasks.update_snapshot.delay(snapshot_id)
