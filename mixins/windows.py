"""
mixin class containing methods that are needed by windows vm task classes
methods included;
    - method to deploy a given command to a given host
    - a fixed method to run powershell commands
"""
# stdlib
import logging
# lib
import opentracing
from jaeger_client import Span
from winrm import Response, Session
# local
from settings import NETWORK_PASSWORD


class WindowsMixin:
    logger: logging.Logger

    @classmethod
    def deploy(cls, cmd: str, management_ip: str, span: Span) -> Response:
        """
        Deploy the given command to the specified Windows host.
        :param management_ip: ip address to access the host
        :param cmd: command to execute on the host
        :param span: The span used for tracing the task that's currently running
        """
        cls.logger.debug(f'Deploying command to Windows Host {management_ip}\n{cmd}')
        session = Session(management_ip, auth=('administrator', NETWORK_PASSWORD))
        child_span = opentracing.tracer.start_span('run_ps', child_of=span)
        response = session.run_ps(cmd)
        child_span.finish()
        # Decode out and err
        if hasattr(response.std_out, 'decode'):
            response.std_out = response.std_out.decode()
        if hasattr(response.std_err, 'decode'):
            response.std_err = response.std_err.decode()
        return response
