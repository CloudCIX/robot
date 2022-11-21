# stdlib
import logging
# from datetime import datetime, timedelta
# local
# import tasks
from tasks import virtual_router as virtual_router_tasks


class VirtualRouter:
    """
    A class that handles 'dispatching' a virtual_router to various services such as builders, scrubbers, etc.
    """

    # Network password used to login to the routers
    password: str

    def __init__(self, password: str):
        self.password = password

    def build(self, virtual_router_id: int):
        """
        Dispatches a celery task to build the specified virtual_router
        :param virtual_router_id: The id of the virtual_router to build
        """
        # log a message about the dispatch, and pass the request to celery
        logging.getLogger('robot.dispatchers.virtual_router.build').debug(
            f'Passing virtual_router #{virtual_router_id} to the build task queue',
        )
        virtual_router_tasks.build_virtual_router.delay(virtual_router_id)
        # Reset debug logs of firewall rules after 15min
        # commenting the firewall rule debugging until logging is sorted out
        # logging.getLogger('robot.dispatchers.virtual_router.debug_logging').debug(
        #     f'Passing virtual_router #{virtual_router_id} to the debug_logs task queue after virtual_router build',
        # )
        # tasks.debug.s(virtual_router_id).apply_async(eta=datetime.now() + timedelta(seconds=15 * 60))

    def quiesce(self, virtual_router_id: int):
        """
        Dispatches a celery task to quiesce the specified virtual_router
        :param virtual_router_id: The id of the virtual_router to quiesce
        """
        # log a message about the dispatch, and pass the request to celery
        logging.getLogger('robot.dispatchers.virtual_router.quiesce').debug(
            f'Passing virtual_router #{virtual_router_id} to the quiesce task queue',
        )
        virtual_router_tasks.quiesce_virtual_router.delay(virtual_router_id)

    def restart(self, virtual_router_id: int):
        """
        Dispatches a celery task to restart the specified virtual_router
        :param virtual_router_id: The id of the virtual_router to restart
        """
        # log a message about the dispatch, and pass the request to celery
        logging.getLogger('robot.dispatchers.virtual_router.restart').debug(
            f'Passing virtual_router #{virtual_router_id} to the restart task queue',
        )
        virtual_router_tasks.restart_virtual_router.delay(virtual_router_id)

    def scrub(self, virtual_router_id: int):
        """
        Dispatches a celery task to scrub the specified virtual_router
        :param virtual_router_id: The id of the virtual_router to scrub
        """
        # log a message about the dispatch, and pass the request to celery
        logging.getLogger('robot.dispatchers.virtual_router.scrub').debug(
            f'Passing virtual_router #{virtual_router_id} to the scrub task queue',
        )
        virtual_router_tasks.scrub_virtual_router.delay(virtual_router_id)

    def update(self, virtual_router_id: int):
        """
        Dispatches a celery task to update the specified virtual_router
        :param virtual_router_id: The id of the virtual_router to update
        """
        # log a message about the dispatch, and pass the request to celery
        logging.getLogger('robot.dispatchers.virtual_router.update').debug(
            f'Passing virtual_router #{virtual_router_id} to the update task queue',
        )
        virtual_router_tasks.update_virtual_router.delay(virtual_router_id)
        # Reset debug logs of firewall rules after 15min
        # commenting the firewall rule debugging until logging is sorted out
        # logging.getLogger('robot.dispatchers.vrf.debug_logging').debug(
        #     f'Passing VRF #{virtual_router_id} to the debug_logs task queue after vrf update',
        # )
        # tasks.debug.s(virtual_router_id).apply_async(eta=datetime.now() + timedelta(seconds=15 * 60))
