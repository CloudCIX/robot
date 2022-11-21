# stdlib
import logging
from datetime import datetime, timedelta
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
from scrubbers import VirtualRouter as VirtualRouterScrubber

__all__ = [
    'scrub_virtual_router',
]


@app.task
def scrub_virtual_router(virtual_router_id: int):
    """
    Helper function that wraps the actual task in a span, meaning we don't have to remember to call .finish
    """
    span = opentracing.tracer.start_span('tasks.scrub_virtual_router')
    span.set_tag('virtual_router_id', virtual_router_id)
    _scrub_virtual_router(virtual_router_id, span)
    span.finish()

    # Flush the loggers here so it's not in the span
    utils.flush_logstash()


def _scrub_virtual_router(virtual_router_id: int, span: Span):
    """
    Task to scrub the specified virtual_router
    """
    logger = logging.getLogger('robot.tasks.virtual_router.scrub')
    logger.info(f'Commencing scrub of virtual_router #{virtual_router_id}')

    # Read the virtual_router
    # Don't use utils so we can check the response code
    child_span = opentracing.tracer.start_span('read_virtual_router', child_of=span)
    response = IAAS.virtual_router.read(
        token=Token.get_instance().token,
        pk=virtual_router_id,
        span=child_span,
    )
    child_span.finish()

    if response.status_code == 404:
        logger.info(
            f'Received scrub task for virtual_router #{virtual_router_id} but it was already deleted from the API',
        )
        span.set_tag('return_reason', 'already_deleted')
        return
    elif response.status_code != 200:
        logger.error(
            f'HTTP {response.status_code} error occurred when attempting to fetch virtual_router #{virtual_router_id};'
            f'\n Response Text: {response.content.decode()}',
        )
        span.set_tag('return_reason', 'invalid_virtual_router_id')
        return
    virtual_router = response.json()['content']

    # Ensure that the state of the virtual_router is still currently SCRUB_QUEUE
    if virtual_router['state'] != state.SCRUB_QUEUE:
        logger.warning(
            f'Cancelling scrub of virtual_router #{virtual_router_id}. Expected state to be SCRUB, '
            f'found {virtual_router["state"]}.',
        )
        # Return out of this function without doing anything
        span.set_tag('return_reason', 'not_in_valid_state')
        return

    # Update the virtual_router state to SCRUBBING
    child_span = opentracing.tracer.start_span('update_to_scrubbing', child_of=span)
    response = IAAS.virtual_router.partial_update(
        token=Token.get_instance().token,
        pk=virtual_router_id,
        data={'state': state.SCRUBBING},
        span=child_span,
    )
    child_span.finish()

    # Also ensure that all the VMs under this project are scrubbed
    child_span = opentracing.tracer.start_span('read_project_vms', child_of=span)
    params = {
        'search[project_id]': virtual_router['project']['id'],
        'exclude[state]': state.CLOSED,
    }
    vrf_vms = utils.api_list(IAAS.vm, params, span=child_span)
    child_span.finish()
    vm_count = len(vrf_vms)
    if vm_count > 0:
        logger.warning(f'{vm_count} VMs are still in this project, scrub of VRF #{virtual_router_id} is postponed')
        # since vms are yet in the project so wait for 1 min and try again.
        scrub_virtual_router.s(virtual_router_id).apply_async(eta=datetime.now() + timedelta(seconds=60))
        return

    # There's no in-between state for Scrub tasks, just jump straight to doing the work
    virtual_router['errors'] = []
    success: bool = False
    child_span = opentracing.tracer.start_span('scrub', child_of=span)
    try:
        success = VirtualRouterScrubber.scrub(virtual_router, child_span)
    except Exception as err:
        error = f'An unexpected error occurred when attempting to scrub virtual_router #{virtual_router_id}.'
        logger.error(error, exc_info=True)
        virtual_router['errors'].append(f'{error} Error: {err}')
    child_span.finish()

    span.set_tag('return_reason', f'success: {success}')

    if success:
        logger.info(f'Successfully scrubbed virtual_router #{virtual_router_id}')
        metrics.virtual_router_scrub_success()
        # Closing the virtual_router
        child_span = opentracing.tracer.start_span('close_virtual_router', child_of=span)
        response = IAAS.virtual_router.partial_update(
            token=Token.get_instance().token,
            pk=virtual_router_id,
            data={'state': state.CLOSED},
            span=child_span,
        )
        child_span.finish()

        if response.status_code == 200:
            logger.info(f'Successfully closed virtual_router #{virtual_router_id}')
        else:
            logger.error(
                f'HTTP {response.status_code} response received when attempting to close virtual_router '
                f'#{virtual_router_id};\n Response Text: {response.content.decode()}',
            )
    else:
        logger.error(f'Failed to scrub virtual_router #{virtual_router_id}')
        metrics.virtual_router_scrub_failure()

        # Update state to UNRESOURCED in the API
        child_span = opentracing.tracer.start_span('update_to_unresourced', child_of=span)
        response = IAAS.virtual_router.partial_update(
            token=Token.get_instance().token,
            pk=virtual_router_id,
            data={'state': state.UNRESOURCED},
            span=child_span,
        )
        child_span.finish()

        child_span = opentracing.tracer.start_span('send_email', child_of=span)
        try:
            EmailNotifier.virtual_router_failure(virtual_router, 'scrub')
        except Exception:
            logger.error(f'Failed to send scrub failure email for virtual_router #{virtual_router_id}', exc_info=True)
        child_span.finish()
