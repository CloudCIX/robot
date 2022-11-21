# stdlib
import logging
# local
from tasks import vm as vm_tasks


class VM:
    """
    A class that handles 'dispatching' a VM to various services such as builders, scrubbers, etc.
    """

    # Network password used to login to the routers
    password: str

    def __init__(self, password: str):
        self.password = password

    def build(self, vm_id: int):
        """
        Dispatches a celery task to build the specified vm
        :param vm_id: The id of the VM to build
        """
        # log a message about the dispatch, and pass the request to celery
        logging.getLogger('robot.dispatchers.vm.build').debug(f'Passing VM #{vm_id} to the build task queue.')
        vm_tasks.build_vm.delay(vm_id)

    def quiesce(self, vm_id: int):
        """
        Dispatches a celery task to quiesce the specified vm
        :param vm_id: The id of the VM to quiesce
        """
        # log a message about the dispatch, and pass the request to celery
        logging.getLogger('robot.dispatchers.vm.quiesce').debug(f'Passing VM #{vm_id} to the quiesce task queue.')
        vm_tasks.quiesce_vm.delay(vm_id)

    def restart(self, vm_id: int):
        """
        Dispatches a celery task to restart the specified vm
        :param vm_id: The id of the VM to restart
        """
        # log a message about the dispatch, and pass the request to celery
        logging.getLogger('robot.dispatchers.vm.restart').debug(f'Passing VM #{vm_id} to the restart task queue.')
        vm_tasks.restart_vm.delay(vm_id)

    def scrub(self, vm_id: int):
        """
        Dispatches a celery task to scrub the specified vm
        :param vm_id: The id of the VM to scrub
        """
        # log a message about the dispatch, and pass the request to celery
        logging.getLogger('robot.dispatchers.vm.scrub').debug(f'Passing VM #{vm_id} to the scrub task queue.')
        vm_tasks.scrub_vm.delay(vm_id)

    def update(self, vm_id: int):
        """
        Dispatches a celery task to update the specified vm
        :param vm_id: The id of the VM to update
        """
        # log a message about the dispatch, and pass the request to celery
        logging.getLogger('robot.dispatchers.vm.update').debug(f'Passing VM #{vm_id} to the update task queue.')
        vm_tasks.update_vm.delay(vm_id)
