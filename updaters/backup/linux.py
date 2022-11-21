"""
updater class for linux backups

- gathers template data
- connects to the backup vm's server and updates the backup
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
    Class that handles the updating of the specified Backup
    When we get to this point, we can be sure that the backup is on a linux host
    """
    logger = logging.getLogger('robot.updaters.backup.linux')
    template_keys = {
        # the ip address of the host that the Backup will be built on
        'host_ip',
        # the sudo password of the host, used to run some commands
        'host_sudo_passwd',
        # An identifier that uniquely identifies the backup
        'backup_identifier',
        # an identifier that uniquely identifies the vm
        'vm_identifier',
    }

    @staticmethod
    def update(backup_data: Dict[str, Any], span: Span) -> bool:
        """
        Commence the update of a backup using the data record read from the API
        :param backup_data: The result of a read request for the specified Backup
        :param span: The tracing span in use for this update task
        :return: A flag stating whether or not the update was successful
        """
        backup_id = backup_data['id']
        # Generate the necessary template data
        child_span = opentracing.tracer.start_span('generate_template_data', child_of=span)
        template_data = Linux._get_template_data(backup_data, child_span)
        child_span.finish()

        # Check that the data was successfully generated
        if template_data is None:
            error = f'Failed to retrieve template data for Backup #{backup_id}'
            Linux.logger.error(error)
            backup_data['errors'].append(error)
            span.set_tag('failed_reason', 'template_data_failed')
            return False

        # Check that all the necessary keys are present
        if not all(template_data[key] is not None for key in Linux.template_keys):
            missing_keys = [f'"{key}"' for key in Linux.template_keys if template_data[key] is None]
            error_msg = f'Template Data Error, the following keys were missing from the Backup update data: ' \
                        f'{", ".join(missing_keys)}.'
            Linux.logger.error(error_msg)
            span.set_tag('failed_reason', 'template_data_keys_missing')
            return False

        # If everything is okay, commence updating the backup
        host_ip = template_data.pop('host_ip')
        # Generate the update command using the template data
        child_span = opentracing.tracer.start_span('generate_command', child_of=span)
        cmd = utils.JINJA_ENV.get_template('backup/kvm/commands/update.j2').render(**template_data)
        child_span.finish()

        Linux.logger.debug(f'Generated Backup Update command for Backup #{backup_id}\n{cmd}')

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
            Linux.logger.debug(f'Executing update command for Backup #{backup_id}')

            child_span = opentracing.tracer.start_span('update_backup', child_of=span)
            stdout, stderr = Linux.deploy(cmd, client, child_span)
            child_span.finish()

            if stdout:
                Linux.logger.debug(f'Backup Update command for Backup #{backup_id} generated stdout. \n{stdout}')
                updated = True
            if stderr:
                Linux.logger.error(f'Backup update command for Backup #{backup_id} generated stderr. \n{stderr}')
        except (OSError, SSHException, TimeoutError) as err:
            error = f'Exception occurred while updating Backup #{backup_id} in {host_ip}.'
            Linux.logger.error(error, exc_info=True)
            backup_data['errors'].append(f'{error} Error: {err}')
            span.set_tag('failed_reason', 'ssh_error')
        finally:
            client.close()
        return updated

    @staticmethod
    def _get_template_data(backup_data: Dict[str, Any], span: Span) -> Optional[Dict[str, Any]]:
        """
        Given the backup data from the API, create a dictionary that contains all of the necessary keys
        for the template
        The keys will be checked in the update method and not here, this method is only concerned with fetching the data
        that it can.
        :param backup_data: The data of the Backup read from the API
        :param span: The tracing span in use for this task. In this method, just pass it to API calls.
        :returns: The data needed for the templates to update a Linux Backup
        """
        backup_id = backup_data['id']
        Linux.logger.debug(f'Compiling template data for Backup #{backup_id}')
        data: Dict[str, Any] = {key: None for key in Linux.template_keys}

        data['host_sudo_passwd'] = settings.NETWORK_PASSWORD
        data['backup_identifier'] = f'{backup_data["vm"]["id"]}_{backup_data["id"]}'
        data['vm_identifier'] = f'{backup_data["vm"]["project"]["id"]}_{backup_data["vm"]["id"]}'

        # Get the ip address of the host
        host_ip = None
        for interface in backup_data['server_data']['interfaces']:
            if interface['enabled'] is True and interface['ip_address'] is not None:
                if IPAddress(str(interface['ip_address'])).version == 6:
                    host_ip = interface['ip_address']
                    break
        if host_ip is None:
            error = f'Host ip address not found for the server # {backup_data["vm"]["server_id"]}'
            Linux.logger.error(error)
            backup_data['errors'].append(error)
            return None
        data['host_ip'] = host_ip
        return data
