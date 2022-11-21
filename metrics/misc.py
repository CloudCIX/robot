# lib
from cloudcix_metrics import prepare_metrics, Metric
# local
from settings import REGION_NAME
from utils import get_current_git_sha


def current_commit():
    """
    Logs the currently running commit for this instance of Robot.
    Grafana will display this at the top of each Robot's dashboard
    :param sha: The commit sha obtained from git
    """
    prepare_metrics(lambda: Metric('robot_commit', get_current_git_sha(), {'region': REGION_NAME}))
