"""
builder class for kvm snapshot

- gathers template data
- connects to the snapshot vm's server and builds the snapshot of the vm on the server
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
    Class that handles the building of the specified Snapshot
    When we get to this point, we can be sure that the Snapshot is on a linux host
    """
    logger = logging.getLogger('robot.builders.snapshot.linux')
    template_keys = {
        # the ip address of the host that the Snapshot will be built in
        'host_ip',
        # the sudo password of the host, used to run some commands
        'host_sudo_passwd',
        # An identifier that uniquely identifies the snapshot
        'snapshot_identifier',
        # an identifier that uniquely identifies the vm
        'vm_identifier',
    }

    @staticmethod
    def build(snapshot_data: Dict[str, Any], span: Span) -> bool:
        """
        Commence the build of a snapshot using the data read from the API
        :param snapshot_data: The result of a read request to specified snapshot
        :param span: The tracing span in use for this build task
        :return: A flag stating whether or not the build snapshot was successful
        """
        snapshot_id = snapshot_data['id']

        # Generate the necessary template data
        child_span = opentracing.tracer.start_span('generate_template_data', child_of=span)
        template_data = Linux._get_template_data(snapshot_data, child_span)
        child_span.finish()

        # Check that the data was successfully generated
        if template_data is None:
            error = f'Failed to retrieve template data for Snapshot #{snapshot_id}.'
            Linux.logger.error(error)
            span.set_tag('failed_reason', 'template_data_failed')
            return False

        # Check that all of the necessary keys are present
        if not all(template_data[key] is not None for key in Linux.template_keys):
            missing_keys = [f'"{key}"' for key in Linux.template_keys if template_data[key] is None]
            error_msg = f'Template Data Error, the following keys were missing from the Snapshot build data: ' \
                        f'{", ".join(missing_keys)}'
            Linux.logger.error(error_msg)
            snapshot_data['errors'].append(error_msg)
            span.set_tag('failed_reason', 'template_data_keys_missing')
            return False

        # If everything is okay, commence building the snapshot
        host_ip = template_data.pop('host_ip')

        # Generate the command that will be run on the host machine directly
        child_span = opentracing.tracer.start_span('generate_commands', child_of=span)
        snapshot_cmd = Linux._generate_host_commands(snapshot_id, template_data)
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

            # Attempt to execute the snapshot build commands
            Linux.logger.debug(f'Executing snapshot build commands for Snapshot # {snapshot_id}')

            child_span = opentracing.tracer.start_span('build_snapshot', child_of=span)
            stdout, stderr = Linux.deploy(snapshot_cmd, client, child_span)
            child_span.finish()

            if stdout:
                Linux.logger.debug(f'Snapshot build command for Snapshot {snapshot_id} generated stdout. \n{stdout}')
            if stderr:
                Linux.logger.error(f'Snapshot build command for Snapshot {snapshot_id} generated stderr. \n{stderr}')
                snapshot_data['errors'].append(stderr)
            built = 'created' in stdout
        except (OSError, SSHException, TimeoutError):
            error = f'Exception occured while building Snapshot #{snapshot_id} in {host_ip}'
            Linux.logger.error(error, exc_info=True)
            snapshot_data['errors'].append(error)
            span.set_tag('failed_reason', 'ssh_error')
        finally:
            client.close()

        return built

    @staticmethod
    def _get_template_data(snapshot_data: Dict[str, Any], span: Span) -> Optional[Dict[str, Any]]:
        """
        Given the snapshot data from the API, create a dictionary that contains
        all of the necessary keys for the template
        The keys will be checked in the build method and not here, this method
        is only concerned with checking the data it can.
        :param snapshot_data: The data of the Snapshot read from the API
        :param span: Span
        :returns: The data needed for the template to build a new snapshot
        """
        snapshot_id = snapshot_data['id']
        Linux.logger.debug(f'Compiling template data for snapshot #{snapshot_id}.')
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

    @staticmethod
    def _generate_host_commands(snapshot_id: int, template_data: Dict[str, Any]) -> str:
        """
        Generate the command that need to be run on the host machine to build the infrastructure
        Generates the following command:
            - command to build the snapshot
        :param snapshot_id: The id of the Snapshot being built. Used for log messages
        :param template_data: The retrieved template data for the Snapshot
        :returns: A flag stating whether or not the job was successful
        """
        # Render the snapshot command
        snapshot_cmd = utils.JINJA_ENV.get_template('snapshot/kvm/commands/build.j2').render(**template_data)
        Linux.logger.debug(f'Generated snapshot build command for Snapshot #{snapshot_id}')

        return snapshot_cmd
