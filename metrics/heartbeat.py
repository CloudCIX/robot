# lib
from cloudcix_metrics import prepare_metrics, Metric
# local
from settings import REGION_NAME


def heartbeat(value: int = 1):
    """
    Test version of heartbeat for RobotAlpha
    :param value: The value to send to Influx. Defaults to 1
        NOTE: Only send a 0 when Robot has gone down
    """
    prepare_metrics(lambda: Metric('robot_heartbeat', value, {'region': REGION_NAME}))
