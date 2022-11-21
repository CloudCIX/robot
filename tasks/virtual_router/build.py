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
from builders import VirtualRouter as VirtualRouterBuilder
from celery_app import app
from cloudcix_token import Token
from email_notifier import EmailNotifier


__all__ = [
    'build_virtual_router',
]


@app.task
def build_virtual_router(virtual_router_id: int):
    """
    Helper function that wraps the actual task in a span, meaning we don't have to remember to call .finish
    """
    span = opentracing.tracer.start_span('tasks.build_virtual_router')
    span.set_tag('virtual_router_id', virtual_router_id)
    _build_virtual_router(virtual_router_id, span)
    span.finish()
    # Flush the loggers here so it's not in the span
    utils.flush_logstash()


def _build_virtual_router(virtual_router_id: int, span: Span):
    """
    Task to build the specified virtual_router
    """
    logger = logging.getLogger('robot.tasks.virtual_router.build')
    logger.info(f'Commencing build of virtual_router #{virtual_router_id}')

    # Read the virtual_router
    child_span = opentracing.tracer.start_span('read_virtual_router', child_of=span)
    virtual_router = utils.api_read(IAAS.virtual_router, virtual_router_id, span=child_span)
    child_span.finish()

    # Ensure it is not empty
    if not bool(virtual_router):
        # Rely on the utils method for logging
        metrics.virtual_router_build_failure()
        span.set_tag('return_reason', 'invalid_virtual_router_id')
        return

    # Ensure that the state of the virtual_router is still currently REQUESTED
    # (it hasn't been picked up by another runner)
    if virtual_router['state'] != state.REQUESTED:
        logger.warning(
            f'Cancelling build of virtual_router #{virtual_router_id}. '
            f'Expected state to be {state.REQUESTED}, found {virtual_router["state"]}.',
        )
        # Return out of this function without doing anything as it was already handled
        span.set_tag('return_reason', 'not_in_valid_state')
        return

    # catch all the errors if any
    virtual_router['errors'] = []

    # If all is well and good here, update the virtual_router state to BUILDING and pass the data to the builder
    child_span = opentracing.tracer.start_span('update_to_building', child_of=span)
    response = IAAS.virtual_router.partial_update(
        token=Token.get_instance().token,
        pk=virtual_router_id,
        data={'state': state.BUILDING},
        span=child_span,
    )
    child_span.finish()

    if response.status_code != 200:
        logger.error(
            f'Could not update virtual_router #{virtual_router_id} to state BUILDING. '
            f'With error code {response.status_code}.\nResponse: {response.content.decode()}.',
        )
        metrics.virtual_router_build_failure()
        span.set_tag('return_reason', 'could_not_update_state')
        return

    success: bool = False
    child_span = opentracing.tracer.start_span('build', child_of=span)
    try:
        success = VirtualRouterBuilder.build(virtual_router, child_span)
    except Exception:
        logger.error(
            f'An unexpected error occurred when attempting to build virtual_router #{virtual_router_id}',
            exc_info=True,
        )
    child_span.finish()

    span.set_tag('return_reason', f'success: {success}')

    if success:
        logger.info(f'Successfully built virtual_router #{virtual_router_id}')
        metrics.virtual_router_build_success()

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

        # Check if they built any VPNs and if so, send an email
        send_email_vpns = [vpn for vpn in virtual_router.get('vpns', []) if vpn['send_email']]
        if len(send_email_vpns) > 0:
            for vpn in send_email_vpns:
                vpn['virtual_router_ip'] = virtual_router['virtual_router_ip']
                vpn['podnet_cpe'] = virtual_router['podnet_cpe']
                child_span = opentracing.tracer.start_span('send_email', child_of=span)
                try:
                    EmailNotifier.vpn_build_success(vpn)
                    # update the send_email to False.
                    child_span = opentracing.tracer.start_span('update_to_send_email', child_of=span)
                    response = IAAS.vpn.partial_update(
                        token=Token.get_instance().token,
                        pk=vpn['id'],
                        data={'send_email': False},
                        span=child_span,
                    )
                    child_span.finish()
                    if response.status_code != 200:
                        logger.error(
                            f'Could not update VPN #{vpn["id"]} to reset send_email.\n'
                            f'Response: {response.content.decode()}.',
                        )
                except Exception:
                    logger.error(f'Failed to send build success email for VPN #{vpn["id"]}', exc_info=True)
                child_span.finish()
    else:
        logger.error(f'Failed to build virtual_router #{virtual_router_id}, placing in a unresourced state.')
        metrics.virtual_router_build_failure()

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
            EmailNotifier.virtual_router_failure(virtual_router, 'build')
        except Exception:
            logger.error(f'Failed to send build failure email for virtual_router #{virtual_router_id}', exc_info=True)
        child_span.finish()
