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
from updaters import VirtualRouter as VirtualRouterUpdater

__all__ = [
    'debug_logs',
]


@app.task
def debug_logs(virtual_router_id: int):
    """
    Helper function that wraps the actual task in a span, meaning we don't have to remember to call .finish
    """
    span = opentracing.tracer.start_span('tasks.virtual_router.debug_logs')
    span.set_tag('virtual_router_id', virtual_router_id)
    _debug_logs(virtual_router_id, span)
    span.finish()

    # Flush the loggers here so it's not in the span
    utils.flush_logstash()


def _debug_logs(virtual_router_id: int, span: Span):
    """
    Task to change the debug state of firewall rule logs of the specified virtual_router
    """
    logger = logging.getLogger('robot.tasks.virtual_router.debug_logs')
    logger.info(
        f'Commencing update of virtual_router #{virtual_router_id} to disable the debug status of firewall logs',
    )

    # Read the virtual_router
    child_span = opentracing.tracer.start_span('read_virtual_router', child_of=span)
    virtual_router = utils.api_read(IAAS.virtual_router, virtual_router_id, span=child_span)
    child_span.finish()

    # Ensure it is not empty
    if not bool(virtual_router):
        # Rely on the utils method for logging
        metrics.virtual_router_update_failure()
        span.set_tag('return_reason', 'invalid_virtual_router_id')
        return

    # Ensure that the state of the virtual_router is in Running state
    if virtual_router['state'] != state.RUNNING:
        logger.warning(
            f'Cancelling update of virtual_router #{virtual_router_id} to disable the debug status of firewall logs.'
            f'Expected state to be RUNNING, found {virtual_router["state"]}.',
        )
        # Return out of this function without doing anything as it will be handled by other tasks
        span.set_tag('return_reason', 'not_in_valid_state')
        return

    # No need to update the state of virtual_router to updating as this is not actual UPDATE task.

    # check if any firewall rule debug_logging needs to be reset and
    # change the debug_logging to false for all firewall rules
    debug: bool = False
    for firewall in virtual_router['firewall_rules']:
        if firewall['debug_logging']:
            debug = True
            firewall['debug_logging'] = False

    if not debug:
        logger.info(f'No firewall rule debug_logging needs to reset for virtual_router #{virtual_router_id}')
        return

    virtual_router['errors'] = []
    success: bool = False
    child_span = opentracing.tracer.start_span('update', child_of=span)
    try:
        success = VirtualRouterUpdater.update(virtual_router, child_span)
    except Exception as err:
        error = (
            f'An unexpected error occurred when attempting to disable the debug status of firewall logs for '
            f'virtual_router #{virtual_router_id}.',
        )
        logger.error(error, exc_info=True)
        virtual_router['errors'].append(f'{error} Error: {err}')
    child_span.finish()

    span.set_tag('return_reason', f'success: {success}')

    if success:
        logger.info(f'Successfully disabled the debug status of firewall logs virtual_router #{virtual_router_id}')
        metrics.virtual_router_update_success()

        # check the state of virtual_router in DB before changing the debug status of firewall rules of virtual_router.
        child_span = opentracing.tracer.start_span('read_virtual_router', child_of=span)
        virtual_router = utils.api_read(IAAS.virtual_router, virtual_router_id, span=child_span)
        child_span.finish()

        # Ensure it is not none
        if virtual_router is None:
            return

        # Ensure that the state of the virtual_router is in Running state otherwise no need to change the debug status
        # as the next tasks would take care of this.
        if virtual_router['state'] == state.RUNNING:
            child_span = opentracing.tracer.start_span('debug_to_false', child_of=span)
            response = IAAS.virtual_router.partial_update(
                token=Token.get_instance().token,
                pk=virtual_router_id,
                data={'debug': False},
                span=child_span,
            )
            child_span.finish()

            if response.status_code != 200:
                logger.error(
                    f'Could not reset the debug status of firewall logs of virtual_router #{virtual_router_id}.\n'
                    f'Response: {response.content.decode()}.',
                )
    else:
        logger.error(
            f'Failed to disable the debug status of firewall logs of virtual_router #{virtual_router_id} on router.',
        )
        metrics.virtual_router_update_failure()

        child_span = opentracing.tracer.start_span('send_email', child_of=span)
        try:
            EmailNotifier.virtual_router_failure(virtual_router, 'update')
        except Exception:
            logger.error(
                f'Failed to send update failure email for virtual_router #{virtual_router_id}',
                exc_info=True,
            )
        child_span.finish()
