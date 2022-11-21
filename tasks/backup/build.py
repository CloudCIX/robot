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
    LinuxBackup,
    WindowsBackup,
)
from celery_app import app
from cloudcix_token import Token
from email_notifier import EmailNotifier

__all__ = [
    'build_backup',
]


def _unresource(backup: Dict[str, Any], span: Span):
    """
    unresource the specified backup because something went wrong
    """
    backup_id = backup['id']
    metrics.backup_build_failure()

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
        logging.getLogger('robot.tasks.backup.build').error(
            f'could not update Backup #{backup_id} to state UNRESOURCED. \nResponse: {response.content.decode()}.',
        )
    child_span = opentracing.tracer.start_span('send_email', child_of=span)
    try:
        EmailNotifier.backup_build_failure(backup)
    except Exception:
        logging.getLogger('robot.tasks.backup.build').error(
            f'Failed to send build failure email for Backup #{backup_id}',
            exc_info=True,
        )
    child_span.finish()


@app.task
def build_backup(backup_id: int):
    """
    Helper function that wraps the actual task in a span, meaning we don't have to remember to call .finish
    """
    span = opentracing.tracer.start_span('tasks.build_backup')
    span.set_tag('backup_id', backup_id)
    _build_backup(backup_id, span)
    span.finish()

    # Flush the loggers here so it's not in the span
    utils.flush_logstash()


def _build_backup(backup_id: int, span: Span):
    """
    Task to build the specified backup
    """
    logger = logging.getLogger('robot.tasks.backup.build')
    logger.info(f'Commencing build of Backup #{backup_id}.')

    # Read the Backup
    child_span = opentracing.tracer.start_span('read_backup', child_of=span)
    backup = utils.api_read(IAAS.backup, backup_id, span=child_span)
    child_span.finish()

    # Ensure it is not empty
    if not bool(backup):
        # Reply on the utils method for logging
        metrics.backup_build_failure()
        span.set_tag('return_reason', 'invalid_backup_id')
        return

    # Ensure that the state of the backup is still currently REQUESTED (it hasn't been picked up by another runner)
    if backup['state'] != state.REQUESTED:
        logger.warning(
            f'Cancelling build of Backup #{backup_id}. '
            'Expected to be {state.REQUESTED}, found {backup["state"]},',
        )
        # Return out of this function without doing anything as if was already handled
        span.set_tag('return_reason', 'not_in_correct_state')
        return

    # catch all the errors if any
    backup['errors'] = []

    # If all is well and good here, update the Backup state to BUILDING and pass the data to the builder
    response = IAAS.backup.partial_update(
        token=Token.get_instance().token,
        pk=backup_id,
        data={'state': state.BUILDING},
        span=child_span,
    )
    child_span.finish()

    if response.status_code != 200:
        logger.error(
            f'Could not update Backup #{backup_id} to state BUILDING. \nResponse: {response.content.decode()}.',
        )
        metrics.backup_build_failure()
        span.set_tag('return_reason', 'could_not_update_state')
        return

    # Read the Backup's VM server to get the server type
    child_span = opentracing.tracer.start_span('read_backup_vm_server', child_of=span)
    server = utils.api_read(IAAS.server, backup['vm']['server_id'], span=child_span)
    child_span.finish()
    if not bool(server):
        logger.error(f'Could not build Backup #{backup_id} as the associated server was not readable')
        _unresource(backup, span)
        span.set_tag('return_reason', 'server_not_read')
        return
    server_type = server['type']['name']
    # add server detaisl to the backup
    backup['server_data'] = server

    # Call the appropriate builder
    success: bool = False
    child_span = opentracing.tracer.start_span('build', child_of=span)
    try:
        if server_type == 'HyperV':
            success = WindowsBackup.build(backup, child_span)
            child_span.set_tag('server_type', 'backup')
        elif server_type == 'KVM':
            success = LinuxBackup.build(backup, child_span)
            child_span.set_tag('server_type', 'backup')
        else:
            error = f'Unsupported server type #{server_type} for backup #{backup_id}'
            logger.error(error, exc_info=True)
            backup['errors'].append(error)
            child_span.set_tag('server_type', 'unsupported')
    except Exception as err:
        error = f'An unexpected error occured when attempting to build Backup #{backup_id}.'
        logger.error(error, exc_info=True)
        backup['errors'].append(f'{error} Error: {err}')
    child_span.finish()

    span.set_tag('return_reason', f'success: {success}')

    if success:
        logger.info(f'Successfully built Backup #{backup_id}')

        # Update state to RUNNING in the API
        child_span = opentracing.tracer.start_span('update_to_running', child_of=span)
        response = IAAS.backup.partial_update(
            token=Token.get_instance().token,
            pk=backup_id,
            data={'state': state.RUNNING, 'time_valid': backup['time_valid']},
            span=child_span,
        )
        child_span.finish()

        if response.status_code != 200:
            logger.error(
                f'Could not update Backup #{backup_id} to state RUNNING. Response: {response.content.decode()}.',
            )

        # Don't send an email for a successfully created backup
        # If later this feature is added, code goes here
    else:
        logger.error(f'Failed to build Backup #{backup_id}')
        backup.pop('server_data')
        _unresource(backup, span)
