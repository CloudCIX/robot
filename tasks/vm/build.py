# stdlib
import logging
from datetime import datetime, timedelta
from typing import Any, Dict
# lib
import opentracing
from cloudcix.api.iaas import IAAS
from jaeger_client import Span
# local
import metrics
import state
import utils
from builders import (
    LinuxVM,
    WindowsVM,
)
from celery_app import app
from cloudcix_token import Token
from email_notifier import EmailNotifier

__all__ = [
    'build_vm',
]


def _unresource(vm: Dict[str, Any], span: Span):
    """
    unresource the specified vm because something went wrong
    """
    vm_id = vm['id']
    metrics.vm_build_failure()

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
        logging.getLogger('robot.tasks.vm.build').error(
            f'Could not update VM #{vm_id} to state UNRESOURCED.\nResponse: {response.content.decode()}.',
        )
    child_span = opentracing.tracer.start_span('send_email', child_of=span)
    try:
        EmailNotifier.vm_build_failure(vm)
    except Exception:
        logging.getLogger('robot.tasks.vm.build').error(
            f'Failed to send build failure email for VM #{vm_id}',
            exc_info=True,
        )
    child_span.finish()


@app.task
def build_vm(vm_id: int):
    """
    Helper function that wraps the actual task in a span, meaning we don't have to remember to call .finish
    """
    span = opentracing.tracer.start_span('tasks.build_vm')
    span.set_tag('vm_id', vm_id)
    _build_vm(vm_id, span)
    span.finish()

    # Flush the loggers here so it's not in the span
    utils.flush_logstash()


def _build_vm(vm_id: int, span: Span):
    """
    Task to build the specified vm
    """
    logger = logging.getLogger('robot.tasks.vm.build')
    logger.info(f'Commencing build of VM #{vm_id}')

    # Read the VM
    child_span = opentracing.tracer.start_span('read_vm', child_of=span)
    vm = utils.api_read(IAAS.vm, vm_id, span=child_span)
    child_span.finish()

    # Ensure it is not empty
    if not bool(vm):
        # Rely on the utils method for logging
        metrics.vm_build_failure()
        span.set_tag('return_reason', 'invalid_vm_id')
        return

    # Ensure that the state of the vm is still currently REQUESTED (it hasn't been picked up by another runner)
    if vm['state'] != state.REQUESTED:
        logger.warning(f'Cancelling build of VM #{vm_id}. Expected state to be {state.REQUESTED}, found {vm["state"]}.')
        # Return out of this function without doing anything as it was already handled
        span.set_tag('return_reason', 'not_in_correct_state')
        return

    # catch all the errors if any
    vm['errors'] = []

    # Also ensure that the VR is built for the VM
    child_span = opentracing.tracer.start_span('read_project_vr', child_of=span)
    vr_id = vm['project']['virtual_router_id']
    # need to read so to get the current state of virtual_router
    vm_vr = utils.api_read(IAAS.virtual_router, pk=vr_id, span=child_span)
    child_span.finish()

    if vm_vr['state'] == state.UNRESOURCED:
        # If the VR is UNRESOURCED, we cannot build the VM
        error = f'Virtual Router #{vm_vr["id"]} is UNRESOURCED so we cannot build VM #{vm_id}'
        logger.error(error)
        vm['errors'].append(error)
        _unresource(vm, span)
        span.set_tag('return_reason', 'vr_unresourced')
        return
    elif vm_vr['state'] != state.RUNNING:
        logger.warning(
            f'Virtual Router #{vm_vr["id"]} is not yet built, postponing build of VM #{vm_id}. '
            f'Virtual Router is currently in state {vm_vr["state"]}',
        )
        # Return without changing the state
        span.set_tag('return_reason', 'vr_not_ready')
        # since virtual_router is not ready yet so wait for 10 sec and try again.
        build_vm.s(vm_id).apply_async(eta=datetime.now() + timedelta(seconds=10))
        return

    # If all is well and good here, update the VM state to BUILDING and pass the data to the builder
    child_span = opentracing.tracer.start_span('update_to_building', child_of=span)
    response = IAAS.vm.partial_update(
        token=Token.get_instance().token,
        pk=vm_id,
        data={'state': state.BUILDING},
        span=child_span,
    )
    child_span.finish()

    if response.status_code != 200:
        logger.error(f'Could not update VM #{vm_id} to state BUILDING.\nResponse: {response.content.decode()}.')
        metrics.vm_build_failure()
        span.set_tag('return_reason', 'could_not_update_state')
        return

    # Read the VM server to get the server type
    child_span = opentracing.tracer.start_span('read_vm_server', child_of=span)
    server = utils.api_read(IAAS.server, vm['server_id'], span=child_span)
    child_span.finish()
    if not bool(server):
        logger.error(f'Could not build VM #{vm_id} as its Server was not readable')
        _unresource(vm, span)
        span.set_tag('return_reason', 'server_not_read')
        return
    server_type = server['type']['name']
    # add server details to vm
    vm['server_data'] = server

    # Call the appropriate builder
    success: bool = False
    send_email: bool = True
    child_span = opentracing.tracer.start_span('build', child_of=span)
    try:
        if server_type == 'HyperV':
            success = WindowsVM.build(vm, child_span)
            child_span.set_tag('server_type', 'vm')
        elif server_type == 'KVM':
            success = LinuxVM.build(vm, child_span)
            child_span.set_tag('server_type', 'vm')
        elif server_type == 'Phantom':
            success = True
            send_email = False
            child_span.set_tag('server_type', 'phantom')
        else:
            error = f'Unsupported server type #{server_type} for VM #{vm_id}.'
            logger.error(error)
            vm['errors'].append(error)
            child_span.set_tag('server_type', 'unsupported')
    except Exception as err:
        error = f'An unexpected error occurred when attempting to build VM #{vm_id}.'
        logger.error(error, exc_info=True)
        vm['errors'].append(f'{error} Error: {err}')
    child_span.finish()

    span.set_tag('return_reason', f'success: {success}')

    if success:
        logger.info(f'Successfully built VM #{vm_id}')

        # Update state to RUNNING in the API
        child_span = opentracing.tracer.start_span('update_to_running', child_of=span)
        response = IAAS.vm.partial_update(
            token=Token.get_instance().token,
            pk=vm_id,
            data={'state': state.RUNNING},
            span=child_span,
        )
        child_span.finish()

        if response.status_code != 200:
            logger.error(f'Could not update VM #{vm_id} to state RUNNING. Response: {response.content.decode()}.')

        if send_email:
            child_span = opentracing.tracer.start_span('send_email', child_of=span)
            try:
                EmailNotifier.vm_build_success(vm)
            except Exception:
                logger.error(f'Failed to send build success email for VM #{vm_id}', exc_info=True)
            child_span.finish()

        # Calculate the total time it took to build the VM entirely
        # uctnow - vm created time
        total_time = datetime.utcnow() - datetime.strptime(vm['created'], '%Y-%m-%dT%H:%M:%S.%f')
        logger.debug(f'Finished building VM #{vm_id} in {total_time.seconds} seconds')
        metrics.vm_build_success(total_time.seconds)
    else:
        logger.error(f'Failed to build VM #{vm_id}')
        vm.pop('admin_password', None)
        vm.pop('server_data')
        _unresource(vm, span)
