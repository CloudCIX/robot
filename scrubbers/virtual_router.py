"""
scrubber class for virtual_routers

- gathers template data
- generates setconf
- deploys setconf to the chosen router
"""

# stdlib
import logging
import socket
from collections import deque
from typing import Any, Deque, Dict, Optional
# lib
import opentracing
from cloudcix.api.iaas import IAAS
from jaeger_client import Span
from paramiko import AutoAddPolicy, RSAKey, SSHClient, SSHException
# local
import settings
import utils
from mixins import LinuxMixin


__all__ = [
    'VirtualRouter',
]


class VirtualRouter(LinuxMixin):
    """
    Class that handles the scrubbing of the specified virtual_router
    """
    # Keep a logger for logging messages from this class
    logger = logging.getLogger('robot.scrubbers.virtual_router')
    # Keep track of the keys necessary for the template, so we can check all keys are present before scrubbing
    template_keys = {
        # The IP Address of the Management port of the physical Router
        'management_ip',
        # The id of the Project that owns the virtual_router being scrubbed
        'project_id',
        # The interface connecting Podnet to hosts
        'private_interface',
        # A list of vLans to be built in the virtual_router
        'vlans',
        # A list of VPNs to be built in the virtual_router
        'vpns',
    }

    @staticmethod
    def scrub(virtual_router_data: Dict[str, Any], span: Span) -> bool:
        """
        Commence the scrub of a virtual_router using the data read from the API
        :param virtual_router_data: The result of a read request for the specified virtual_router
        :param span: The tracing span in use for this scrub task
        :return: A flag stating whether or not the scrub was successful
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
            error_msg = f'Template Data Error, the following keys were missing from the virtual_router scrub  data: ' \
                        f'{", ".join(missing_keys)}'
            VirtualRouter.logger.error(error_msg)
            virtual_router_data['errors'].append(error_msg)
            span.set_tag('failed_reason', 'template_data_keys_missing')
            return False

        # If everything is okay, commence scrubbing the virtual_router
        child_span = opentracing.tracer.start_span('generate_ip_commands', child_of=span)
        scrub_bash_script = utils.JINJA_ENV.get_template('virtual_router/commands/scrub.j2').render(**template_data)
        VirtualRouter.logger.debug(
            f'Generated scrub bash script for virtual_router #{virtual_router_id}\n{scrub_bash_script}',
        )
        child_span.finish()

        # Log onto PodNet box and run bash script
        management_ip = template_data.pop('management_ip')
        scrubbed = False
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
            sftp = client.open_sftp()
            span.set_tag('host', management_ip)

            # If there are VPNs, remove connections
            if len(virtual_router_data['vpns']) > 0:
                child_span = opentracing.tracer.start_span('scrub_project_vpns', child_of=span)
                # First check for vpn file, if exists then its not quiesced previously
                # then remove the file from /etc/swanctl/conf.d/ and terminate
                vpn_filename = f'/etc/swanctl/conf.d/P{project_id}_vpns.conf'
                try:
                    sftp.open(vpn_filename, mode='r')
                    vpn_cmds = f'sudo rm {vpn_filename}\n'
                    child_span.finish()
                except IOError:
                    VirtualRouter.logger.debug(
                        'VPN config does not exists, It must be removed in Quiesce step.',
                    )

            # Attempt to execute ALL of the virtual router scrub commands
            VirtualRouter.logger.debug(
                f'Executing Virtual Router scrub commands for virtual_router #{virtual_router_id}',
            )
            child_span = opentracing.tracer.start_span('scrub_virtual_router', child_of=span)
            stdout, stderr = VirtualRouter.deploy(f'{vpn_cmds}{scrub_bash_script}', client, child_span)
            child_span.finish()
            if stderr:
                VirtualRouter.logger.error(
                    f'Virtaul Router scrub commands for virtual_router #{virtual_router_id} generated stderr.'
                    f'\n{stderr}',
                )
                virtual_router_data['errors'].append(stderr)
            else:
                VirtualRouter.logger.debug(
                    f'Virtual Router scrub commands for virtual_router #{virtual_router_id} generated stdout.'
                    f'\n{stdout}',
                )
                scrubbed = True

        except (OSError, SSHException, TimeoutError):
            error = f'Exception occurred while quiescing virtual_router #{virtual_router_id} in {management_ip}'
            VirtualRouter.logger.error(error, exc_info=True)
            virtual_router_data['errors'].append(error)
            span.set_tag('failed_reason', 'ssh_error')
        finally:
            client.close()

        return scrubbed

    @staticmethod
    def _get_template_data(virtual_router_data: Dict[str, Any], span: Span) -> Optional[Dict[str, Any]]:
        """
        Given the virtual_router data from the API, create a dictionary that contains all of the necessary keys
        for the template.
        The keys will be checked in the scrub method and not here, this method is only concerned with fetching the data
        that it can.
        :param virtual_router_data: The data on the virtual_router that was retrieved from the API
        :param span: The tracing span in use for this task. In this method just pass it to API calls
        :returns: Constructed template data, or None if something went wrong
        """
        virtual_router_id = virtual_router_data['id']
        VirtualRouter.logger.debug(f'Compiling template data for virtual_router #{virtual_router_id}')
        data: Dict[str, Any] = {key: None for key in VirtualRouter.template_keys}

        data['project_id'] = virtual_router_data['project']['id']
        # Router information
        data['management_ip'] = settings.MGMT_IP
        data['private_interface'] = settings.PRIVATE_INF

        vlans: Deque[Dict[str, str]] = deque()
        subnets = virtual_router_data['subnets']
        # Add the vlan information to the deque
        for subnet in subnets:
            vlans.append({
                'vlan': subnet['vlan'],
            })

        data['vlans'] = vlans

        # Finally, get the VPNs for the Project
        vpns: Deque[Dict[str, Any]] = deque()
        params = {'search[virtual_router_id]': virtual_router_id}
        child_span = opentracing.tracer.start_span('listing_vpns', child_of=span)
        virtual_router_vpns = utils.api_list(IAAS.vpn, params, span=child_span)
        child_span.finish()
        for vpn in virtual_router_vpns:
            vpns.append(vpn)
        data['vpns'] = vpns
        virtual_router_data['vpns'] = data['vpns']

        return data
