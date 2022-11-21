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
from quiescers import VirtualRouter as VirtualRouterQuiescer

__all__ = [
    'quiesce_virtual_router',
]


@app.task
def quiesce_virtual_router(virtual_router_id: int):
    """
    Helper function that wraps the actual task in a span, meaning we don't have to remember to call .finish
    """
    span = opentracing.tracer.start_span('tasks.quiesce_virtual_router')
    span.set_tag('virtual_router_id', virtual_router_id)
    _quiesce_virtual_router(virtual_router_id, span)
    span.finish()

    # Flush the loggers here so it's not in the span
    utils.flush_logstash()


def _quiesce_virtual_router(virtual_router_id: int, span: Span):
    """
    Task to quiesce the specified virtual_router
    """
    logger = logging.getLogger('robot.tasks.virtual_router.quiesce')
    logger.info(f'Commencing quiesce of virtual_router #{virtual_router_id}')

    # Read the virtual_router
    child_span = opentracing.tracer.start_span('read_virtual_router', child_of=span)
    virtual_router = utils.api_read(IAAS.virtual_router, virtual_router_id, span=child_span)
    child_span.finish()

    # Ensure it is not empty
    if not bool(virtual_router):
        # Rely on the utils method for logging
        metrics.virtual_router_quiesce_failure()
        span.set_tag('return_reason', 'invalid_virtual_router_id')
        return

    # Ensure that the state of the virtual_router is still currently SCRUB or QUIESCE
    valid_states = [state.QUIESCE, state.SCRUB]
    if virtual_router['state'] not in valid_states:
        logger.warning(
            f'Cancelling quiesce of virtual_router #{virtual_router_id}. Expected state to be one of {valid_states}, '
            f'found {virtual_router["state"]}.',
        )
        # Return out of this function without doing anything
        span.set_tag('return_reason', 'not_in_valid_state')
        return

    if virtual_router['state'] == state.QUIESCE:
        # Update the state to QUIESCING (12)
        child_span = opentracing.tracer.start_span('update_to_quiescing', child_of=span)
        response = IAAS.virtual_router.partial_update(
            token=Token.get_instance().token,
            pk=virtual_router_id,
            data={'state': state.QUIESCING},
            span=child_span,
        )
        child_span.finish()

        # Ensure the update was successful
        if response.status_code != 200:
            logger.error(
                f'Could not update VM #{virtual_router_id} to the necessary QUIESCING.\n'
                f'Response: {response.content.decode()}.',
            )
            span.set_tag('return_reason', 'could_not_update_state')
            metrics.virtual_router_quiesce_failure()
            # Update to Unresourced?
            return
    else:
        # Update the state to SCRUB_PREP (14)
        child_span = opentracing.tracer.start_span('update_to_scrub_prep', child_of=span)
        response = IAAS.virtual_router.partial_update(
            token=Token.get_instance().token,
            pk=virtual_router_id,
            data={'state': state.SCRUB_PREP},
            span=child_span,
        )
        child_span.finish()
        # Ensure the update was successful
        if response.status_code != 200:
            logger.error(
                f'Could not update VM #{virtual_router_id} to the necessary SCRUB_PREP.\n'
                f'Response: {response.content.decode()}.',
            )
            span.set_tag('return_reason', 'could_not_update_state')
            metrics.virtual_router_quiesce_failure()
            # Update to Unresourced?
            return

    # Do the actual quiescing
    virtual_router['errors'] = []
    success: bool = False
    child_span = opentracing.tracer.start_span('quiesce', child_of=span)
    try:
        success = VirtualRouterQuiescer.quiesce(virtual_router, child_span)
    except Exception as err:
        error = f'An unexpected error occurred when attempting to quiesce virtual_router #{virtual_router_id}.'
        logger.error(error, exc_info=True)
        virtual_router['errors'].append(f'{error} Error: {err}')
    child_span.finish()

    span.set_tag('return_reason', f'success: {success}')

    if success:
        logger.info(f'Successfully quiesced virtual_router #{virtual_router_id}')
        metrics.virtual_router_quiesce_success()
        # Update state, depending on what state the virtual_router is currently in
        # (QUIESCE -> QUIESCED, SCRUB -> SCRUB_QUEUE)
        if virtual_router['state'] == state.QUIESCE:
            child_span = opentracing.tracer.start_span('update_to_quiescing', child_of=span)
            response = IAAS.virtual_router.partial_update(
                token=Token.get_instance().token,
                pk=virtual_router_id,
                data={'state': state.QUIESCED},
                span=child_span,
            )
            child_span.finish()

            if response.status_code != 200:
                logger.error(
                    f'Could not update virtual_router #{virtual_router_id} to state QUIESCED.\n'
                    f'Response: {response.content.decode()}.',
                )
        elif virtual_router['state'] == state.SCRUB:
            child_span = opentracing.tracer.start_span('update_to_deleted', child_of=span)
            response = IAAS.virtual_router.partial_update(
                token=Token.get_instance().token,
                pk=virtual_router_id,
                data={'state': state.SCRUB_QUEUE},
                span=child_span,
            )
            child_span.finish()

            if response.status_code != 200:
                logger.error(
                    f'Could not update virtual_router #{virtual_router_id} to state SCRUB_QUEUE.\n'
                    f'Response: {response.content.decode()}.',
                )
        else:
            logger.error(
                f'virtual_router #{virtual_router_id} has been quiesced despite not being in a valid state. '
                f'Valid states: {valid_states}, virtual_router is in state {virtual_router["state"]}',
            )
    else:
        logger.error(f'Failed to quiesce virtual_router #{virtual_router_id}')
        metrics.virtual_router_quiesce_failure()

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
            EmailNotifier.virtual_router_failure(virtual_router, 'quiesce')
        except Exception:
            logger.error(f'Failed to send build failure email for virtual_router #{virtual_router_id}', exc_info=True)
        child_span.finish()
