"""
scrubber class for linux backups

- gathers template data
- connects to the backup vm's host and runs commands to delete a backup off it
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
    Class that handles the scrubbing of the specified Backup
    When we get to this point, we can be sure the Backup is on a linux host
    """
    logger = logging.getLogger('robot.scrubbers.backup.linux')
    template_keys = {
        # Location of backup export
        'export_path',
        # the ip address of the host that the Backup will be built on
        'host_ip',
        # the sudo password of the host, used to run some commands
        'host_sudo_passwd',
    }

    @staticmethod
    def scrub(backup_data: Dict[str, Any], span: Span) -> bool:
        """
        Commence the scrub of a backup using the data read from the API
        :param backup_data: The result of a read request for the specified Backup
        :param span: The tracing span for the scrub task
        :return: A flag stating whether or not the scrub was successful
        """
        backup_id = backup_data['id']

        # Generate the necessary template data
        child_span = opentracing.tracer.start_span('generate_template_data', child_of=span)
        template_data = Linux._get_template_data(backup_data, child_span)
        child_span.finish()

        # Check that the data was successfully generated
        if template_data is None:
            error = f'Failed to retrieve template data for Backup #{backup_id}.'
            Linux.logger.error(error)
            backup_data['errors'].append(error)
            span.set_tag('failed_reason', 'template_data_failed')
            return False

        # Check that all of the necessary keys are present
        if not all(template_data[key] is not None for key in Linux.template_keys):
            missing_keys = [f'"{key}"' for key in Linux.template_keys if template_data[key] is None]
            error_msg = f'Template Data Error, the following keys were missing from the Backup scrub data: ' \
                        f'{", ".join(missing_keys)}.'
            Linux.logger.error(error_msg)
            span.set_tag('failed_reason', 'template_data_keys_missing')
            return False

        # If everything is okay, commence scrubbing the backup
        host_ip = template_data.pop('host_ip')

        # Generate command to be run on the host
        child_span = opentracing.tracer.start_span('generate_commands', child_of=span)
        cmd = Linux._generate_host_commands(backup_id, template_data)
        child_span.finish()

        # Open a client and run the command
        scrubbed = False
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

            # Now attempt to execute the backup scrub command
            Linux.logger.debug(f'Executing scrub command for Backup #{backup_id}')
            child_span = opentracing.tracer.start_span('scrub_backup', child_of=span)
            stdout, stderr = Linux.deploy(cmd, client, child_span)
            child_span.finish()

            if stdout:
                Linux.logger.debug(f'Backup scrub command for Backup #{backup_id} generated stdout\n{stdout}.')
                if 'removed' in stdout:
                    scrubbed = True

            if stderr:
                Linux.logger.error(f'Backup scrub command for Backup #{backup_id} generated stderr\n{stderr}')

        except (OSError, SSHException, TimeoutError) as err:
            error = f'Exception occured while scrubbing Backup #{backup_id} in {host_ip}.'
            Linux.logger.error(error, exc_info=True)
            backup_data['errors'].append(f'{error} Error: {err}')
            span.set_tag('failed_reason', 'ssh_error')
        finally:
            client.close()
        return scrubbed

    @staticmethod
    def _get_template_data(backup_data: Dict[str, Any], span: Span) -> Optional[Dict[str, Any]]:
        """
        Given the Backup data from the API, create a dictionary that contains all
        of the necessary keys for the template
        The keys will be checked in the scrub method and not here, this method is only
        concerned with fetching the data that it can.
        :param backup_data: The data of the Backup read from the API
        :param span: The tracing span in use for this task. In this method, just pass it to API calls.
        :returns: The data needed for the templates to scrub a Backup
        """
        backup_id = backup_data['id']
        vm_id = backup_data['vm']['id']
        backup_identifier = f'{vm_id}_{backup_id}'

        Linux.logger.debug(f'Compiling template data for backup #{backup_id}.')
        data: Dict[str, Any] = {key: None for key in Linux.template_keys}

        data['host_sudo_passwd'] = settings.NETWORK_PASSWORD
        if str(backup_data['repository']) == '1':
            export_path = f'{settings.KVM_PRIMARY_BACKUP_STORAGE_PATH}{backup_identifier}'
        elif str(backup_data['repository']) == '2':
            export_path = f'{settings.KVM_SECONDARY_BACKUP_STORAGE_PATH}{backup_identifier}'
        else:
            error = f'Repository # {backup_data["repository"]} ' \
                    f'not available on the server # {backup_data["vm"]["server_id"]}'
            Linux.logger.error(error)
            backup_data['errors'].append(error)
            return None
        data['export_path'] = export_path

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

    @staticmethod
    def _generate_host_commands(backup_id: int, template_data: Dict[str, Any]) -> str:
        """
        Generate the commands that need to be run on the host machine to scrub the infrastructure
        Generates the following commands:
            - Command to scrub the backup
        :param backup_id: The id of the backup being built. Used for log messages
        :param template_data: The retrieved template data for the backup
        :returns: A flag stating whether or not the job was successful
        """
        cmd = utils.JINJA_ENV.get_template('backup/kvm/commands/scrub.j2').render(**template_data)
        Linux.logger.debug(f'Generated backup scrub command for Backup #{backup_id}\n{cmd}')

        return cmd
