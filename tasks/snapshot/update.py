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
from updaters.snapshot import (
    Linux as LinuxSnapshot,
    Windows as WindowsSnapshot,
)


__all__ = [
    'update_snapshot',
]


def _unresource(snapshot: Dict[str, Any], span: Span):
    """
    unresource the specified snapshot because something went wrong
    """
    logger = logging.getLogger('robot.tasks.snapshot.update')
    snapshot_id = snapshot['id']
    # Send failure metric
    metrics.snapshot_update_failure()

    # Update state to UNRESOURCED in the API
    child_span = opentracing.tracer.start_span('update_to_unresourced', child_of=span)
    response = IAAS.snapshot.partial_update(
        token=Token.get_instance().token,
        pk=snapshot_id,
        data={'state': state.UNRESOURCED},
        span=child_span,
    )
    child_span.finish()

    if response.status_code != 200:
        logger.error(
            f'Could not update Snapshot #{snapshot_id} to state UNRESOURCED. \nResponse: {response.content.decode()}.',
        )

    child_span = opentracing.tracer.start_span('send_email', child_of=span)
    try:
        EmailNotifier.snapshot_failure(snapshot, 'update')
    except Exception:
        logger.error(f'Failed to send failure email for Snapshot #{snapshot_id}', exc_info=True)
    child_span.finish()


@app.task
def update_snapshot(snapshot_id: int):
    """
    Helper function that wraps the actual task in a span, meaning we don't have to remember to call .finish
    """
    logger = logging.getLogger('robot.tasks.snapshot.update')
    logger.info('printing some info to tell user that update snapshot called')
    span = opentracing.tracer.start_span('tasks.update_snapshot')
    span.set_tag('snapshot_id', snapshot_id)
    _update_snapshot(snapshot_id, span)
    span.finish()

    # Flush the loggers here so it's not in the span
    utils.flush_logstash()


def _update_snapshot(snapshot_id: int, span: Span):
    """
    Task to update the specified snapshot
    """
    logger = logging.getLogger('robot.tasks.snapshot.update')
    logger.info(f'Commencing update of Snapshot #{snapshot_id}')

    # Read the Snapshot
    child_span = opentracing.tracer.start_span('read_snapshot', child_of=span)
    snapshot = utils.api_read(IAAS.snapshot, snapshot_id, span=child_span)
    child_span.finish()

    # Ensure it is not empty
    if not bool(snapshot):
        # Rely on the utils method for logging
        metrics.snapshot_update_failure()
        span.set_tag('return_reason', 'invalid_snapshot_id')
        return

    # Ensure that the state of the snapshot is still currently UPDATE
    if snapshot['state'] != state.RUNNING_UPDATE:
        logger.warning(
            f'Cancelling update of Snapshot #{snapshot_id}. Expected state to be UPDATE, found {snapshot["state"]}.'
            f'Expected state to be UPDATE, found {snapshot["state"]}.',
        )
        # Return out of this function without doing anyuthing
        span.set_tag('return_reason', 'not_in_valid_state')
        return

    # If all is well and good here, update the Snapshot state to RUNNING_UPDATING and pass the data to the updater
    child_span = opentracing.tracer.start_span('update_to_running_updating', child_of=span)
    response = IAAS.snapshot.partial_update(
        token=Token.get_instance().token,
        pk=snapshot_id,
        data={'state': state.RUNNING_UPDATING},
        span=child_span,
    )
    child_span.finish()

    if response.status_code != 200:
        logger.error(
            f'Could not update Snapshot #{snapshot_id} to state'
            f' RUNNING_UPDATING.\nResponse: {response.content.decode()}.',
        )
        metrics.snapshot_update_failure()
        span.set_tag('return_reason', 'could_not_update_state')
        return

    success: bool = False
    # Read the snapshot VM server to get the server type
    child_span = opentracing.tracer.start_span('read_snapshot_vm_server', child_of=span)
    server = utils.api_read(IAAS.server, snapshot['vm']['server_id'], span=child_span)
    child_span.finish()
    if not bool(server):
        logger.error(f'Could not update snapshot #{snapshot_id} as the associated server was not readable')
        span.set_tag('return_reason', 'server_not_read')
        return
    server_type = server['type']['name']
    # Add server details to snapshot
    snapshot['server_data'] = server
    snapshot['errors'] = []
    child_span = opentracing.tracer.start_span('update', child_of=span)
    try:
        if server_type == 'HyperV':
            success = WindowsSnapshot.update(snapshot, child_span)
            child_span.set_tag('server_type', 'windows')
        elif server_type == 'KVM':
            success = LinuxSnapshot.update(snapshot, child_span)
            child_span.set_tag('server_type', 'linux')
        else:
            error = f'Unsupported server type #{server_type} for Snapshot #{snapshot_id}.'
            logger.error(error)
            snapshot['errors'].append(error)
            child_span.set_tag('server_tag', 'unsupported')
    except Exception as err:
        error = f'An unexpected error occurred when attempting to update the Snapshot #{snapshot_id}.'
        logger.error(error, exc_info=True)
        snapshot['errors'].append(f'{error} Error: {err}')
    child_span.finish()

    span.set_tag('return_reason', f'success: {success}')

    if success:
        logger.info(f'Successfully updated Snapshot #{snapshot_id}.')
        # Update back to RUNNING
        child_span = opentracing.tracer.start_span('update_to_prev_state', child_of=span)
        response = IAAS.snapshot.partial_update(
            token=Token.get_instance().token,
            pk=snapshot_id,
            data={'state': state.RUNNING},
            span=child_span,
        )
        child_span.finish()

        if response.status_code != 200:
            logger.error(
                f'Could not update Snapshot #{snapshot_id} to state {state.RUNNING}.\n'
                f'Response {response.content.decode()}.',
            )
            metrics.snapshot_update_failure()
            return
        metrics.snapshot_update_success()
    else:
        logger.error(f'Failed to update snapshot #{snapshot_id}')
        snapshot.pop('server_data')
        _unresource(snapshot, span)
