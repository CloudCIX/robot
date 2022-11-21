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
from scrubbers import (
    LinuxSnapshot,
    WindowsSnapshot,
)


__all__ = [
    'scrub_snapshot',
]


def _unresource(snapshot: Dict[str, Any], span: Span):
    """
    unresource the specified snapshot because something went wrong
    """
    logger = logging.getLogger('robot.tasks.snapshot.scrub')
    snapshot_id = snapshot['id']
    metrics.snapshot_scrub_failure()

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
        EmailNotifier.snapshot_failure(snapshot, 'scrub')
    except Exception:
        logger.error(f'Failed to send failure email for Snapshot #{snapshot_id}', exc_info=True)
    child_span.finish()


@app.task
def scrub_snapshot(snapshot_id: int):
    """
    Helper function that wraps the actual task in a span, meaning we don't have to remember to call .finish
    """
    span = opentracing.tracer.start_span('tasks.scrub_snapshot')
    span.set_tag('snapshot_id', snapshot_id)
    _scrub_snapshot(snapshot_id, span)
    span.finish()
    # Flush the loggers here so it's not in the span
    utils.flush_logstash()


def _scrub_snapshot(snapshot_id: int, span: Span):
    """
    Task to scrub the specified snapshot
    """
    logger = logging.getLogger('robot.tasks.snapshot.scrub')
    logger.info(f'Commencing scrub of snapshot #{snapshot_id}')

    # Read the Snapshot
    # Don't use utils so we can check the response code
    child_span = opentracing.tracer.start_span('read_snapshot', child_of=span)
    response = IAAS.snapshot.read(
        token=Token.get_instance().token,
        pk=snapshot_id,
    )
    child_span.finish()

    if response.status_code == 404:
        logger.info(f'Received scrub task for Snapshot #{snapshot_id} but it was already deleted from the API')
        span.set_tag('return_reason', 'already_deleted')
        return
    elif response.status_code != 200:
        logger.error(
            f'HTTP {response.status_code} error occured when attempting to fetch snapshot #{snapshot_id}.\n'
            f'Response Text: {response.content.decode()}',
        )
        span.set_tag('return_reason', 'invalid_snapshot_id')
        return
    snapshot = response.json()['content']

    # Ensure that the state of the snapshot is still currently SCRUB
    if snapshot['state'] != state.SCRUB:
        logger.warning(
            f'Cancelling scrub of snapshot #{snapshot_id}. \
            Expected state to be SCRUB found {snapshot["state"]}.',
        )
        # Return out this without doing anything
        span.set_tag('return_reason', 'not_in_valid_state')
        return

    # If all is well and good here, update the Snapshot state to SCRUBBING and pass the data to the scrubber
    child_span = opentracing.tracer.start_span('update_to_scrubbing', child_of=span)
    response = IAAS.snapshot.partial_update(
        token=Token.get_instance().token,
        pk=snapshot_id,
        data={
            'state': state.SCRUBBING,
        },
        span=child_span,
    )
    child_span.finish()

    if response.status_code != 200:
        logger.error(f'Could not update Snapshot #{snapshot_id} to state \
        SCRUBBING.\nResponse: {response.content.decode()}.')
        metrics.snapshot_update_failure()
        span.set_tag('return_reason', 'could_not_update_state')

    # Read the snapshot vm server to get the server type
    child_span = opentracing.tracer.start_span('read_snapshot_vm_server', child_of=span)
    server = utils.api_read(IAAS.server, snapshot['vm']['server_id'], span=child_span)
    child_span.finish()
    if not bool(server):
        logger.error(f'Could not scrub snapshot #{snapshot_id} as the associated server was not readable')
        span.set_tag('return_reason', 'server_not_read')
        return
    server_type = server['type']['name']
    # add server details to snapshot
    snapshot['server_data'] = server

    snapshot['errors'] = []
    success: bool = False
    child_span = opentracing.tracer.start_span('scrub', child_of=span)
    try:
        if server_type == 'HyperV':
            success = WindowsSnapshot.scrub(snapshot, child_span)
            child_span.set_tag('server_type', 'windows')
        elif server_type == 'KVM':
            success = LinuxSnapshot.scrub(snapshot, child_span)
            child_span.set_tag('server_type', 'linux')
        else:
            error = f'Unsupported server type #{server_type} for snapshot #{snapshot_id}.'
            logger.error(error)
            snapshot['errors'].append(error)
            child_span.set_tag('server_type', 'unsupported')
    except Exception as err:
        error = f'An unexpected error occured when attempting to scrub Snapshot #{snapshot_id}.'
        logger.error(error, exc_info=True)
        snapshot['errors'].append(f'{error} Error: {err}')
    child_span.finish()

    span.set_tag('return_reason', f'success: {success}')

    if success:
        logger.info(f'Successfully scrubbed snapshot #{snapshot_id} from hardware.')
        metrics.snapshot_scrub_success()
        # Do API deletions
        logger.debug(f'Closing Snapshot #{snapshot_id} in IAAS')

        child_span = opentracing.tracer.start_span('closing_snapshot_from', child_of=span)
        response = IAAS.snapshot.partial_update(
            token=Token.get_instance().token,
            pk=snapshot_id,
            data={
                'state': state.CLOSED,
            },
            span=child_span,
        )
        child_span.finish()
        if response.status_code != 200:
            logger.error(
                f'HTTP {response.status_code} response received when attempting to close Snapshot #{snapshot_id}:\n'
                f'Response Text: {response.content.decode()}.',
            )
            return
        logger.info(f'Successfully closed Snapshot #{snapshot_id}')
    else:
        logger.error(f'Failed to scrub Snapshot #{snapshot_id}')
        snapshot.pop('server_data')
        _unresource(snapshot, span)
