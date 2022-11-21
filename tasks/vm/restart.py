# stdlib
import logging
from typing import Any, Dict
# lib
import opentracing
from cloudcix.api.iaas import IAAS
from jaeger_client import Span
# local
import metrics
import state
import utils
from celery_app import app
from cloudcix_token import Token
from email_notifier import EmailNotifier
from restarters import (
    LinuxVM,
    WindowsVM,
)


__all__ = [
    'restart_vm',
]


def _unresource(vm: Dict[str, Any], span: Span):
    """
    unresource the specified vm because something went wrong
    """
    logger = logging.getLogger('robot.tasks.vm.restart')
    vm_id = vm['id']
    # Send failure metric
    metrics.vm_restart_failure()

    # Update state to UNRESOURCED in the API
    child_span = opentracing.tracer.start_span('update_to_unresourced', child_of=span)
    response = IAAS.vm.partial_update(
        token=Token.get_instance().token,
        pk=vm_id,
        data={'state': state.UNRESOURCED},
        span=child_span,
    )
    child_span.finish()

    if response.status_code != 200:
        logger.error(f'Could not update VM #{vm_id} to state UNRESOURCED.\nResponse: {response.content.decode()}.')

    child_span = opentracing.tracer.start_span('send_email', child_of=span)
    try:
        EmailNotifier.vm_failure(vm, 'restart')
    except Exception:
        logger.error(f'Failed to send failure email for VM #{vm_id}', exc_info=True)
    child_span.finish()


@app.task
def restart_vm(vm_id: int):
    """
    Helper function that wraps the actual task in a span, meaning we don't have to remember to call .finish
    """
    span = opentracing.tracer.start_span('tasks.restart_vm')
    span.set_tag('vm_id', vm_id)
    _restart_vm(vm_id, span)
    span.finish()
    # Flush the loggers here so it's not in the span
    utils.flush_logstash()


def _restart_vm(vm_id: int, span: Span):
    """
    Task to restart the specified vm
    """
    logger = logging.getLogger('robot.tasks.vm.restart')
    logger.info(f'Commencing restart of VM #{vm_id}')

    # Read the VM
    child_span = opentracing.tracer.start_span('read_vm', child_of=span)
    vm = utils.api_read(IAAS.vm, vm_id, span=child_span)
    child_span.finish()

    # Ensure it is not empty
    if not bool(vm):
        # Rely on the utils method for logging
        metrics.vm_restart_failure()
        span.set_tag('return_reason', 'invalid_vm_id')
        return

    # Ensure that the state of the vm is still currently RESTART
    if vm['state'] != state.RESTART:
        logger.warning(f'Cancelling restart of VM #{vm_id}. Expected state to be RESTART, found {vm["state"]}.')
        # Return out of this function without doing anything
        span.set_tag('return_reason', 'not_in_valid_state')
        return

    # Update to intermediate state here (RESTARTING - 13)
    child_span = opentracing.tracer.start_span('update_to_restarting', child_of=span)
    response = IAAS.vm.partial_update(
        token=Token.get_instance().token,
        pk=vm_id,
        data={'state': state.RESTARTING},
        span=child_span,
    )
    child_span.finish()

    # Ensure the update was successful
    if response.status_code != 200:
        logger.error(f'Could not update VM #{vm_id} to RESTARTING.\nResponse: {response.content.decode()}.')
        span.set_tag('return_reason', 'could_not_update_state')
        metrics.vm_restart_failure()
        # Update to Unresourced?
        return

    # Read the VM server to get the server type
    child_span = opentracing.tracer.start_span('read_vm_server', child_of=span)
    server = utils.api_read(IAAS.server, vm['server_id'], span=child_span)
    child_span.finish()
    if not bool(server):
        logger.error(f'Could not restart VM #{vm_id} as its Server was not readable')
        _unresource(vm, span)
        span.set_tag('return_reason', 'server_not_read')
        return
    server_type = server['type']['name']
    # add server details to vm
    vm['server_data'] = server

    # Do the actual restarting
    vm['errors'] = []
    success: bool = False
    child_span = opentracing.tracer.start_span('restart', child_of=span)
    try:
        if server_type == 'HyperV':
            success = WindowsVM.restart(vm, child_span)
            child_span.set_tag('server_type', 'windows')
        elif server_type == 'KVM':
            success = LinuxVM.restart(vm, child_span)
            child_span.set_tag('server_type', 'linux')
        elif server_type == 'Phantom':
            success = True
            child_span.set_tag('server_type', 'phantom')
        else:
            error = f'Unsupported server type #{server_type} for VM #{vm_id}.'
            logger.error(error)
            vm['errors'].append(error)
            child_span.set_tag('server_type', 'unsupported')
    except Exception as err:
        error = f'An unexpected error occurred when attempting to restart VM #{vm_id}.'
        logger.error(error, exc_info=True)
        vm['errors'].append(f'{error} Error: {err}')
    child_span.finish()

    span.set_tag('return_reason', f'success: {success}')

    if success:
        logger.info(f'Successfully restarted VM #{vm_id}')
        metrics.vm_restart_success()
        # Update state back to RUNNING
        child_span = opentracing.tracer.start_span('update_to_running', child_of=span)
        response = IAAS.vm.partial_update(
            token=Token.get_instance().token,
            pk=vm_id,
            data={'state': state.RUNNING},
            span=child_span,
        )
        if response.status_code != 200:
            logger.error(f'Could not update VM #{vm_id} to state RUNNING.\nResponse: {response.content.decode()}.')
    else:
        logger.error(f'Failed to restart VM #{vm_id}')
        vm.pop('server_data')
        _unresource(vm, span)
