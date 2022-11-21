"""
updater class for virtual_routers

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
from builders import VirtualRouter as VirtualRouterBuilder

__all__ = [
    'VirtualRouter',
]


class VirtualRouter(VirtualRouterBuilder):
    """
    Class that handles the updating of the specified virtual_router

    Inherits the Builder class as both classes have the same template data gathering method
    """
    # Keep a logger for logging messages from this class
    logger = logging.getLogger('robot.updaters.virtual_router')

    # Override this method to ensure that nobody calls this accidentally
    @staticmethod
    def build(virtual_router_data: Dict[str, Any], span: Span) -> bool:
        """
        Shadow the build class build job to make sure we don't accidentally call it in this class
        """
        raise NotImplementedError(
            'If you want to build a virtual_router, use `builders.virtual_router`, not `updaters.virtual_router`',
        )

    @staticmethod
    def update(virtual_router_data: Dict[str, Any], span: Span) -> bool:
        """
        Commence the update of a virtual_router using the data read from the API
        :param span: The tracing span in use for this update task
        :param virtual_router_data: The result of a read request for the specified virtual_router
        :return: A flag stating whether or not the update was successful
        """
        virtual_router_id = virtual_router_data['id']

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
            error = f'Template Data Error, the following keys were missing from the virtual_router update data: ' \
                    f'{", ".join(missing_keys)}'
            VirtualRouter.logger.error(error)
            virtual_router_data['errors'].append(error)
            span.set_tag('failed_reason', 'template_data_keys_missing')
            return False

        # If everything is okay, commence updating the virtual_router
        child_span = opentracing.tracer.start_span('generate_ip_commands', child_of=span)
        update_bash_script = utils.JINJA_ENV.get_template('virtual_router/commands/update.j2').render(**template_data)
        VirtualRouter.logger.debug(
            f'Generated update bash script for virtual_router #{virtual_router_id}\n{update_bash_script}',
        )

        firewall_nft = utils.JINJA_ENV.get_template('virtual_router/features/firewall.j2').render(**template_data)
        VirtualRouter.logger.debug(f'Generated firewall nft for virtual_router #{virtual_router_id}\n{firewall_nft}')

        if len(virtual_router_data['vpns']) > 0:
            vpn_conf = utils.JINJA_ENV.get_template('virtual_router/features/vpn.j2').render(**template_data)
            VirtualRouter.logger.debug(f'Generated vpn conf for virtual_router #{virtual_router_id}\n{vpn_conf}')

        child_span.finish()

        # Log onto PodNet box and run bash script
        remote_path = template_data.pop('remote_path')
        management_ip = template_data.pop('management_ip')
        updated = False

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

            # Firstly, Write Firewall rules file .nft and vpn.conf file(if any) to PodNet box
            child_span = opentracing.tracer.start_span('write_files_to_podnet_box', child_of=span)
            sftp = client.open_sftp()
            firewall_filename = template_data.pop('firewall_filename')
            try:
                with sftp.open(f'{remote_path}{firewall_filename}', mode='w', bufsize=1) as firewall:
                    firewall.write(firewall_nft)
                VirtualRouter.logger.debug(f'Successfully wrote file {firewall_filename} to PodNet box#{management_ip}')
            except IOError as err:
                VirtualRouter.logger.error(
                    f'Failed to write file {firewall_filename} to PodNet box#{management_ip}',
                    exc_info=True,
                )
                virtual_router_data['errors'].append(err)
                return False

            if len(virtual_router_data['vpns']) > 0:
                temp_vpn_filename = template_data.pop('temp_vpn_filename')
                try:
                    with sftp.open(temp_vpn_filename, mode='w', bufsize=1) as vpn:
                        vpn.write(vpn_conf)
                    VirtualRouter.logger.debug(
                        f'Successfully wrote file {temp_vpn_filename} to PodNet box#{management_ip}',
                    )
                except IOError as err:
                    VirtualRouter.logger.error(
                        f'Failed to write file {temp_vpn_filename} to PodNet box#{management_ip}',
                        exc_info=True,
                    )
                    virtual_router_data['errors'].append(err)
                    return False
            child_span.finish()

            # Finally, Attempt to execute ALL of the virtual router update commands
            # which includes deleting namespace, firewall rules and vpns(if any) and adding all of the VR at once.
            VirtualRouter.logger.debug(
                f'Executing Virtual Router update commands for virtual_router #{virtual_router_id}',
            )

            child_span = opentracing.tracer.start_span('update_virtual_router', child_of=span)
            stdout, stderr = VirtualRouter.deploy(update_bash_script, client, child_span)
            child_span.finish()
            if stderr:
                VirtualRouter.logger.error(
                    f'Virtual Router update commands for virtual_router #{virtual_router_id} generated stderr.'
                    f'\n{stderr}',
                )
                virtual_router_data['errors'].append(stderr)
            else:
                VirtualRouter.logger.debug(
                    f'Virtual Router update commands for virtual_router #{virtual_router_id} generated stdout.'
                    f'\n{stdout}',
                )
                updated = True

        except (OSError, SSHException, TimeoutError):
            error = f'Exception occurred while updating virtual_router #{virtual_router_id} in {management_ip}'
            VirtualRouter.logger.error(error, exc_info=True)
            virtual_router_data['errors'].append(error)
            span.set_tag('failed_reason', 'ssh_error')
        finally:
            client.close()

        return updated
