"""
quiescer class for virtual_routers

- gathers template data
- generates setconf
- deploys setconf to the chosen router
"""

# stdlib
import logging
import socket
from typing import Any, Dict
# lib
import opentracing
from jaeger_client import Span
from paramiko import AutoAddPolicy, RSAKey, SSHClient, SSHException
# local
import utils
from scrubbers import VirtualRouter as VirtualRouterScrubber


__all__ = [
    'VirtualRouter',
]


class VirtualRouter(VirtualRouterScrubber):
    """
    Class that handles the quiescing of the specified virtual_router
    """
    # Keep a logger for logging messages from this class
    logger = logging.getLogger('robot.quiescers.virtual_router')

    # Override this method to ensure that nobody calls this accidentally
    @staticmethod
    def scrub(virtual_router_data: Dict[str, Any], span: Span) -> bool:
        """
        Shadow the scrub class scrub job to make sure we don't accidentally call it in this class
        """
        raise NotImplementedError(
            'If you want to scrub a virtual_router, use `scrubbers.virtual_router`, not `quiescers.virtual_router`',
        )

    @staticmethod
    def quiesce(virtual_router_data: Dict[str, Any], span: Span) -> bool:
        """
        Commence the quiesce of a virtual_router using the data read from the API
        :param virtual_router_data: The result of a read request for the specified virtual_router
        :param span: The tracing span in use for this quiesce task
        :return: A flag stating whether or not the quiesce was successful
        """
        virtual_router_id = virtual_router_data['id']
        project_id = virtual_router_data['project']['id']

        # Start by generating the proper dict of data needed by the template
        child_span = opentracing.tracer.start_span('generate_template_data', child_of=span)
        template_data = VirtualRouter._get_template_data(virtual_router_data, child_span)
        child_span.finish()

        # Check that the template data was successfully retrieved
        if template_data is None:
            error = f'Failed to retrieve template data for virtual_router #{virtual_router_id}.'
            VirtualRouter.logger.error(error)
            virtual_router_data['errors'].append(error)
            span.set_tag('failed_reason', 'template_data_failed')
            return False

        # Check that all of the necessary keys are present
        if not all(template_data[key] is not None for key in VirtualRouter.template_keys):
            missing_keys = [f'"{key}"' for key in VirtualRouter.template_keys if template_data[key] is None]
            error_msg = f'Template Data Error, the following keys were missing from the virtual_router quiesce data:' \
                        f' {", ".join(missing_keys)}'
            VirtualRouter.logger.error(error_msg)
            virtual_router_data['errors'].append(error_msg)
            span.set_tag('failed_reason', 'template_data_keys_missing')
            return False

        # If everything is okay, commence quiescing the virtual_router
        child_span = opentracing.tracer.start_span('generate_setconf', child_of=span)
        quiesce_bash_script = utils.JINJA_ENV.get_template('virtual_router/commands/quiesce.j2').render(**template_data)
        VirtualRouter.logger.debug(
            f'Generated quiesce bash script for virtual_router #{virtual_router_id}\n{quiesce_bash_script}',
        )
        child_span.finish()

        # Log onto PodNet box and run bash script
        management_ip = template_data.pop('management_ip')
        quiesced = False
        vpn_cmds = ''

        client = SSHClient()
        client.set_missing_host_key_policy(AutoAddPolicy())
        key = RSAKey.from_private_key_file('/root/.ssh/id_rsa')
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            # Try connecting to the host and running the necessary commands
            # No need for password as it should have keys
            sock.connect((management_ip, 22))
            client.connect(hostname=management_ip, username='robot', pkey=key, timeout=30, sock=sock)
            span.set_tag('host', management_ip)

            # If there are VPNs, remove connections
            if len(virtual_router_data['vpns']) > 0:
                # First remove the file from /etc/swanctl/conf.d/ then terminate
                vpn_filename = f'/etc/swanctl/conf.d/P{project_id}_vpns.conf'
                vpn_cmds = f'sudo rm {vpn_filename}\n'

            # Attempt to execute ALL of the virtual router build commands
            VirtualRouter.logger.debug(
                f'Executing Virtual Router quiesce commands for virtual_router #{virtual_router_id}',
            )
            child_span = opentracing.tracer.start_span('quiesce_virtual_router', child_of=span)
            stdout, stderr = VirtualRouter.deploy(f'{vpn_cmds}{quiesce_bash_script}', client, child_span)
            child_span.finish()
            if stderr:
                VirtualRouter.logger.error(
                    f'Virtual Router quiesce commands for virtual_router #{virtual_router_id} generated stderr.'
                    f'\n{stderr}',
                )
                virtual_router_data['errors'].append(stderr)
            else:
                VirtualRouter.logger.debug(
                    f'Virtual Router quiesce commands for virtual_router #{virtual_router_id} generated stdout.'
                    f'\n{stdout}',
                )
                quiesced = True

        except (OSError, SSHException, TimeoutError):
            error = f'Exception occurred while quiescing virtual_router #{virtual_router_id} in {management_ip}'
            VirtualRouter.logger.error(error, exc_info=True)
            virtual_router_data['errors'].append(error)
            span.set_tag('failed_reason', 'ssh_error')
        finally:
            client.close()

        return quiesced
