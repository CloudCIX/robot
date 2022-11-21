"""
builder class for kvm backup

- gathers template data
- connects to the backup vm's server and builds the backup of the vm on the server
"""
# stdlib
import logging
import socket
from datetime import datetime
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
    Class that handles the building of the specified Backup
    When we get to this point, we can be sure that the Backup is on a linux host
    """
    logger = logging.getLogger('robot.builders.backup.linux')
    template_keys = {
        # backup location on the Host
        'export_path',
        # the ip address of the host that the Backup will be built in
        'host_ip',
        # the sudo password of the host, used to run some commands
        'host_sudo_passwd',
        # an identifier that uniquely identifies the vm
        'vm_identifier',
    }

    @staticmethod
    def build(backup_data: Dict[str, Any], span: Span) -> bool:
        """
        Commence the build of a backup using the data read from the API
        :param backup_data: The result of a read request to specified backup
        :param span: The tracing span in use for this build task
        :return: A flag stating whether or not the build backup was successful
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
            span.set_tag('failed_reason', 'template_data_failed')
            return False

        # Check that all of the necessary keys are present
        if not all(template_data[key] is not None for key in Linux.template_keys):
            missing_keys = [f'"{key}"' for key in Linux.template_keys if template_data[key] is None]
            error_msg = f'Template Data Error, the following keys were missing from the Backup build data: ' \
                        f'{", ".join(missing_keys)}'
            Linux.logger.error(error_msg)
            backup_data['errors'].append(error_msg)
            span.set_tag('failed_reason', 'template_data_keys_missing')
            return False

        # If everything is okay, commence building the backup
        host_ip = template_data.pop('host_ip')

        # Generate the command that will be run on the host machine directly
        child_span = opentracing.tracer.start_span('generate_commands', child_of=span)
        backup_cmd = Linux._generate_host_commands(backup_id, template_data)
        child_span.finish()

        # Open a client and run the two necessary commands on the host
        built = False
        client = SSHClient()
        client.set_missing_host_key_policy(AutoAddPolicy)
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

            # Attempt to execute the backup build commands
            Linux.logger.debug(f'Executing backup build commands for Backup # {backup_id}')

            # time_valid field
            backup_data['time_valid'] = datetime.utcnow().isoformat().replace('T', ' ').split('.')[0]

            child_span = opentracing.tracer.start_span('build_backup', child_of=span)
            stdout, stderr = Linux.deploy(backup_cmd, client, child_span)
            child_span.finish()

            if stdout:
                Linux.logger.debug(f'Backup build command for Backup {backup_id} generated stdout. \n{stdout}')
            if stderr:
                Linux.logger.error(f'Backup build command for Backup {backup_id} generated stderr. \n{stderr}')
                backup_data['errors'].append(stderr)
            built = f'Backup done {template_data["vm_identifier"]}' in stdout
        except (OSError, SSHException, TimeoutError):
            error = f'Exception occured while building Backup #{backup_id} in {host_ip}'
            Linux.logger.error(error, exc_info=True)
            backup_data['errors'].append(error)
            span.set_tag('failed_reason', 'ssh_error')
        finally:
            client.close()

        return built

    @staticmethod
    def _get_template_data(backup_data: Dict[str, Any], span: Span) -> Optional[Dict[str, Any]]:
        """
        Given the backup data from the API, create a dictionary that contains
        all of the necessary keys for the template
        The keys will be checked in the build method and not here, this method
        is only concerned with checking the data it can.
        :param backup_data: The data of the Backup read from the API
        :param span: Span
        :returns: The data needed for the template to build a new backup
        """
        backup_id = backup_data['id']
        vm_id = backup_data['vm']['id']
        backup_identifier = f'{vm_id}_{backup_id}'

        Linux.logger.debug(f'Compiling template data for backup #{backup_id}.')
        data: Dict[str, Any] = {key: None for key in Linux.template_keys}

        data['host_sudo_passwd'] = settings.NETWORK_PASSWORD
        data['vm_identifier'] = f'{backup_data["vm"]["project"]["id"]}_{vm_id}'
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
        Generate the command that need to be run on the host machine to build the infrastructure
        Generates the following command:
            - command to build the backup
        :param backup_id: The id of the Backup being built. Used for log messages
        :param template_data: The retrieved template data for the Backup
        :returns: A flag stating whether or not the job was successful
        """
        # Render the backup command
        backup_cmd = utils.JINJA_ENV.get_template('backup/kvm/commands/build.j2').render(**template_data)
        Linux.logger.debug(f'Generated backup build command for Backup #{backup_id}\n{backup_cmd}')

        return backup_cmd
