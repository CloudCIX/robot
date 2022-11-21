"""
updater class for linux snapshots

- gathers template data
- connects to the snapshot vm's server and updates the snapshot
"""
# stdlib
import logging
import socket
from typing import Any, Dict, Optional
# lib
import opentracing
from jaeger_client import Span
from netaddr import IPAddress
from paramiko import AutoAddPolicy, RSAKey, SSHClient, SSHException
# local
import settings
import utils
from mixins import LinuxMixin


__all__ = [
    'Linux',
]


class Linux(LinuxMixin):
    """
    Class that handles the updating of the specified Snapshot
    When we get to this point, we can be sure that the snapshot is on a linux host
    """
    logger = logging.getLogger('robot.updaters.snapshot.linux')
    template_keys = {
        # the ip address of the host that the Snapshot will be built on
        'host_ip',
        # the sudo password of the host, used to run some commands
        'host_sudo_passwd',
        # An identifier that uniquely identifies the snapshot
        'snapshot_identifier',
        # an identifier that uniquely identifies the vm
        'vm_identifier',
    }

    @staticmethod
    def update(snapshot_data: Dict[str, Any], span: Span) -> bool:
        """
        Commence the update of a snapshot using the data record read from the API
        :param snapshot_data: The result of a read request for the specified Snapshot
        :param span: The tracing span in use for this update task
        :return: A flag stating whether or not the update was successful
        """
        snapshot_id = snapshot_data['id']
        # Generate the necessary template data
        child_span = opentracing.tracer.start_span('generate_template_data', child_of=span)
        template_data = Linux._get_template_data(snapshot_data, child_span)
        child_span.finish()

        # Check that the data was successfully generated
        if template_data is None:
            error = f'Failed to retrieve template data for Snapshot #{snapshot_id}'
            Linux.logger.error(error)
            snapshot_data['errors'].append(error)
            span.set_tag('failed_reason', 'template_data_failed')
            return False

        # Check that all the necessary keys are present
        if not all(template_data[key] is not None for key in Linux.template_keys):
            missing_keys = [f'"{key}"' for key in Linux.template_keys if template_data[key] is None]
            error_msg = f'Template Data Error, the following keys were missing from the Snapshot update data: ' \
                        f'{", ".join(missing_keys)}.'
            Linux.logger.error(error_msg)
            span.set_tag('failed_reason', 'template_data_keys_missing')
            return False

        # If everything is okay, commence updating the snapshot
        host_ip = template_data.pop('host_ip')
        # Generate the update command using the template data
        child_span = opentracing.tracer.start_span('generate_command', child_of=span)
        cmd = utils.JINJA_ENV.get_template('snapshot/kvm/commands/update.j2').render(**template_data)
        child_span.finish()

        Linux.logger.debug(f'Generated Snapshot Update command for Snapshot #{snapshot_id}\n{cmd}')

        # Open a client and run the necessary command on the host
        updated = False
        client = SSHClient()
        client.set_missing_host_key_policy(AutoAddPolicy())
        key = RSAKey.from_private_key_file('/root/.ssh/id_rsa')
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            # Try connecting to the host and running the necessary commands
            sock.connect((host_ip, 22))
            client.connect(
                hostname=host_ip,
                username='administrator',
                pkey=key,
                timeout=30,
                sock=sock,
            )  # No need for password as it should have keys
            span.set_tag('host', host_ip)

            # Attempt to execute the update command
            Linux.logger.debug(f'Executing update command for Snapshot #{snapshot_id}')

            child_span = opentracing.tracer.start_span('update_snapshot', child_of=span)
            stdout, stderr = Linux.deploy(cmd, client, child_span)
            child_span.finish()

            if stdout:
                Linux.logger.debug(f'Snapshot Update command for Snapshot #{snapshot_id} generated stdout. \n{stdout}')
                updated = True
            if stderr:
                Linux.logger.error(f'Snapshot update command for Snapshot #{snapshot_id} generated stderr. \n{stderr}')
        except (OSError, SSHException) as err:
            error = f'Exception occurred while updating Snapshot #{snapshot_id} in {host_ip}.'
            Linux.logger.error(error, exc_info=True)
            snapshot_data['errors'].append(f'{error} Error: {err}')
            span.set_tag('failed_reason', 'ssh_error')
        finally:
            client.close()
        return updated

    @staticmethod
    def _get_template_data(snapshot_data: Dict[str, Any], span: Span) -> Optional[Dict[str, Any]]:
        """
        Given the snapshot data from the API, create a dictionary that contains all of the necessary keys
        for the template
        The keys will be checked in the update method and not here, this method is only concerned with fetching the data
        that it can.
        :param snapshot_data: The data of the Snapshot read from the API
        :param span: The tracing span in use for this task. In this method, just pass it to API calls.
        :returns: The data needed for the templates to update a Linux Snapshot
        """
        snapshot_id = snapshot_data['id']
        Linux.logger.debug(f'Compiling template data for Snapshot #{snapshot_id}')
        data: Dict[str, Any] = {key: None for key in Linux.template_keys}

        data['host_sudo_passwd'] = settings.NETWORK_PASSWORD
        data['snapshot_identifier'] = f'{snapshot_data["vm"]["id"]}_{snapshot_data["id"]}'
        data['vm_identifier'] = f'{snapshot_data["vm"]["project"]["id"]}_{snapshot_data["vm"]["id"]}'

        # Get the ip address of the host
        host_ip = None
        for interface in snapshot_data['server_data']['interfaces']:
            if interface['enabled'] is True and interface['ip_address'] is not None:
                if IPAddress(str(interface['ip_address'])).version == 6:
                    host_ip = interface['ip_address']
                    break
        if host_ip is None:
            error = f'Host ip address not found for the server # {snapshot_data["vm"]["server_id"]}'
            Linux.logger.error(error)
            snapshot_data['errors'].append(error)
            return None
        data['host_ip'] = host_ip
        return data
