"""
mixin class containing methods that are needed by linux vm task classes
methods included;
    - method to deploy a given command to a given host
    - a helper method to fully retrieve the response from paramiko outputs
"""
# stdlib
import logging
from collections import deque
from time import sleep
from typing import Deque, Tuple
# lib
import opentracing
from jaeger_client import Span
from paramiko import Channel, SSHClient
# local

__all__ = [
    'LinuxMixin',
]


class LinuxMixin:
    logger: logging.Logger

    @staticmethod
    def get_full_response(channel: Channel, wait_time: int = 15, read_size: int = 64) -> str:
        """
        Get the full response from the specified paramiko channel, waiting a given number of seconds before trying to
        read from it each time.
        :param channel: The channel to be read from
        :param wait_time: How long in seconds between each read
        :param read_size: How many bytes to be read from the channel each time
        :return: The full output from the channel, or as much as can be read given the parameters.
        """
        fragments: Deque[str] = deque()
        sleep(wait_time)
        while channel.recv_ready():
            fragments.append(channel.recv(read_size).decode())
            sleep(wait_time)
        return ''.join(fragments)

    @classmethod
    def deploy(cls, command: str, client: SSHClient, span: Span) -> Tuple[str, str]:
        """
        Deploy the given `command` to the Linux host accessible via the supplied `client`
        :param command: The command to run on the host
        :param client: A paramiko.Client instance that is connected to the host
            The client is passed instead of the host_ip so we can avoid having to open multiple connections
        :param span: The span used for tracing the task that's currently running
        :return: The messages retrieved from stdout and stderr of the command
        """
        hostname = client.get_transport().sock.getpeername()[0]
        cls.logger.debug(f'Deploying command {command} to Linux Host {hostname}')

        # Run the command via the client
        child_span = opentracing.tracer.start_span('exec_command', child_of=span)
        _, stdout, stderr = client.exec_command(command)
        # Block until command finishes
        stdout.channel.recv_exit_status()
        child_span.finish()

        # Read the full response from both channels
        child_span = opentracing.tracer.start_span('read_stdout', child_of=span)
        output = cls.get_full_response(stdout.channel)
        child_span.finish()

        child_span = opentracing.tracer.start_span('read_stderr', child_of=span)
        error = cls.get_full_response(stderr.channel)
        child_span.finish()
        return output, error
