# stdlib
import logging
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
from restarters import VirtualRouter as VirtualRouterRestarter

__all__ = [
    'restart_virtual_router',
]


@app.task
def restart_virtual_router(virtual_router_id: int):
    """
    Helper function that wraps the actual task in a span, meaning we don't have to remember to call .finish
    """
    span = opentracing.tracer.start_span('tasks.restart_virtual_router')
    span.set_tag('virtual_router_id', virtual_router_id)
    _restart_virtual_router(virtual_router_id, span)
    span.finish()

    # Flush the loggers here so it's not in the span
    utils.flush_logstash()


def _restart_virtual_router(virtual_router_id: int, span: Span):
    """
    Task to restart the specified virtual_router
    """
    logger = logging.getLogger('robot.tasks.virtual_router.restart')
    logger.info(f'Commencing restart of virtual_router #{virtual_router_id}')

    # Read the virtual_router
    child_span = opentracing.tracer.start_span('read_virtual_router', child_of=span)
    virtual_router = utils.api_read(IAAS.virtual_router, virtual_router_id, span=child_span)
    child_span.finish()

    # Ensure it is not empty
    if not bool(virtual_router):
        # Rely on the utils method for logging
        metrics.virtual_router_restart_failure()
        span.set_tag('return_reason', 'invalid_virtual_router_id')
        return

    # Ensure that the state of the virtual_router is still currently RESTART
    if virtual_router['state'] != state.RESTART:
        logger.warning(
            f'Cancelling restart of virtual_router #{virtual_router_id}. '
            f'Expected state to be RESTART, found {virtual_router["state"]}.',
        )
        # Return out of this function without doing anything
        span.set_tag('return_reason', 'not_in_valid_state')
        return

    # Update to intermediate state here (RESTARTING - 13)
    child_span = opentracing.tracer.start_span('update_to_restarting', child_of=span)
    response = IAAS.virtual_router.partial_update(
        token=Token.get_instance().token,
        pk=virtual_router_id,
        data={'state': state.RESTARTING},
        span=child_span,
    )
    child_span.finish()

    # Ensure the update was successful
    if response.status_code != 200:
        logger.error(
            f'Could not update VM #{virtual_router_id} to the necessary RESTARTING.\n'
            f'Response: {response.content.decode()}.',
        )
        span.set_tag('return_reason', 'could_not_update_state')
        metrics.virtual_router_restart_failure()
        # Update to Unresourced?
        return

    # Do the actual restarting
    virtual_router['errors'] = []
    success: bool = False
    child_span = opentracing.tracer.start_span('restart', child_of=span)
    try:
        success = VirtualRouterRestarter.restart(virtual_router, child_span)
    except Exception as err:
        error = f'An unexpected error occurred when attempting to restart virtual_router #{virtual_router_id}.'
        logger.error(error, exc_info=True)
        virtual_router['errors'].append(f'{error} Error: {err}')
    child_span.finish()

    span.set_tag('return_reason', f'success: {success}')

    if success:
        logger.info(f'Successfully restarted virtual_router #{virtual_router_id}')
        metrics.virtual_router_restart_success()

        # Update state to RUNNING in the API
        child_span = opentracing.tracer.start_span('update_to_running', child_of=span)
        response = IAAS.virtual_router.partial_update(
            token=Token.get_instance().token,
            pk=virtual_router_id,
            data={'state': state.RUNNING},
            span=child_span,
        )
        child_span.finish()

        if response.status_code != 200:
            logger.error(
                f'Could not update virtual_router #{virtual_router_id} to state RUNNING.\n'
                f'Response: {response.content.decode()}.',
            )
    else:
        logger.error(f'Failed to restart virtual_router #{virtual_router_id}')
        metrics.virtual_router_restart_failure()

        # Update state to UNRESOURCED in the API
        child_span = opentracing.tracer.start_span('update_to_unresourced', child_of=span)
        response = IAAS.virtual_router.partial_update(
            token=Token.get_instance().token,
            pk=virtual_router_id,
            data={'state': state.UNRESOURCED},
            span=child_span,
        )
        child_span.finish()

        if response.status_code != 200:
            logger.error(
                f'Could not update virtual_router #{virtual_router_id} to state UNRESOURCED.\n'
                f'Response: {response.content.decode()}.',
            )

        child_span = opentracing.tracer.start_span('send_email', child_of=span)
        try:
            EmailNotifier.virtual_router_failure(virtual_router, 'restart')
        except Exception:
            logger.error(f'Failed to send build failure email for virtual_router #{virtual_router_id}', exc_info=True)
        child_span.finish()
