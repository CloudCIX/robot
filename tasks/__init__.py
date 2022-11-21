"""
Module containing all of the non beat celery tasks

In this file, we define the robot based tasks that will be run by celery beat
"""
# stdlib
import logging
from datetime import datetime, timedelta
# lib
from cloudcix.api.iaas import IAAS
# local
import robot
import utils
from celery_app import app
from settings import IN_PRODUCTION
from .virtual_router import debug_logs


@app.task
def scrub():
    """
    Once per day, at midnight, call the robot scrub methods to delete hardware
    """
    # Add the Scrub timestamp when the region isn't Alpha
    timestamp = None
    if IN_PRODUCTION:
        timestamp = (datetime.now() - timedelta(days=7)).isoformat()
    robot_scrub = robot.Robot([], [], [], [])
    robot_scrub.scrub(timestamp)


@app.task
def debug(virtual_router_id: int):
    """
    Waits for 15 min from the time latest updated or created for Firewall rules to reset the debug_logging field
    for all firewall rules of a Virtual router
    """
    logging.getLogger('robot.tasks.debug').debug(
        f'Checking Virtual Router #{virtual_router_id} to pass to the debug task queue',
    )
    virtual_router_data = utils.api_read(IAAS.virtual_router, virtual_router_id)
    if virtual_router_data is None:
        return
    firewall_rules = virtual_router_data['firewall_rules']
    if len(firewall_rules) == 0:
        return
    list_updated = [firewall_rule['updated'] for firewall_rule in firewall_rules]
    # Find the latest updated firewall
    latest = max(list_updated)
    # format latest string and convert to a datetime
    latest = latest.split('+')[0]  # removing timezone info
    latest_dt = datetime.strptime(latest, '%Y-%m-%dT%H:%M:%S.%f')
    # compare with 15 min from utc now time
    utc_now = datetime.utcnow()
    delta = utc_now - latest_dt
    if delta >= timedelta(minutes=15):
        logging.getLogger('robot.tasks.debug').debug(
            f'Passing virtual_router #{virtual_router_id} to the debug_logs task queue',
        )
        debug_logs.delay(virtual_router_id)
