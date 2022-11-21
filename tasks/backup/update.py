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
from updaters.backup import (
    Linux as LinuxBackup,
    Windows as WindowsBackup,
)


__all__ = [
    'update_backup',
]


def _unresource(backup: Dict[str, Any], span: Span):
    """
    unresource the specified backup because something went wrong
    """
    logger = logging.getLogger('robot.tasks.backup.update')
    backup_id = backup['id']
    # Send failure metric
    metrics.backup_update_failure()

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
        EmailNotifier.backup_failure(backup, 'update')
    except Exception:
        logger.error(f'Failed to send failure email for Backup #{backup_id}', exc_info=True)
    child_span.finish()


@app.task
def update_backup(backup_id: int):
    """
    Helper function that wraps the actual task in a span, meaning we don't have to remember to call .finish
    """
    logger = logging.getLogger('robot.tasks.backup.update')
    logger.info('printing some info to tell user that update backup called')
    span = opentracing.tracer.start_span('tasks.update_backup')
    span.set_tag('backup_id', backup_id)
    _update_backup(backup_id, span)
    span.finish()

    # Flush the loggers here so it's not in the span
    utils.flush_logstash()


def _update_backup(backup_id: int, span: Span):
    """
    Task to update the specified backup
    """
    logger = logging.getLogger('robot.tasks.backup.update')
    logger.info(f'Commencing update of Backup #{backup_id}')

    # Read the Backup
    child_span = opentracing.tracer.start_span('read_backup', child_of=span)
    backup = utils.api_read(IAAS.backup, backup_id, span=child_span)
    child_span.finish()

    # Ensure it is not empty
    if not bool(backup):
        # Rely on the utils method for logging
        metrics.backup_update_failure()
        span.set_tag('return_reason', 'invalid_backup_id')
        return

    # Ensure that the state of the backup is still currently UPDATE
    if backup['state'] != state.RUNNING_UPDATE:
        logger.warning(
            f'Cancelling update of Backup #{backup_id}. Expected state to be UPDATE, found {backup["state"]}.'
            f'Expected state to be UPDATE, found {backup["state"]}.',
        )
        # Return out of this function without doing anyuthing
        span.set_tag('return_reason', 'not_in_valid_state')
        return

    # If all is well and good here, update the Backup state to RUNNING_UPDATING and pass the data to the updater
    child_span = opentracing.tracer.start_span('update_to_running_updating', child_of=span)
    response = IAAS.backup.partial_update(
        token=Token.get_instance().token,
        pk=backup_id,
        data={'state': state.RUNNING_UPDATING},
        span=child_span,
    )
    child_span.finish()

    if response.status_code != 200:
        logger.error(
            f'Could not update Backup #{backup_id} to state'
            f' RUNNING_UPDATING.\nResponse: {response.content.decode()}.',
        )
        metrics.backup_update_failure()
        span.set_tag('return_reason', 'could_not_update_state')
        return

    success: bool = False
    # Read the backup VM server to get the server type
    child_span = opentracing.tracer.start_span('read_backup_vm_server', child_of=span)
    server = utils.api_read(IAAS.server, backup['vm']['server_id'], span=child_span)
    child_span.finish()
    if not bool(server):
        logger.error(f'Could not update backup #{backup_id} as the associated server was not readable')
        span.set_tag('return_reason', 'server_not_read')
        return
    server_type = server['type']['name']
    # Add server details to backup
    backup['server_data'] = server
    backup['errors'] = []
    child_span = opentracing.tracer.start_span('update', child_of=span)
    try:
        if server_type == 'HyperV':
            success = WindowsBackup.update(backup, child_span)
            child_span.set_tag('server_type', 'windows')
        elif server_type == 'KVM':
            success = LinuxBackup.update(backup, child_span)
            child_span.set_tag('server_type', 'linux')
        else:
            error = f'Unsupported server type #{server_type} for Backup #{backup_id}.'
            logger.error(error)
            backup['errors'].append(error)
            child_span.set_tag('server_tag', 'unsupported')
    except Exception as err:
        error = f'An unexpected error occurred when attempting to update the Backup #{backup_id}.'
        logger.error(error, exc_info=True)
        backup['errors'].append(f'{error} Error: {err}')
    child_span.finish()

    span.set_tag('return_reason', f'success: {success}')

    if success:
        logger.info(f'Successfully updated Backup #{backup_id}.')
        # Update back to RUNNING
        child_span = opentracing.tracer.start_span('update_to_prev_state', child_of=span)
        response = IAAS.backup.partial_update(
            token=Token.get_instance().token,
            pk=backup_id,
            data={'state': state.RUNNING},
            span=child_span,
        )
        child_span.finish()

        if response.status_code != 200:
            logger.error(
                f'Could not update Backup #{backup_id} to state {state.RUNNING}.\n'
                f'Response {response.content.decode()}.',
            )
            metrics.backup_update_failure()
            return
        metrics.backup_update_success()
    else:
        logger.error(f'Failed to update backup #{backup_id}')
        backup.pop('server_data')
        _unresource(backup, span)
