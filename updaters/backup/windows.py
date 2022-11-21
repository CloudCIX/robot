"""
updater class for windows backups

- gathers template data
- connects to the backup vm's server and updates the backup
"""
# stdlib
import logging
from typing import Any, Dict, Optional
# lib
import opentracing
from jaeger_client import Span
from netaddr import IPAddress
from winrm.exceptions import WinRMError
# local
import utils
from mixins import WindowsMixin


__all__ = [
    'Windows',
]


class Windows(WindowsMixin):
    """
    Class that handles the updating of the specified Backup
    When we get to this point, we can be sure that the Backup is on a windows host
    """
    logger = logging.getLogger('robot.updaters.backup.windows')
    template_keys = {
        # the DNS hostname for the host machine, as WinRM cannot use IPv6
        'host_name',
        # An identifier that uniquely identifies the backup
        'backup_identifier',
        # an identifier that uniquely identifies the vm
        'vm_identifier',
    }

    @staticmethod
    def update(backup_data: Dict[str, Any], span: Span) -> bool:
        """
        Commence the update of a backup using the data read from the API
        :param backup_data: The result of a read request for the specified Backup
        :param span: The tracing span in use for this update task
        :return: A flag stating whether or not the update was successful
        """
        backup_id = backup_data['id']

        # Generate the necessary template data
        child_span = opentracing.tracer.start_span('generate_template_data', child_of=span)
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
            missing_keys = [
                f'"{key}"' for key in Windows.template_keys if template_data[key] is None
            ]
            error_msg = f'Template Data Error, the following keys were missing from the Backup update data: ' \
                        f'{", ".join(missing_keys)}.'
            Windows.logger.error(error_msg)
            backup_data['errors'].append(error_msg)
            span.set_tag('failed_reason', 'template_data_keys_missing')
            return False

        # If everything is okay, commence updating the backup
        host_name = template_data.pop('host_name')

        # Render the update command
        child_span = opentracing.tracer.start_span('generate_command', child_of=span)
        cmd = utils.JINJA_ENV.get_template('backup/hyperv/commands/update.j2').render(**template_data)
        child_span.finish()

        # Open a client and run the two necessary commands on the host
        updated = False
        try:
            child_span = opentracing.tracer.start_span('update_backup', child_of=span)
            response = Windows.deploy(cmd, host_name, child_span)
            span.set_tag('host', host_name)
            child_span.finish()
        except WinRMError as err:
            error = f'Exception occurred while attempting to update Backup #{backup_id} on {host_name}.'
            Windows.logger.error(error, exc_info=True)
            backup_data['errors'].append(f'{error} Error: {err}')
            span.set_tag('failed_reason', 'winrm_error')
        else:
            # Check the stdout and stderr for messages
            if response.std_out:
                msg = response.std_out.strip()
                Windows.logger.debug(f'Backup update command for Backup #{backup_id} generated stdout\n{msg}')
                updated = True
            # Check if the error was parsed to ensure we're not logging invalid std_err output
            if response.std_err and '#< CLIXML\r\n' not in response.std_err:
                msg = response.std_err.strip()
                Windows.logger.error(f'Backup update command for Backup #{backup_id} generated stderr\n{msg}')
        return updated

    @staticmethod
    def _get_template_data(backup_data: Dict[str, Any], span: Span) -> Optional[Dict[str, Any]]:
        """
        Given the backup data from the API, create a dictionary that contains all
        of the necessary keys for the template
        The keys will be checked in the build method and not here, this method is only concerned with fetching the data
        that it can.
        :param backup_data: The data of the Backup read from the API
        :returns: The data needed for the templates to build a Windows Backup
        """
        backup_id = backup_data['id']
        Windows.logger.debug(f'Compiling template data for backup #{backup_id}.')
        data: Dict[str, Any] = {key: None for key in Windows.template_keys}

        data['backup_identifier'] = f'{backup_data["vm"]["id"]}_{backup_data["id"]}'
        data['vm_identifier'] = f'{backup_data["vm"]["project"]["id"]}_{backup_data["vm"]["id"]}'

        # Get the host name of the server
        host_name = None
        for interface in backup_data['server_data']['interfaces']:
            if interface['enabled'] is True and interface['ip_address'] is not None:
                if IPAddress(str(interface['ip_address'])).version == 6:
                    host_name = interface['hostname']
                    break
        if host_name is None:
            error = f'Host ip address not found for the server # {backup_data["server_id"]}.'
            Windows.logger.error(error)
            backup_data['errors'].append(error)
            return None
        # Add the host information to the data
        data['host_name'] = host_name
        return data
