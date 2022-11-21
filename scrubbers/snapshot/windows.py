"""
scrubber class for windows snapshots

- gathers template data
- connects to the snapshot vm's host and runs commands to delete a snapshot off it
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
    Class that handles the scrubbing of the specified Snapshot
    When we get to this point, we can be sure that the Snapshot is on a Windows host
    """
    logger = logging.getLogger('robot.scrubbers.snapshot.windows')
    template_keys = {
        # the DNS hostname for the host machine, as WinRM cannot use IPv6
        'host_name',
        # Should the scrubber remove the children of this snapshot?
        'remove_subtree',
        # An identifier that uniquely identifies the snapshot
        'snapshot_identifier',
        # an identifier that uniquely identifies the vm
        'vm_identifier',
    }

    @staticmethod
    def scrub(snapshot_data: Dict[str, Any], span: Span) -> bool:
        """
        Commence the scrub of a snapshot using the data read from the API
        :param snapshot_data: The result of a read request for the specified Snapshot
        :param span: The tracing span in use for this task
        :return: A flag stating whether or not the scrub was successful
        """
        snapshot_id = snapshot_data['id']

        # Generate the necessary template data
        child_span = opentracing.tracer.start_span('generate_template_data', child_of=span)
        template_data = Windows._get_template_data(snapshot_data)
        child_span.finish()

        # Check that the data was successfully generated
        if template_data is None:
            error = f'Failed to retrieve template data for Snapshot #{snapshot_id}.'
            Windows.logger.error(error)
            snapshot_data['errors'].append(error)
            span.set_tag('failed_reason', 'template_data_failed')
            return False

        # Check that all of the necessary keys are present
        if not all(template_data[key] is not None for key in Windows.template_keys):
            missing_keys = [f'"{key}"' for key in Windows.template_keys if template_data[key] is None]
            error_msg = f'Template Data Error, the following keys were missing from the Snapshot scrub data: ' \
                        f'{", ".join(missing_keys)}.'
            Windows.logger.error(error_msg)
            snapshot_data['errors'].append(error_msg)
            span.set_tag('failed_reason', 'template_data_keys_missing')
            return False

        # If everything is okay, commence scrubbing the Snapshot
        host_name = template_data.pop('host_name')

        # Render the scrub command
        child_span = opentracing.tracer.start_span('generate_command', child_of=span)
        cmd = utils.JINJA_ENV.get_template('snapshot/hyperv/commands/scrub.j2').render(**template_data)
        child_span.finish()

        # Open a client and run the command on the host
        scrubbed = False
        try:
            child_span = opentracing.tracer.start_span('scrub_snapshot', child_of=span)
            response = Windows.deploy(cmd, host_name, child_span)
            span.set_tag('host', host_name)
            child_span.finish()
        except WinRMError as err:
            error = f'Exception occurred while attempting to scrub Snapshot #{snapshot_id} on {host_name}.'
            Windows.logger.error(error, exc_info=True)
            snapshot_data['errors'].append(f'{error} Error: {err}')
            span.set_tag('failed_reason', 'winrm_error')
        else:
            # Check the stdout and stderr for messages
            if response.std_out:
                msg = response.std_out.strip()
                Windows.logger.debug(f'Snapshot scrub command for Snapshot #{snapshot_id} generated stdout\n{msg}')
                scrubbed = True
            # Check if the error was parsed to ensure we're not logging invalid std_err output
            if response.std_err and '#< CLIXML\r\n' not in response.std_err:
                msg = response.std_err.strip()
                error = f'Snapshot scrub command for Snapshot #{snapshot_id} generated stderr\n{msg}'
                snapshot_data['errors'].append(error)
                Windows.logger.error(error)
        return scrubbed

    @staticmethod
    def _get_template_data(snapshot_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Given the snapshot data from the API, create a dictionary that contains
        all of the necessary keys for the template
        The keys will be checked in the scrub method and not here, this method is only concerned with fetching the data
        that it can.
        :param snapshot_data: The data of the Snapshot read from the API
        :returns: The data needed for the templates to scrub a Windows Snapshot
        """
        snapshot_id = snapshot_data['id']
        Windows.logger.debug(f'Compiling template data for snapshot #{snapshot_id}.')
        data: Dict[str, Any] = {key: None for key in Windows.template_keys}

        data['remove_subtree'] = snapshot_data['remove_subtree']
        data['snapshot_identifier'] = f'{snapshot_data["vm"]["id"]}_{snapshot_data["id"]}'
        data['vm_identifier'] = f'{snapshot_data["vm"]["project"]["id"]}_{snapshot_data["vm"]["id"]}'

        # Get the host name of the server
        host_name = None
        for interface in snapshot_data['server_data']['interfaces']:
            if interface['enabled'] is True and interface['ip_address'] is not None:
                if IPAddress(str(interface['ip_address'])).version == 6:
                    host_name = interface['hostname']
                    break
        if host_name is None:
            error = f'Host ip address not found for the server # {snapshot_data["server_id"]}.'
            Windows.logger.error(error)
            snapshot_data['errors'].append(error)
            return None
        data['host_name'] = host_name
        return data
