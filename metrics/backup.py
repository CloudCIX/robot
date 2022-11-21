from settings import REGION_NAME
from cloudcix_metrics import prepare_metrics, Metric


def build_success(total_secs: int):
    """
    Sends a data packet to Influx reporting a successful build
    :param total_secs: The total number of seconds that Robot took to build the Backup (from request to build)
    """
    tags = {'region': REGION_NAME}
    prepare_metrics(lambda: Metric('backup_build_success', 1, tags))
    prepare_metrics(lambda: Metric('backup_time_to_build', total_secs, tags))


def build_failure():
    """
    Sends a data packet to Influx reporting a failed build
    """
    prepare_metrics(lambda: Metric('backup_build_failure', 1, {'region': REGION_NAME}))


def scrub_success():
    """
    Sends a data packet to Influx reporting a successful scrub
    """
    prepare_metrics(lambda: Metric('backup_scrub_success', 1, {'region': REGION_NAME}))


def scrub_failure():
    """
    Sends a data packet to Influx reporting a failed scrub
    """
    prepare_metrics(lambda: Metric('backup_scrub_failure', 1, {'region': REGION_NAME}))


def update_success():
    """
    Sends a data packet to Influx reporting a successful update
    """
    prepare_metrics(lambda: Metric('backup_update_success', 1, {'region': REGION_NAME}))


def update_failure():
    """
    Sends a data packet to Influx reporting a failed update
    """
    prepare_metrics(lambda: Metric('backup_update_failure', 1, {'region': REGION_NAME}))
