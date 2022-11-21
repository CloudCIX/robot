"""
builder class for windows vms

- gathers template data
- generates necessary files
- connects to the vm's server and deploys the vm to it

"""
# stdlib
import logging
import os
import random
import shutil
import string
from typing import Any, Dict, Optional
# lib
import opentracing
from jaeger_client import Span
from netaddr import IPAddress
from winrm.exceptions import WinRMError
# local
import settings
import utils
from mixins import VMImageMixin, WindowsMixin


__all__ = [
    'Windows',
]


class Windows(WindowsMixin, VMImageMixin):
    """
    Class that handles the building of the specified VM
    When we get to this point, we can be sure that the VM is a windows VM
    """
    # Keep a logger for logging messages from this class
    logger = logging.getLogger('robot.builders.vm.windows')
    # Keep track of the keys necessary for the template, so we can ensure that all keys are present before building
    template_keys = {
        # the admin password for the vm, unencrypted
        'admin_password',
        # the number of cpus in the vm
        'cpu',
        # the default subnet gateway
        'default_gateway',
        # default ip address of the VM
        'default_ips',
        # the default subnet mask in integer form (/24)
        'default_netmask_int',
        # the default vlan that the vm is a part of
        'default_vlan',
        # the dns servers for the vm (in list form, not string form)
        'dns',
        # the DNS hostname for the host machine, as WinRM cannot use IPv6
        'host_name',
        # the answer_files file of the image used to build the VM
        'image_answer_file_name',
        # the name of the image file used to build the vm
        'image_filename',
        # the non default ip addresses of the vm
        'ip_addresses',
        # the language of the vm
        'language',
        # the nas drive url for the region
        'network_drive_url',
        # the amount of RAM in the VM
        'ram',
        # storage type (HDD/SSD)
        'storage_type',
        # storages of the vm
        'storages',
        # the timezone of the vm
        'timezone',
        # all subnet vlans numbers list for bridges
        'vlans',
        # an identifier that uniquely identifies the vm
        'vm_identifier',
        # path for vm's folders files located in host
        'vms_path',
    }

    @staticmethod
    def build(vm_data: Dict[str, Any], span: Span) -> bool:
        """
        Commence the build of a vm using the data read from the API
        :param vm_data: The result of a read request for the specified VM
        :param span: The tracing span in use for this build task
        :return: A flag stating whether or not the build was successful
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
            missing_keys = [f'"{key}"' for key in Windows.template_keys if template_data[key] is None]
            error_msg = f'Template Data Error, the following keys were missing from the VM build data: ' \
                        f'{", ".join(missing_keys)}'
            Windows.logger.error(error_msg)
            vm_data['errors'].append(error_msg)
            span.set_tag('failed_reason', 'template_data_keys_missing')
            return False

        # If everything is okay, commence building the VM
        host_name = template_data.pop('host_name')

        # Write necessary files into the network drive
        network_drive_path = settings.HYPERV_ROBOT_NETWORK_DRIVE_PATH
        path = f'{network_drive_path}/VMs/{vm_data["project"]["id"]}_{vm_id}'
        child_span = opentracing.tracer.start_span('write_files_to_network_drive', child_of=span)
        file_write_success = Windows._generate_network_drive_files(vm_data, template_data, path)
        child_span.finish()

        if not file_write_success:
            # The method will log which part failed, so we can just exit
            span.set_tag('failed_reason', 'network_drive_files_failed_to_write')
            return False

        # Render the build command
        child_span = opentracing.tracer.start_span('generate_command', child_of=span)
        cmd = utils.JINJA_ENV.get_template('vm/hyperv/commands/build.j2').render(**template_data)
        child_span.finish()

        # Open a client and run the two necessary commands on the host
        built = False
        try:
            child_span = opentracing.tracer.start_span('build_vm', child_of=span)
            response = Windows.deploy(cmd, host_name, child_span)
            child_span.finish()
            span.set_tag('host', host_name)
        except WinRMError as err:
            error = f'Exception occurred while attempting to build VM #{vm_id} on {host_name}.'
            Windows.logger.error(error, exc_info=True)
            vm_data['errors'].append(f'{error} Error: {err}')
            span.set_tag('failed_reason', 'winrm_error')
        else:
            # Check the stdout and stderr for messages
            if response.std_out:
                msg = response.std_out.strip()
                Windows.logger.debug(f'VM build command for VM #{vm_id} generated stdout\n{msg}')
                built = 'VM Successfully Created' in msg
            # Check if the error was parsed to ensure we're not logging invalid std_err output
            if response.std_err and '#< CLIXML\r\n' not in response.std_err:
                msg = response.std_err.strip()
                error = f'VM build command for VM #{vm_id} generated stderr\n{msg}'
                vm_data['errors'].append(error)
                Windows.logger.error(error)

        # remove all the files created in network drive
        try:
            shutil.rmtree(path)
        except OSError:
            Windows.logger.warning(f'Failed to remove network drive files for VM #{vm_id}')

        return built

    @staticmethod
    def _get_template_data(vm_data: Dict[str, Any], span: Span) -> Optional[Dict[str, Any]]:
        """
        Given the vm data from the API, create a dictionary that contains all of the necessary keys for the template
        The keys will be checked in the build method and not here, this method is only concerned with fetching the data
        that it can.
        :param vm_data: The data of the VM read from the API
        :param span: Span
        :returns: The data needed for the templates to build a Windows VM
        """
        vm_id = vm_data['id']
        Windows.logger.debug(f'Compiling template data for VM #{vm_id}')
        data: Dict[str, Any] = {key: None for key in Windows.template_keys}

        data['vm_identifier'] = f'{vm_data["project"]["id"]}_{vm_id}'
        data['image_answer_file_name'] = vm_data['image']['answer_file_name']

        data['image_filename'] = vm_data['image']['filename']
        # check if file exists at /mnt/images/HyperV/VHDXs/
        path = '/mnt/images/HyperV/VHDXs/'
        child_span = opentracing.tracer.start_span('vm_image_file_download', child_of=span)
        if not Windows.check_image(data['image_filename'], path):
            # download the file
            downloaded, errors = Windows.download_image(data['image_filename'], path)
            if not downloaded:
                for error in errors:
                    Windows.logger.error(error)
                    vm_data['errors'].append(error)
                return None
        child_span.finish()

        # RAM is needed in MB for the builder but we take it in in GB (1024, not 1000)
        data['ram'] = vm_data['ram'] * 1024
        data['cpu'] = vm_data['cpu']
        data['dns'] = vm_data['dns']

        # Generate encrypted passwords
        data['admin_password'] = Windows._password_generator(size=12)
        # Also save the password back to the VM data dict
        vm_data['admin_password'] = data['admin_password']

        # Check for the primary storage
        if not any(storage['primary'] for storage in vm_data['storages']):
            error = 'No primary storage drive found. Expected one primary storage drive'
            Windows.logger.error(error)
            vm_data['errors'].append(error)
            return None

        data['storages'] = vm_data['storages']
        data['storage_type'] = vm_data['storage_type']

        # Get the Networking details
        data['vlans'] = []
        data['ip_addresses'] = []
        data['default_ips'] = []
        data['default_gateway'] = ''
        data['default_netmask_int'] = ''
        data['default_vlan'] = ''

        # The private IPs for the VM will be the one we need to pass to the template
        vm_data['ip_addresses'].reverse()
        ip_addresses = []
        subnets = []
        for ip in vm_data['ip_addresses']:
            if IPAddress(ip['address']).is_private():
                ip_addresses.append(ip)
                subnets.append({
                    'address_range': ip['subnet']['address_range'],
                    'vlan': ip['subnet']['vlan'],
                    'id': ip['subnet']['id'],
                })
        # Removing duplicates
        subnets = [dict(tuple_item) for tuple_item in {tuple(subnet.items()) for subnet in subnets}]
        # sorting nics (each subnet is one nic)
        for subnet in subnets:
            non_default_ips = []
            gateway, netmask_int = subnet['address_range'].split('/')
            vlan = str(subnet['vlan'])
            data['vlans'].append(vlan)

            for ip_address in ip_addresses:
                address = ip_address['address']
                if ip_address['subnet']['id'] == subnet['id']:
                    # Pick the default ips if any
                    if vm_data['gateway_subnet'] is not None:
                        if subnet['id'] == vm_data['gateway_subnet']['id']:
                            data['default_ips'].append(address)
                            data['default_gateway'] = gateway
                            data['default_netmask_int'] = netmask_int
                            data['default_vlan'] = vlan
                            continue
                    # else store the non gateway subnet ips
                    non_default_ips.append(address)

            if len(non_default_ips) > 0:
                data['ip_addresses'].append({
                    'ips': non_default_ips,
                    'gateway': gateway,
                    'netmask_int': netmask_int,
                    'vlan': vlan,
                })

        # Add locale data to the VM
        data['language'] = 'en_IE'
        data['timezone'] = 'GMT Standard Time'

        # Get the host name of the server
        host_name = None
        for interface in vm_data['server_data']['interfaces']:
            if interface['enabled'] is True and interface['ip_address'] is not None:
                if IPAddress(str(interface['ip_address'])).version == 6:
                    host_name = interface['hostname']
                    break
        if host_name is None:
            error = f'Host name is not found for the server # {vm_data["server_id"]}'
            Windows.logger.error(error)
            vm_data['errors'].append(error)
            return None

        # Add the host information to the data
        data['host_name'] = host_name
        data['network_drive_url'] = settings.NETWORK_DRIVE_URL
        data['vms_path'] = settings.HYPERV_VMS_PATH

        return data

    @staticmethod
    def _generate_network_drive_files(vm_data: Dict[str, Any], template_data: Dict[str, Any], path: str) -> bool:
        """
        Generate and write files into the network drive so they are on the host for the build scripts to utilise.
        Writes the following files to the drive;
            - unattend.xml
            - network.xml
            - build.psm1
        :param vm_data: The data of the VM read from the API
        :param path: Network drive location to create above files for VM build
        :param template_data: The retrieved template data for the vm
        :returns: A flag stating whether or not the job was successful
        """
        vm_id = vm_data['id']
        # Create a folder by vm_identifier name at network_drive_path/VMs/
        try:
            os.mkdir(path)
        except FileExistsError:
            pass
        except OSError as err:
            error = f'Failed to create directory for VM #{vm_id} at {path}.'
            Windows.logger.error(error, exc_info=True)
            vm_data['errors'].append(f'{error} Error: {err}')
            return False

        # Render and attempt to write the answer file
        template_name = 'vm/hyperv/answer_files/windows.j2'
        answer_file_data = utils.JINJA_ENV.get_template(template_name).render(**template_data)
        template_data.pop('admin_password')
        answer_file_log = utils.JINJA_ENV.get_template(template_name).render(**template_data)
        Windows.logger.debug(f'Generated answer file for VM #{vm_id}\n{answer_file_log}')
        answer_file_path = f'{path}/unattend.xml'
        try:
            # Attempt to write
            with open(answer_file_path, 'w') as f:
                f.write(answer_file_data)
            Windows.logger.debug(f'Successfully wrote answer file for VM #{vm_id} to {answer_file_log}')
        except IOError as err:
            error = f'Failed to write answer file for VM #{vm_id} to {answer_file_path}.'
            Windows.logger.error(error, exc_info=True)
            vm_data['errors'].append(f'{error} Error: {err}')
            return False

        # Render and attempt to write the network file
        template_name = 'vm/hyperv/commands/network.j2'
        network = utils.JINJA_ENV.get_template(template_name).render(**template_data)
        Windows.logger.debug(f'Generated network file for VM #{vm_id}\n{network}')
        network_file = f'{path}/network.xml'
        try:
            # Attempt to write
            with open(network_file, 'w') as f:
                f.write(network)
            Windows.logger.debug(f'Successfully wrote network file for VM #{vm_id} to {network_file}')
        except IOError as err:
            error = f'Failed to write network file for VM #{vm_id} to {network_file}.'
            Windows.logger.error(error, exc_info=True)
            vm_data['errors'].append(f'{error} Error: {err}')
            return False

        # Render and attempt to write the build script file
        template_name = 'vm/hyperv/commands/script.j2'
        builder = utils.JINJA_ENV.get_template(template_name).render(**template_data)
        Windows.logger.debug(f'Generated build script file for VM #{vm_id}\n{builder}')
        script_file = f'{path}/builder.psm1'
        try:
            # Attempt to write
            with open(script_file, 'w') as f:
                f.write(builder)
            Windows.logger.debug(f'Successfully wrote build script file for VM #{vm_id} to {script_file}')
        except IOError as err:
            error = f'Failed to write build script file for VM #{vm_id} to {script_file}.'
            Windows.logger.error(error, exc_info=True)
            vm_data['errors'].append(f'{error} Error: {err}')
            return False

        # Return True as all was successful
        return True

    @staticmethod
    def _password_generator(size: int = 12, chars: Optional[str] = None) -> str:
        """
        Returns a string of random characters, useful in generating temporary
        passwords for automated password resets.

        :param size: default=12; override to provide smaller/larger passwords
        :param chars: default=A-Za-z0-9; override to provide more/less diversity
        :return: A password of length 'size' generated randomly from 'chars'
        """
        if chars is None:
            chars = string.ascii_letters + string.digits
        return ''.join(random.choice(chars) for _ in range(size))
