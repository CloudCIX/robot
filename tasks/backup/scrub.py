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
    LinuxBackup,
    WindowsBackup,
)


__all__ = [
    'scrub_backup',
]


def _unresource(backup: Dict[str, Any], span: Span):
    """
    unresource the specified backup because something went wrong
    """
    logger = logging.getLogger('robot.tasks.backup.scrub')
    backup_id = backup['id']
    metrics.backup_scrub_failure()

    # Update state to UNRESOURCED in the API
    child_span = opentracing.tracer.start_span('update_to_unresourced', child_of=span)
    response = IAAS.backup.partial_update(
        token=Token.get_instance().token,
        pk=backup_id,
        data={'state': state.UNRESOURCED},
        span=child_span,
    )
    child_span.finish()

    if response.status_code != 200:
        logger.error(
            f'Could not update Backup #{backup_id} to state UNRESOURCED. \nResponse: {response.content.decode()}.',
        )

    child_span = opentracing.tracer.start_span('send_email', child_of=span)
    try:
        EmailNotifier.backup_failure(backup, 'scrub')
    except Exception:
        logger.error(f'Failed to send failure email for Backup #{backup_id}', exc_info=True)
    child_span.finish()


@app.task
def scrub_backup(backup_id: int):
    """
    Helper function that wraps the actual task in a span, meaning we don't have to remember to call .finish
    """
    span = opentracing.tracer.start_span('tasks.scrub_backup')
    span.set_tag('backup_id', backup_id)
    _scrub_backup(backup_id, span)
    span.finish()
    # Flush the loggers here so it's not in the span
    utils.flush_logstash()


def _scrub_backup(backup_id: int, span: Span):
    """
    Task to scrub the specified backup
    """
    logger = logging.getLogger('robot.tasks.backup.scrub')
    logger.info(f'Commencing scrub of backup #{backup_id}')

    # Read the Backup
    # Don't use utils so we can check the response code
    child_span = opentracing.tracer.start_span('read_backup', child_of=span)
    response = IAAS.backup.read(
        token=Token.get_instance().token,
        pk=backup_id,
    )
    child_span.finish()

    if response.status_code == 404:
        logger.info(f'Received scrub task for Backup #{backup_id} but it was already deleted from the API')
        span.set_tag('return_reason', 'already_deleted')
        return
    elif response.status_code != 200:
        logger.error(
            f'HTTP {response.status_code} error occured when attempting to fetch backup #{backup_id}.\n'
            f'Response Text: {response.content.decode()}',
        )
        span.set_tag('return_reason', 'invalid_backup_id')
        return
    backup = response.json()['content']

    # Ensure that the state of the backup is still currently SCRUB
    if backup['state'] != state.SCRUB:
        logger.warning(
            f'Cancelling scrub of backup #{backup_id}. \
            Expected state to be SCRUB found {backup["state"]}.',
        )
        # Return out this without doing anything
        span.set_tag('return_reason', 'not_in_valid_state')
        return

    # If all is well and good here, update the Backup state to SCRUBBING and pass the data to the scrubber
    child_span = opentracing.tracer.start_span('update_to_scrubbing', child_of=span)
    response = IAAS.backup.partial_update(
        token=Token.get_instance().token,
        pk=backup_id,
        data={
            'state': state.SCRUBBING,
        },
        span=child_span,
    )
    child_span.finish()

    if response.status_code != 200:
        logger.error(f'Could not update Backup #{backup_id} to state \
        SCRUBBING.\nResponse: {response.content.decode()}.')
        metrics.backup_update_failure()
        span.set_tag('return_reason', 'could_not_update_state')

    # Read the backup vm server to get the server type
    child_span = opentracing.tracer.start_span('read_backup_vm_server', child_of=span)
    server = utils.api_read(IAAS.server, backup['vm']['server_id'], span=child_span)
    child_span.finish()
    if not bool(server):
        logger.error(f'Could not scrub backup #{backup_id} as the associated server was not readable')
        span.set_tag('return_reason', 'server_not_read')
        return
    server_type = server['type']['name']
    # add server details to backup
    backup['server_data'] = server

    backup['errors'] = []
    success: bool = False
    child_span = opentracing.tracer.start_span('scrub', child_of=span)
    try:
        if server_type == 'HyperV':
            success = WindowsBackup.scrub(backup, child_span)
            child_span.set_tag('server_type', 'windows')
        elif server_type == 'KVM':
            success = LinuxBackup.scrub(backup, child_span)
            child_span.set_tag('server_type', 'linux')
        else:
            error = f'Unsupported server type #{server_type} for backup #{backup_id}.'
            logger.error(error)
            backup['errors'].append(error)
            child_span.set_tag('server_type', 'unsupported')
    except Exception as err:
        error = f'An unexpected error occured when attempting to scrub Backup #{backup_id}.'
        logger.error(error, exc_info=True)
        backup['errors'].append(f'{error} Error: {err}')
    child_span.finish()

    span.set_tag('return_reason', f'success: {success}')

    if success:
        logger.info(f'Successfully scrubbed backup #{backup_id} from hardware.')
        metrics.backup_scrub_success()
        # Do API deletions
        logger.debug(f'Closing Backup #{backup_id} in IAAS')

        child_span = opentracing.tracer.start_span('closing_backup_from', child_of=span)
        response = IAAS.backup.partial_update(
            token=Token.get_instance().token,
            pk=backup_id,
            data={
                'state': state.CLOSED,
            },
            span=child_span,
        )
        child_span.finish()
        if response.status_code != 200:
            logger.error(
                f'HTTP {response.status_code} response received when attempting to close Backup #{backup_id}:\n'
                f'Response Text: {response.content.decode()}.',
            )
            return
        logger.info(f'Successfully closed Backup #{backup_id}')
    else:
        logger.error(f'Failed to scrub Backup #{backup_id}')
        backup.pop('server_data')
        _unresource(backup, span)
