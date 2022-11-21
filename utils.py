"""
File containing some utility functions that wrap around various repeatedly used logic
"""
# stdlib
import atexit
import logging
import os
import subprocess
from collections import deque
from json import JSONEncoder
from typing import Any, Deque, Dict, Iterable, Optional, Tuple
# lib
import jinja2
import netaddr
from logstash_async.formatter import LogstashFormatter
from logstash_async.handler import AsynchronousLogstashHandler
# local
from cloudcix_token import Token
from cloudcix.client import Client
from settings import (
    LOGSTASH_PORT,
    LOGSTASH_URL,
    NETWORK_PASSWORD,
    REGION_NAME,
)


__all__ = [
    'api_list',
    'api_read',
    'flush_logstash',
    'get_current_git_sha',
    'JINJA_ENV',
    'setup_root_logger',
    'write_to_drive',
]

JINJA_ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader('templates'),
    trim_blocks=True,
)


class DequeEncoder(JSONEncoder):
    """
    JSON Encoder that will allow us to encode deques without changing too much in the code
    """

    def default(self, obj: Iterable) -> Iterable:
        try:
            return super(DequeEncoder, self).default(obj)
        except TypeError:
            # Special case handling for IPNetwork objects because IPNetworks are iterable but we don't want to
            # print them as a list
            if isinstance(obj, netaddr.IPNetwork):
                return str(obj)
            try:
                iterable = iter(obj)
            except TypeError:
                return str(obj)
            else:
                return list(iterable)


def _redact_logs(record: logging.LogRecord) -> bool:
    """
    Filter out the logs to redact passwords and other sensitive information
    """
    record.msg = record.msg.replace(NETWORK_PASSWORD, '*' * 16)
    return True


def setup_root_logger():
    """
    Called at startup.
    Sets up the proper handlers on the root logger which allows all other loggers to propogate messages to it
    instead of having that old bad system
    """
    # Add null handler to root logger to avoid basicConfig from running
    logging.getLogger().handlers = []

    # Set up robot parent logger
    logger = logging.getLogger('robot')
    if len(logger.handlers) > 0:
        return
    logger.setLevel(logging.DEBUG)

    # Logstash Handler
    logstash_fmt = LogstashFormatter(extra={'application': 'robot', 'region': REGION_NAME})
    logstash_handler = AsynchronousLogstashHandler(LOGSTASH_URL, LOGSTASH_PORT, 'log.db')

    logstash_handler.setFormatter(logstash_fmt)
    logger.addHandler(logstash_handler)

    # Add the redact filter
    logstash_handler.addFilter(_redact_logs)
    logger.addFilter(_redact_logs)

    # At exit, flush all logs to logstash
    atexit.register(logstash_handler.flush)


def get_current_git_sha() -> str:
    """
    Finds the current git commit sha and returns it
    :return: The sha of the current commit
    """
    return subprocess.check_output([
        'git',
        'describe',
        '--always',
    ]).strip().decode()


def flush_logstash():
    """
    helper method to flush the logstash handler
    """
    for handler in logging.getLogger('robot').handlers:
        if hasattr(handler, 'flush'):
            handler.flush()


# Methods that wrap cloudcix clients to abstract retrieval and checking
# These methods replace the ones in ro.py
# However, we only replace the list and read method, since wrapping update and delete didn't really affect the code
# make request -> check status code / call ro.update -> check flag

