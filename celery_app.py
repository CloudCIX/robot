"""
Celery main runner
"""
# stdlib
import atexit
import logging
import time
# lib
import opentracing
from celery import Celery
from celery.schedules import crontab
from celery.signals import task_failure, task_prerun, task_postrun
from jaeger_client import Config
# local
import metrics
import settings
import utils

__all__ = [
    'app',
]

# Jaeger opentracing.tracer config
tracer_config = Config(
    config={
        'logging': True,
        'sampler': {
            'type': 'const',
            'param': 1,
        },
    },
    service_name=f'robot_{settings.REGION_NAME}',
    validate=True,
)


app = Celery(
    'robot',
    broker=f'amqp://[{settings.CELERY_HOST}]:5672',
    include=['tasks'],
)
# Optional config
app.conf.timezone = 'Europe/Dublin'
# Route heartbeat tasks to a different queue than the other tasks
app.conf.task_routes = {
    'tasks.scrub': {'queue': 'heartbeat'},
    # Also send virtual router tasks to a separate queue
    'tasks.virtual_router.*': {'queue': 'virtual_router'},
    # All other tasks will be sent to the default queue named 'celery'
}

# Add cron based jobs
app.conf.beat_schedule = {
    'scrub-at-midnight': {
        'task': 'tasks.scrub',
        'schedule': crontab(minute=0, hour=0),  # daily at midnight
    },
}


# Ensure the loggers are set up before each task is run
@task_prerun.connect
def setup_logger_and_tracer(*args, **kwargs):
    """
    Set up the logger before each task is run, in the hopes that it will fix our logging issue.
    Also ensure that the opentracing.tracer is setup for this environment
    """
    if not settings.LOGSTASH_ENABLE:
        logging.disable(logging.CRITICAL)
        return
    # Ensure the root logger is set up
    utils.setup_root_logger()
    # Also check to ensure we have a opentracing.tracer initialized in the forked process
    if not opentracing.is_tracer_registered:
        tracer_config.initialize_tracer()
        atexit.register(opentracing.tracer.close)


# Sleep after each task to try and flush spans
@task_postrun.connect
def sleep_to_flush_spans(*args, **kwargs):
    """
    Flush spans by passing to IO loop, just to be safe
    """
    if settings.LOGSTASH_ENABLE:
        time.sleep(5)


# Catch all uncaught errors
@task_failure.connect
def catch_uncaught_errors(task_id: str, exception: Exception, *args, **kwargs):
    logging.getLogger('robot.celery_app').error('Uncaught error occurred in a task.', exc_info=exception)


if __name__ == '__main__':
    if settings.ROBOT_ENV != 'dev':
        metrics.current_commit()
    app.start()
