"""
updater class for windows vms

- gathers template data
- generates necessary files
- connects to the vm's server and deploys the vm to it
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
import settings
import utils
from mixins import VMUpdateMixin, WindowsMixin


__all__ = [
    'Windows',
]


class Windows(WindowsMixin, VMUpdateMixin):
    """
    Class that handles the updating of the specified VM
    When we get to this point, we can be sure that the VM is a windows VM
    """
    # Keep a logger for logging messages from this class
    logger = logging.getLogger('robot.updaters.vm.windows')
    # Keep track of the keys necessary for the template, so we can ensure that all keys are present before updating
    template_keys = {
        # changes of vm
        'changes',
        # the DNS hostname for the host machine, as WinRM cannot use IPv6
        'host_name',
        # a flag stating whether or not the VM should be turned back on after updating it
        'restart',
        # storage type (HDD/SSD)
        'storage_type',
        # an identifier that uniquely identifies the vm
        'vm_identifier',
        # path for vm's folders files located in host
        'vms_path',
    }

    @staticmethod
    def update(vm_data: Dict[str, Any], span: Span) -> bool:
        """
        Commence the update of a vm using the data read from the API
        :param vm_data: The result of a read request for the specified VM
        :param span: The tracing span in use for this update task
        :return: A flag stating whether or not the update was successful
        """
        vm_id = vm_data['id']

        # Generate the necessary template data
        child_span = opentracing.tracer.start_span('generate_template_data', child_of=span)
        template_data = Windows._get_template_data(vm_data, child_span)
        child_span.finish()

        # Check that the data was successfully generated
        if template_data is None:
            error = f'Failed to retrieve template data for VM #{vm_id}.'
            Windows.logger.error(error)
            vm_data['errors'].append(error)
            span.set_tag('failed_reason', 'template_data_failed')
            return False

        # Check that all of the necessary keys are present
        if not all(template_data[key] is not None for key in Windows.template_keys):
            missing_keys = [
                f'"{key}"' for key in Windows.template_keys if template_data[key] is None
            ]
            error_msg = f'Template Data Error, the following keys were missing from the VM update data: ' \
                        f'{", ".join(missing_keys)}.'
            Windows.logger.error(error_msg)
            vm_data['errors'].append(error_msg)
            span.set_tag('failed_reason', 'template_data_keys_missing')
            return False

        # If everything is okay, commence updating the VM
        host_name = template_data.pop('host_name')

        # Render the update command
        child_span = opentracing.tracer.start_span('generate_command', child_of=span)
        cmd = utils.JINJA_ENV.get_template('vm/hyperv/commands/update.j2').render(**template_data)
        child_span.finish()

        # Open a client and run the two necessary commands on the host
        updated = False
        try:
            child_span = opentracing.tracer.start_span('update_vm', child_of=span)
            response = Windows.deploy(cmd, host_name, child_span)
            span.set_tag('host', host_name)
            child_span.finish()
        except WinRMError as err:
            error = f'Exception occurred while attempting to update VM #{vm_id} on {host_name}.'
            Windows.logger.error(error, exc_info=True)
            vm_data['errors'].append(f'{error} Error: {err}')
            span.set_tag('failed_reason', 'winrm_error')
        else:
            # Check the stdout and stderr for messages
            if response.std_out:
                msg = response.std_out.strip()
                Windows.logger.debug(f'VM update command for VM #{vm_id} generated stdout\n{msg}')
                updated = 'VM Successfully Updated' in msg
            # Check if the error was parsed to ensure we're not logging invalid std_err output
            if response.std_err and '#< CLIXML\r\n' not in response.std_err:
                msg = response.std_err.strip()
                Windows.logger.error(f'VM update command for VM #{vm_id} generated stderr\n{msg}')

            # Check if we need to restart the VM as well
            if template_data['restart']:
                # Also render and deploy the restart_cmd template
                restart_cmd = utils.JINJA_ENV.get_template('vm/hyperv/commands/restart.j2').render(**template_data)

                # Attempt to execute the restart command
                Windows.logger.debug(f'Executing restart command for VM #{vm_id}')
                child_span = opentracing.tracer.start_span('restart_vm', child_of=span)
                response = Windows.deploy(restart_cmd, host_name, child_span)
                child_span.finish()

                if response.std_out:
                    msg = response.std_out.strip()
                    Windows.logger.debug(f'VM restart command for VM #{vm_id} generated stdout\n{msg}')
                # Check if the error was parsed to ensure we're not logging invalid std_err output
                if response.std_err and '#< CLIXML\r\n' not in response.std_err:
                    msg = response.std_err.strip()
                    error = f'VM restart command for VM #{vm_id} generated stderr\n{msg}'
                    vm_data['errors'].append(error)
                    Windows.logger.error(error)
        return updated

    @staticmethod
    def _get_template_data(vm_data: Dict[str, Any], span: Span) -> Optional[Dict[str, Any]]:
        """
        Given the vm data from the API, create a dictionary that contains all of the necessary keys for the template
        The keys will be checked in the update method and not here, this method is only concerned with fetching the data
        that it can.
        :param vm_data: The data of the VM read from the API
        :param span: The tracing span in use for this task. In this method just pass it to API calls
        :returns: The data needed for the templates to update a Windows VM
        """
        vm_id = vm_data['id']
        Windows.logger.debug(f'Compiling template data for VM #{vm_id}')
        data: Dict[str, Any] = {key: None for key in Windows.template_keys}

        data['vm_identifier'] = f'{vm_data["project"]["id"]}_{vm_id}'
        # changes
        changes: Dict[str, Any] = {
            'ram': False,
            'cpu': False,
            'storages': False,
        }
        updates = vm_data['history'][0]
        try:
            if updates['ram_quantity'] is not None:
                # RAM is needed in MB for the updater but we take it in in GB (1024, not 1000)
                changes['ram'] = vm_data['ram'] * 1024
        except KeyError:
            pass
        try:
            if updates['cpu_quantity'] is not None:
                changes['cpu'] = vm_data['cpu']
        except KeyError:
            pass
        # Fetch the drive information for the update
        try:
            if len(updates['storage_histories']) != 0:
                Windows.logger.debug(f'Fetching drives for VM #{vm_id}')
                child_span = opentracing.tracer.start_span('fetch_drive_updates', child_of=span)
                changes['storages'] = Windows.fetch_drive_updates(vm_data)
                child_span.finish()
        except KeyError:
            pass
        # Add changes to data
        data['changes'] = changes
        data['storage_type'] = vm_data['storage_type']
        data['vms_path'] = settings.HYPERV_VMS_PATH

        # Get the host name of the server
        host_name = None
        for interface in vm_data['server_data']['interfaces']:
            if interface['enabled'] is True and interface['ip_address'] is not None:
                if IPAddress(str(interface['ip_address'])).version == 6:
                    host_name = interface['hostname']
                    break
        if host_name is None:
            error = f'Host ip address not found for the server # {vm_data["server_id"]}.'
            Windows.logger.error(error)
            vm_data['errors'].append(error)
            return None
        # Add the host information to the data
        data['host_name'] = host_name
        # Determine restart
        data['restart'] = vm_data['restart']
        return data