def api_list(client: Client, params: Dict[str, Any], **kwargs) -> Deque[Dict[str, Any]]:
    """
    Calls the list command on the supplied client, using the supplied parameters and kwargs and fetches all of the data
    that matches.
    :param client: The client to call the list method on
    :param params: List parameters to be sent in the request
    :param kwargs: Any extra kwargs to pass to the request (ie spans)
    :returns: A list of instances of the client, if the request is valid, else an empty list
    """
    logger = logging.getLogger('robot.utils.api_list')
    client_name = f'{client.application}.{client.service_uri}'
    logger.debug(f'Attempting to retrieve a list of {client_name} records with the following filters: {params}')

    # Set up necessary stuff for fetching all of the items for the params
    params['page'] = 0
    objects: Deque[Dict[str, Any]] = deque()

    response = client.list(
        token=Token.get_instance().token,
        params=params,
        **kwargs,
    )
    # Token expire error "detail":"JWT token is expired. Please login again."
    if response.status_code == 401 and 'token is expired' in response.json()['detail']:
        # try again to list as Token renews in response call
        return api_list(client, params)

    if response.status_code != 200:
        logger.error(
            f'HTTP {response.status_code} error occurred when attempting to fetch {client_name} instances with '
            f'filters {params};\nResponse Text: {response.content.decode()}',
        )
        return deque()

    response_data = response.json()
    objects.extend(response_data['content'])

    # Determine the total number of records to fetch
    total_records: int
    total_records = response_data['_metadata'].get(
        'total_records',
        response_data['_metadata']['total_records'],
    )
    logger.debug(f'{client_name}.list retrieved {total_records} records with the following filters: {params}')

    # Go fetch the rest of the objects
    while len(objects) < total_records:
        params['page'] += 1
        response = client.list(
            token=Token.get_instance().token,
            params=params,
            **kwargs,
        )
        # Token expire error "detail":"JWT token is expired. Please login again." so expect renew in next request.
        if response.status_code == 401 and 'token is expired' in response.json()['detail']:
            response.json()['content'] = []
        if response.status_code != 200:
            logger.error(
                f'HTTP {response.status_code} error occurred when attempting to fetch {client_name} instances with '
                f'filters {params};\nResponse Text: {response.content.decode()}',
            )
            # Return what we have so fa
            return objects
        objects.extend(response.json()['content'])
    return objects


def api_read(client: Client, pk: int, **kwargs) -> Dict[str, Any]:
    """
    Calls the read command on the supplied client, using the supplied pk to make the request
    :param client: The client to call the read method on
    :param pk: The id of the object to read
    :param kwargs: Any extra kwargs to pass to the request (ie spans)
    :returns: The read instance, or None if an error occurs
    """
    obj: Dict[str, Any] = {}
    logger = logging.getLogger('robot.utils.api_read')
    client_name = f'{client.application}.{client.service_uri}'
    logger.debug(f'Attempting to read {client_name} #{pk}')
    response = client.read(
        token=Token.get_instance().token,
        pk=pk,
        **kwargs,
    )
    # Token expire error "detail":"JWT token is expired. Please login again."
    if response.status_code == 401 and 'token is expired' in response.json()['detail']:
        # try again to list as Token renews in response call
        return api_read(client, pk)

    if response.status_code == 200:
        obj = response.json()['content']
    else:
        logger.error(
            f'HTTP {response.status_code} error occurred when attempting to fetch {client_name} #{pk};\n'
            f'Response Text: {response.content.decode()}',
        )
    return obj


def write_to_drive(
    conf: str,
    filename: str,
    obj_id: int,
    obj_type: str,
    path: str,
) -> Tuple[bool, Optional['str']]:
    """
    Generate and write files into the network drive so they are on the host for the build scripts to utilise.
    :param conf: The contents of the file you want to add the the path
    :param filename: The name of the file to write the template_data to on the path given.
    :param obj_id: The ID of the object you are writing the file to.
    :param obj_type: The type of object that the file is for e.g. vm or virtual router
    :param path: Network drive location to create above files for Virtual Router build
    :returns: A flag stating whether the job was successful
    :return errors: The error that occurred while writing the file
    """
    # Create the folder in the network drive to store files in
    logger = logging.getLogger('robot.utils.write_to_drive')
    try:
        os.mkdir(path)
    except FileExistsError:
        pass
    except OSError as err:
        logger.error(f'Failed to create directory for {obj_type} #{obj_id} at {path}.', exc_info=True)
        return False, f'{err}'

    try:
        with open(f'{path}/{filename}', 'w') as f:
            f.write(conf)
        logger.debug(f'Successfully wrote file {filename} for {obj_type} #{obj_id}')
    except IOError as err:
        logger.error(f'Failed to write file {filename} for {obj_type} #{obj_id}', exc_info=True)
        return False, f'{err}'

    return True, None
