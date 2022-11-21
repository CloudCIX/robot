"""
builder class for windows backups

- gathers template data
- connects to the backup vm's server and builds the backup of the vm

"""
# stdlib
import logging
from datetime import datetime
from typing import Any, Dict, Optional
# lib
import opentracing
from jaeger_client import Span
from netaddr import IPAddress
from winrm.exceptions import WinRMError
# local
import utils
from mixins import WindowsMixin
import settings


__all__ = [
    'Windows',
]


class Windows(WindowsMixin):
    """
    Class that handles the building of the specified Backup`
    When we get to this point, we can be sure that the Backup
    is on a windows host
    """
    logger = logging.getLogger('robot.builders.backup.windows')
    template_keys = {
        # backup location on the Host
        'export_path',
        # the DNS hostname for the host machine, as WinRM cannot use IPv6
        'host_name',
        # an identifier that uniquely identifies the vm
        'vm_identifier',
    }

    @staticmethod
    def build(backup_data: Dict[str, Any], span: Span) -> bool:
        """
        Commence the build of a backup using the data read from the API
        :param backup_data: The result of a read request for the specified Backup
        :param span: The tracing span in use for this build task
        :return: A flag stating whether or not the build was successful
        """
        backup_id = backup_data['id']

        # Generate the necessary template data
        child_span = opentracing.tracer.start_span('generating_template_data', child_of=span)
        template_data = Windows._get_template_data(backup_data, child_span)
        child_span.finish()

        # Check that the data was successfully generated
        if template_data is None:
            error = f'Failed to retrieve template data for Backup #{backup_id}.'
            Windows.logger.error(error)
            backup_data['errors'].append(error)
            span.set_tag('failed_reason', 'template_data_failed')
            return False

        # Check that all of the necessary keys are present
        if not all(template_data[key] is not None for key in Windows.template_keys):
            missing_keys = [f'"{key}"' for key in Windows.template_keys if template_data[key] is None]
            error_msg = f'Template Data Error, the following keys were missing from the Backup build data:' \
                        f' {", ".join(missing_keys)}'
            Windows.logger.error(error_msg)
            backup_data['errors'].append(error_msg)
            span.set_tag('failed_reason', 'template_data_keys_missing')
            return False

        # If everything is okay, commence building the Backup
        host_name = template_data.pop('host_name')

        # Render the build command
        # Generate the two commands that will be run on the host machine directly
        child_span = opentracing.tracer.start_span('generate_commands', child_of=span)
        backup_build_cmd = Windows._generate_host_commands(backup_id, template_data)
        child_span.finish()

        # Open a client and run the two necessary commands on the host
        built = False
        try:
            # time_valid field
            backup_data['time_valid'] = datetime.utcnow().isoformat().replace('T', ' ').split('.')[0]
            child_span = opentracing.tracer.start_span('build_backup', child_of=span)
            response = Windows.deploy(backup_build_cmd, host_name, child_span)
            child_span.finish()
            span.set_tag('host', host_name)
        except WinRMError as err:
            error = f'Exception occurred while attempting to build Backup #{backup_id} on {host_name}.'
            Windows.logger.error(error, exc_info=True)
            backup_data['errors'].append(f'{error} Error: {err}')
            span.set_tag('failed_reason', 'winrm_error')
        else:
            # Check the stdout and stderr for messages
            if response.std_out:
                msg = response.std_out.strip()
                Windows.logger.debug(f'Backup build command for Backup #{backup_id} generated stdout\n{msg}')
                built = 'Created VM backup' in msg
            # Check if the error was parsed to ensure we're not logging invalid std_err output
            if response.std_err and '#< CLIXML\r\n' not in response.std_err:
                msg = response.std_err.strip()
                error = f'Backup build command for Backup #{backup_id} generated stderr\n{msg}'
                backup_data['errors'].append(error)
                Windows.logger.error(error)

        return built

    @staticmethod
    def _get_template_data(backup_data: Dict[str, Any], span: Span) -> Optional[Dict[str, Any]]:
        """
        Given the backup data from the API, create a dictionary that contains all of the
        necessary keys for the template
        The keys will be checked in the build method and not here, this method is only concerned with fetching the data
        that it can.
        :param backup_data: The data of the Backup read from the API
        :param span: Span
        :returns: The data needed for the templates to build a Windows Backup
        """
        backup_id = backup_data['id']
        vm_id = backup_data['vm']['id']
        backup_identifier = f'{vm_id}_{backup_id}'

        Windows.logger.debug(f'Compiling template data for Backup #{backup_id}')
        data: Dict[str, Any] = {key: None for key in Windows.template_keys}

        data['vm_identifier'] = f'{backup_data["vm"]["project"]["id"]}_{vm_id}'
        # export path
        if str(backup_data['repository']) == '1':
            export_path = f'{settings.HYPERV_PRIMARY_BACKUP_STORAGE_PATH}{backup_identifier}\\'
        elif str(backup_data['repository']) == '2':
            export_path = f'{settings.HYPERV_SECONDARY_BACKUP_STORAGE_PATH}{backup_identifier}\\'
        else:
            error = f'Repository # {backup_data["repository"]} ' \
                    f'not available on the server # {backup_data["vm"]["server_id"]}'
            Windows.logger.error(error)
            backup_data['errors'].append(error)
            return None
        data['export_path'] = export_path

        # Get the host name of the server
        host_name = None
        for interface in backup_data['server_data']['interfaces']:
            if interface['enabled'] is True and interface['ip_address'] is not None:
                if IPAddress(str(interface['ip_address'])).version == 6:
                    host_name = interface['hostname']
                    break
        if host_name is None:
            error = f'Host name is not found for the server # {backup_data["server_id"]}'
            Windows.logger.error(error)
            backup_data['errors'].append(error)
            return None

        # Add the host information to the data
        data['host_name'] = host_name
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
        backup_cmd = utils.JINJA_ENV.get_template('backup/hyperv/commands/build.j2').render(**template_data)
        Windows.logger.debug(f'Generated backup build command for Backup #{backup_id}\n{backup_cmd}')

        return backup_cmd
