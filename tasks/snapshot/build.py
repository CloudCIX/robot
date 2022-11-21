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
from builders import (
    LinuxSnapshot,
    WindowsSnapshot,
)
from celery_app import app
from cloudcix_token import Token
from email_notifier import EmailNotifier

__all__ = [
    'build_snapshot',
]


def _unresource(snapshot: Dict[str, Any], span: Span):
    """
    unresource the specified snapshot because something went wrong
    """
    snapshot_id = snapshot['id']
    metrics.snapshot_build_failure()

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
        logging.getLogger('robot.tasks.snapshot.build').error(
            f'could not update Snapshot #{snapshot_id} to state UNRESOURCED. \nResponse: {response.content.decode()}.',
        )
    child_span = opentracing.tracer.start_span('send_email', child_of=span)
    try:
        EmailNotifier.snapshot_build_failure(snapshot)
    except Exception:
        logging.getLogger('robot.tasks.snapshot.build').error(
            f'Failed to send build failure email for Snapshot #{snapshot_id}',
            exc_info=True,
        )
    child_span.finish()


@app.task
def build_snapshot(snapshot_id: int):
    """
    Helper function that wraps the actual task in a span, meaning we don't have to remember to call .finish
    """
    span = opentracing.tracer.start_span('tasks.build_snapshot')
    span.set_tag('snapshot_id', snapshot_id)
    _build_snapshot(snapshot_id, span)
    span.finish()

    # Flush the loggers here so it's not in the span
    utils.flush_logstash()


def _build_snapshot(snapshot_id: int, span: Span):
    """
    Task to build the specified snapshot
    """
    logger = logging.getLogger('robot.tasks.snapshot.build')
    logger.info(f'Commencing build of Snapshot #{snapshot_id}.')

    # Read the Snapshot
    child_span = opentracing.tracer.start_span('read_snapshot', child_of=span)
    snapshot = utils.api_read(IAAS.snapshot, snapshot_id, span=child_span)
    child_span.finish()

    # Ensure it is not empty
    if not bool(snapshot):
        # Reply on the utils method for logging
        metrics.snapshot_build_failure()
        span.set_tag('return_reason', 'invalid_snapshot_id')
        return

    # Ensure that the state of the snapshot is still currently REQUESTED (it hasn't been picked up by another runner)
    if snapshot['state'] != state.REQUESTED:
        logger.warning(
            f'Cancelling build of Snapshot #{snapshot_id}. '
            'Expected to be {state.REQUESTED}, found {snapshot["state"]},',
        )
        # Return out of this function without doing anything as if was already handled
        span.set_tag('return_reason', 'not_in_correct_state')
        return

    # catch all the errors if any
    snapshot['errors'] = []

    # If all is well and good here, update the Snapshot state to BUILDING and pass the data to the builder
    response = IAAS.snapshot.partial_update(
        token=Token.get_instance().token,
        pk=snapshot_id,
        data={'state': state.BUILDING},
        span=child_span,
    )
    child_span.finish()

    if response.status_code != 200:
        logger.error(
            f'Could not update Snapshot #{snapshot_id} to state BUILDING. \nResponse: {response.content.decode()}.',
        )
        metrics.snapshot_build_failure()
        span.set_tag('return_reason', 'could_not_update_state')
        return

    # Read the Snapshot's VM server to get the server type
    child_span = opentracing.tracer.start_span('read_snapshot_vm_server', child_of=span)
    server = utils.api_read(IAAS.server, snapshot['vm']['server_id'], span=child_span)
    child_span.finish()
    if not bool(server):
        logger.error(f'Could not build Snapshot #{snapshot_id} as the associated server was not readable')
        _unresource(snapshot, span)
        span.set_tag('return_reason', 'server_not_read')
        return
    server_type = server['type']['name']
    # add server detaisl to the snapshot
    snapshot['server_data'] = server

    # Call the appropriate builder
    success: bool = False
    child_span = opentracing.tracer.start_span('build', child_of=span)
    try:
        if server_type == 'HyperV':
            success = WindowsSnapshot.build(snapshot, child_span)
            child_span.set_tag('server_type', 'snapshot')
        elif server_type == 'KVM':
            success = LinuxSnapshot.build(snapshot, child_span)
            child_span.set_tag('server_type', 'snapshot')
        else:
            error = f'Unsupported server type #{server_type} for snapshot #{snapshot_id}'
            logger.error(error, exc_info=True)
            snapshot['errors'].append(error)
            child_span.set_tag('server_type', 'unsupported')
    except Exception as err:
        error = f'An unexpected error occured when attempting to build Snapshot #{snapshot_id}.'
        logger.error(error, exc_info=True)
        snapshot['errors'].append(f'{error} Error: {err}')
    child_span.finish()

    span.set_tag('return_reason', f'success: {success}')

    if success:
        logger.info(f'Successfully built Snapshot #{snapshot_id}')

        # Update state to RUNNING in the API
        child_span = opentracing.tracer.start_span('update_to_running', child_of=span)
        response = IAAS.snapshot.partial_update(
            token=Token.get_instance().token,
            pk=snapshot_id,
            data={'state': state.RUNNING},
            span=child_span,
        )
        child_span.finish()

        if response.status_code != 200:
            logger.error(
                f'Could not update Snapshot #{snapshot_id} to state RUNNING. Response: {response.content.decode()}.',
            )

        # Don't send an email for a successfully created snapshot
        # If later this feature is added, code goes here
    else:
        logger.error(f'Failed to build Snapshot #{snapshot_id}')
        snapshot.pop('server_data')
        _unresource(snapshot, span)
